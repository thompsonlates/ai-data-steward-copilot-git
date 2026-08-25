from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any
import re

import requests
import snowflake.connector
from fastapi import HTTPException
from google.cloud import bigquery

from app.api.schemas import (
    ConnectionTestResponse,
    EnterpriseConnectionCreate,
    EnterpriseConnectionListResponse,
    EnterpriseConnectionResponse,
)
from app.repositories.connection_repository import ConnectionRepository
from app.services.secret_manager_service import SecretManagerService

from google.auth.transport.requests import Request as GoogleAuthRequest


logger = logging.getLogger(__name__)


class ConnectionService:
    def __init__(
        self,
        repository: ConnectionRepository,
        secret_manager: SecretManagerService,
    ) -> None:
        self.repository = repository
        self.secret_manager = secret_manager

    @staticmethod
    def _require_organization_id(
        organization_id: str,
    ) -> str:
        normalized = str(
            organization_id or ""
        ).strip()

        if not normalized:
            raise ValueError(
                "organization_id is required for "
                "tenant-isolated connection operations."
            )

        if not normalized.startswith("org_"):
            raise ValueError(
                "organization_id must use the "
                "org_ identifier standard."
            )

        return normalized

    @staticmethod
    def _require_connection_id(
        connection_id: str,
    ) -> str:
        value = str(
            connection_id or ""
        ).strip()

        if not value:
            raise ValueError(
                "connection_id is required."
            )

        is_conn_id = value.startswith("conn_")

        is_uuid = bool(
            re.fullmatch(
                r"[0-9a-fA-F]{8}-"
                r"[0-9a-fA-F]{4}-"
                r"[0-9a-fA-F]{4}-"
                r"[0-9a-fA-F]{4}-"
                r"[0-9a-fA-F]{12}",
                value,
            )
        )

        if not is_conn_id and not is_uuid:
            raise ValueError(
                "connection_id must use either the "
                "conn_ identifier standard or a valid UUID."
            )

        return value
    @staticmethod
    def _require_actor(
        actor: str,
        *,
        field_name: str,
    ) -> str:
        normalized = str(
            actor or ""
        ).strip().lower()

        if not normalized:
            raise ValueError(
                f"{field_name} is required."
            )

        return normalized

    @staticmethod
    def _require_customer_id(
        customer_id: str,
    ) -> str:
        normalized = str(
            customer_id or ""
        ).strip()

        if not normalized:
            raise ValueError(
                "customer_id is required for "
                "tenant-isolated connection creation."
            )

        return normalized

    @staticmethod
    def _assert_connection_tenant(
        *,
        connection: dict[str, Any],
        organization_id: str,
    ) -> None:
        record_organization_id = str(
            connection.get("organization_id")
            or ""
        ).strip()

        if (
            not record_organization_id
            or record_organization_id
            != organization_id
        ):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Enterprise connection does not "
                    "belong to the authenticated organization."
                ),
            )

    def get_connection(
        self,
        *,
        connection_id: str,
        organization_id: str,
    ) -> EnterpriseConnectionResponse:
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )
        effective_connection_id = (
            self._require_connection_id(
                connection_id
            )
        )

        record = self.repository.get_connection(
            connection_id=effective_connection_id,
            organization_id=effective_organization_id,
        )

        if record is None:
            raise HTTPException(
                status_code=404,
                detail="Enterprise connection not found",
            )

        self._assert_connection_tenant(
            connection=record,
            organization_id=effective_organization_id,
        )

        normalized_record = dict(record)

        if isinstance(normalized_record.get("created_at"), datetime):
            normalized_record["created_at"] = normalized_record[
                "created_at"
            ].isoformat()

        if isinstance(normalized_record.get("updated_at"), datetime):
            normalized_record["updated_at"] = normalized_record[
                "updated_at"
            ].isoformat()

        return EnterpriseConnectionResponse.model_validate(
            normalized_record
        )

    def list_connections(
        self,
        *,
        page: int,
        page_size: int,
        organization_id: str,
    ) -> EnterpriseConnectionListResponse:
        effective_organization_id = self._require_organization_id(
            organization_id
        )

        records, total = self.repository.list_connections(
            page=page,
            page_size=page_size,
            organization_id=effective_organization_id,
        )

        items = []

        for record in records:
            self._assert_connection_tenant(
                connection=record,
                organization_id=effective_organization_id,
            )

            normalized_record = dict(record)

            if isinstance(normalized_record.get("created_at"), datetime):
                normalized_record["created_at"] = normalized_record[
                    "created_at"
                ].isoformat()

            if isinstance(normalized_record.get("updated_at"), datetime):
                normalized_record["updated_at"] = normalized_record[
                    "updated_at"
                ].isoformat()

            items.append(
                EnterpriseConnectionResponse.model_validate(
                    normalized_record
                )
            )

        total_pages = (total + page_size - 1) // page_size

        return EnterpriseConnectionListResponse(
                organization_id=effective_organization_id,
                items=items,
                page=page,
                page_size=page_size,
                total=total,
                total_pages=total_pages,
            )

    def _test_databricks(
        self,
        *,
        connection: dict[str, Any],
        credentials: dict[str, Any],
    ) -> tuple[bool, str]:
        endpoint = str(
            connection.get("api_endpoint") or ""
        ).rstrip("/")

        if not endpoint:
            return False, "Databricks API endpoint is missing"

        authentication_type = str(
            credentials.get("authentication_type") or ""
        ).upper()

        if authentication_type == "PAT":
            access_token = credentials.get("token")

            if not access_token:
                return False, "Databricks PAT is missing"

        elif authentication_type in {
            "OAUTH_CLIENT",
            "OAUTH_CLIENT_CREDENTIALS",
        }:
            client_id = credentials.get("client_id")
            client_secret = credentials.get("client_secret")

            if not client_id or not client_secret:
                return (
                    False,
                    "Databricks OAuth credentials are incomplete",
                )

            token_response = requests.post(
                f"{endpoint}/oidc/v1/token",
                auth=(client_id, client_secret),
                data={
                    "grant_type": "client_credentials",
                    "scope": "all-apis",
                },
                timeout=20,
            )

            if not token_response.ok:
                return (
                    False,
                    "Databricks OAuth token request returned "
                    f"HTTP {token_response.status_code}",
                )

            token_payload = token_response.json()
            access_token = token_payload.get("access_token")

            if not access_token:
                return (
                    False,
                    "Databricks OAuth response contained no access token",
                )

        else:
            return (
                False,
                "Unsupported Databricks authentication type: "
                f"{authentication_type or 'MISSING'}",
            )

        test_response = requests.get(
            f"{endpoint}/api/2.0/workspace/list",
            params={"path": "/"},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            timeout=20,
        )

        if test_response.ok:
            return True, "Databricks connection succeeded"

        return (
            False,
            f"Databricks API returned HTTP {test_response.status_code}",
        )

    def _test_snowflake(
        self,
        *,
        connection: dict[str, Any],
        credentials: dict[str, Any],
    ) -> tuple[bool, str]:
        username = credentials.get("username")
        password = credentials.get("password")

        account = (
            credentials.get("account")
            or connection.get("workspace_name")
        )

        if not username or not password or not account:
            return False, "Snowflake credentials are incomplete"

        snowflake_connection = None

        try:
            snowflake_connection = snowflake.connector.connect(
                user=username,
                password=password,
                account=account,
                warehouse=credentials.get("warehouse"),
                database=credentials.get("database"),
                schema=credentials.get("schema"),
                login_timeout=15,
            )

            cursor = snowflake_connection.cursor()

            try:
                cursor.execute("SELECT CURRENT_VERSION()")
                cursor.fetchone()
            finally:
                cursor.close()

            return True, "Snowflake connection succeeded"

        finally:
            if snowflake_connection is not None:
                snowflake_connection.close()

    def _test_bigquery(
        self,
        *,
        connection: dict[str, Any],
        credentials: dict[str, Any],
    ) -> tuple[bool, str]:
        from google.oauth2 import credentials as user_credentials
        from google.oauth2 import service_account

        authentication_type = str(
            credentials.get("authentication_type")
            or connection.get("authentication_type")
            or ""
        ).strip().upper()

        connection_details = (
            connection.get("connection_details") or {}
        )

        if isinstance(connection_details, str):
            try:
                connection_details = json.loads(
                    connection_details
                )
            except json.JSONDecodeError:
                connection_details = {}

        project_id = (
            connection_details.get("project_id")
            or connection.get("workspace_name")
        )

        if not project_id:
            return False, "BigQuery project ID is missing"

        credentials_object: Any

        if authentication_type in {
            "SERVICE_ACCOUNT",
            "SERVICE_ACCOUNT_JSON",
            "M2M",
        }:
            service_account_json = credentials.get(
                "service_account_json"
            )

            if not service_account_json:
                return (
                    False,
                    "BigQuery service account JSON is missing",
                )

            if isinstance(service_account_json, str):
                try:
                    service_account_json = json.loads(
                        service_account_json
                    )
                except json.JSONDecodeError:
                    return (
                        False,
                        "BigQuery service account JSON is invalid",
                    )

            project_id = (
                service_account_json.get("project_id")
                or project_id
            )

            credentials_object = (
                service_account.Credentials
                .from_service_account_info(
                    service_account_json,
                    scopes=[
                        "https://www.googleapis.com/auth/bigquery",
                    ],
                )
            )

        elif authentication_type in {
            "GOOGLE_OAUTH",
            "OAUTH",
            "OAUTH2",
            "U2M",
            "USER_OAUTH",
        }:
            access_token = (
                credentials.get("access_token")
                or credentials.get("token")
            )

            if not access_token:
                return (
                    False,
                    "Google Cloud authorization is required",
                )

            credentials_object = user_credentials.Credentials(
                
                token=access_token,
                refresh_token=credentials.get(
                    "refresh_token"
                ),
                token_uri=(
                    credentials.get("token_url")
                    or "https://oauth2.googleapis.com/token"
                ),
                client_id=credentials.get("client_id"),
                client_secret=credentials.get(
                    "client_secret"
                ),
                scopes=[
                    "https://www.googleapis.com/auth/bigquery",
                ],

            )

            if credentials_object.refresh_token:
                try:
                    credentials_object.refresh(
                        GoogleAuthRequest()
                    )
                except Exception as exc:
                    logger.warning(
                        "Google OAuth refresh failed: %s",
                        type(exc).__name__,
                    )
                    return (
                        False,
                        "Google authorization expired. "
                        "Please reconnect your Google account.",
                    )

        else:
            return (
                False,
                (
                    "Unsupported BigQuery authentication type: "
                    f"{authentication_type or 'MISSING'}"
                ),
            )

        client = bigquery.Client(
            project=project_id,
            credentials=credentials_object,
        )

        rows = list(
            client.query(
                "SELECT 1 AS connection_test"
            ).result()
        )

        if rows and rows[0]["connection_test"] == 1:
            if authentication_type in {
                "GOOGLE_OAUTH",
                "OAUTH",
                "OAUTH2",
                "U2M",
                "USER_OAUTH",
            }:
                return (
                    True,
                    "BigQuery Google OAuth connection succeeded",
                )

            return (
                True,
                "BigQuery service account connection succeeded",
            )

        return (
            False,
            "BigQuery test query did not return a result",
        )

    def test_connection(
        self,
        *,
        connection_id: str,
        tested_by: str,
        organization_id: str,
    ) -> ConnectionTestResponse:
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )
        effective_connection_id = (
            self._require_connection_id(
                connection_id
            )
        )
        effective_tested_by = self._require_actor(
            tested_by,
            field_name="tested_by",
        )

        connection = self.repository.get_connection_for_test(
            connection_id=effective_connection_id,
            organization_id=effective_organization_id,
        )

        if connection is None:
            raise HTTPException(
                status_code=404,
                detail="Enterprise connection not found",
            )

        self._assert_connection_tenant(
            connection=connection,
            organization_id=effective_organization_id,
        )

        if not connection.get("is_active", False):
            raise HTTPException(
                status_code=409,
                detail="Inactive connections cannot be tested",
            )

        credential_reference = connection.get("credential_reference")

        if not credential_reference:
            raise HTTPException(
                status_code=500,
                detail="Connection credential reference is missing",
            )

        try:
            credentials = self.secret_manager.access_secret(
                credential_reference
            )
        except Exception as exc:
            logger.exception(
                "Secret retrieval failed. "
                "organization_id=%s connection_id=%s error=%s",
                effective_organization_id,
                effective_connection_id,
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Unable to retrieve connection credentials",
            ) from exc

        vendor = str(connection.get("vendor") or "").upper()
        authentication_type = str(
            credentials.get("authentication_type") or ""
        ).upper()

        started_at = time.perf_counter()
        error_code: str | None = None
        error_message: str | None = None

        try:
            if vendor == "DATABRICKS":
                success, message = self._test_databricks(
                    connection=connection,
                    credentials=credentials,
                )

            elif vendor == "SNOWFLAKE":
                success, message = self._test_snowflake(
                    connection=connection,
                    credentials=credentials,
                )

            elif vendor in {"BIGQUERY", "GOOGLE_BIGQUERY"}:
                success, message = self._test_bigquery(
                    connection=connection,
                    credentials=credentials,
                )

            else:
                success = False
                message = (
                    f"Unsupported connection vendor: "
                    f"{vendor or 'MISSING'}"
                )
                error_code = "UNSUPPORTED_VENDOR"
                error_message = message

        except Exception as exc:
            success = False
            error_code = type(exc).__name__
            error_message = (
                "Connection test failed. Review credentials, "
                "endpoint, permissions, and network access."
            )

            logger.exception(
                "Connection test failed. "
                "organization_id=%s connection_id=%s "
                "vendor=%s auth_type=%s error=%s",
                effective_organization_id,
                effective_connection_id,
                vendor,
                authentication_type,
                type(exc).__name__,
            )

            message = (
                f"{vendor.title() or 'Connection'} test failed: "
                f"{type(exc).__name__}"
            )

        response_time_ms = round(
            (time.perf_counter() - started_at) * 1000
        )

        tested_at = datetime.now(timezone.utc)
        health_status = "HEALTHY" if success else "UNHEALTHY"

        authentication_status = (
            "SUCCESS" if success else "FAILED"
        )
        connectivity_status = (
            "SUCCESS" if success else "FAILED"
        )

        ai_health_score = 100.0 if success else 0.0
        ai_confidence = 1.0 if success else 0.9

        ai_recommendation = (
            "Connection is healthy and ready for enterprise use."
            if success
            else (
                "Review the authentication type, credentials, endpoint, "
                "service-principal permissions, and outbound network access."
            )
        )

        self.repository.update_health_status(
            connection_id=effective_connection_id,
            organization_id=effective_organization_id,
            health_status=health_status,
            updated_at=tested_at,
        )

        self.repository.create_health_record(
            record={
                "health_check_id": str(uuid.uuid4()),
                "connection_id": effective_connection_id,
                "organization_id": effective_organization_id,
                "connection_name": connection.get("connection_name"),
                "vendor": vendor,
                "environment": connection.get("environment"),
                "authentication_status": authentication_status,
                "connectivity_status": connectivity_status,
                "metadata_status": "NOT_TESTED",
                "read_status": "NOT_TESTED",
                "write_status": "NOT_TESTED",
                "overall_status": health_status,
                "ai_health_score": ai_health_score,
                "ai_confidence": ai_confidence,
                "ai_recommendation": ai_recommendation,
                "latency_ms": response_time_ms,
                "response_time_ms": response_time_ms,
                "datasets_discovered": 0,
                "tables_discovered": 0,
                "metadata_objects_discovered": 0,
                "error_code": error_code,
                "error_message": (
                    error_message
                    if error_message
                    else (None if success else message)
                ),
                "checked_by": effective_tested_by,
                "checked_at": tested_at,
                "created_at": tested_at,
            }
        )

        return ConnectionTestResponse(
                organization_id=effective_organization_id,
                connection_id=effective_connection_id,
                vendor=vendor,
                health_status=health_status,
                success=success,
                message=message,
                tested_at=tested_at.isoformat(),
                response_time_ms=response_time_ms,
)
    def create_connection(
        self,
        request: EnterpriseConnectionCreate,
        *,
        created_by: str,
        organization_id: str,
        customer_id: str,
    ) -> EnterpriseConnectionResponse:
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )
        effective_customer_id = (
            self._require_customer_id(
                customer_id
            )
        )
        effective_created_by = self._require_actor(
            created_by,
            field_name="created_by",
        )

        connection_id = f"conn_{uuid.uuid4().hex[:24]}"
        now = datetime.now(timezone.utc)

        secret_id = self.secret_manager.build_secret_id(
            connection_id=connection_id,
            environment=request.environment,
        )

        credential_reference: str | None = None

        try:
            credential_payload = request.credentials.model_dump(
                exclude_none=True
            )

            credential_reference = (
                self.secret_manager.create_or_update_secret(
                    secret_id=secret_id,
                    credential_payload=credential_payload,
                )
            )

            registry_record: dict[str, Any] = {
                "product": request.product,
                "description": request.description,
                "connection_details": request.connection_details.model_dump(
                    by_alias=True
                ),
                "network_config": request.network_config.model_dump(),
                "health_check_config": request.health_check_config.model_dump(),
                "tags": request.tags,
                "connection_id": connection_id,
                "organization_id": effective_organization_id,
                "customer_id": effective_customer_id,
                "connection_name": request.connection_name,
                "vendor": request.vendor,
                "connection_type": request.connection_type,
                "environment": request.environment,
                "api_endpoint": request.api_endpoint,
                "workspace_name": request.workspace_name,
                "authentication_type": (
                    request.credentials.authentication_type
                ),
                "credential_reference": credential_reference,
                "connection_capabilities": (
                    request.connection_capabilities
                ),
                "health_status": "NOT_TESTED",
                "is_active": True,
                "created_by": effective_created_by,
                "created_at": now,
                "updated_at": now,
            }

            saved_record = self.repository.create_connection(
                registry_record
            )

            self._assert_connection_tenant(
                connection=saved_record,
                organization_id=effective_organization_id,
            )

            return EnterpriseConnectionResponse(
                organization_id=effective_organization_id,
                customer_id=effective_customer_id,
                connection_id=saved_record["connection_id"],
                connection_name=saved_record["connection_name"],
                vendor=saved_record["vendor"],
                connection_type=saved_record["connection_type"],
                environment=saved_record["environment"],
                api_endpoint=saved_record.get("api_endpoint"),
                workspace_name=saved_record.get("workspace_name"),
                authentication_type=saved_record[
                    "authentication_type"
                ],
                connection_capabilities=saved_record.get(
                    "connection_capabilities",
                    [],
                ),
                health_status=saved_record["health_status"],
                is_active=saved_record["is_active"],
                created_by=saved_record.get("created_by"),
                created_at=saved_record["created_at"].isoformat(),
                updated_at=saved_record["updated_at"].isoformat(),
            )

        except Exception as exc:
            logger.exception(
                "Create connection failed. "
                "organization_id=%s customer_id=%s error=%s",
                effective_organization_id,
                effective_customer_id,
                type(exc).__name__,
            )

            if credential_reference:
                try:
                    self.secret_manager.delete_secret(
                        credential_reference
                    )
                except Exception:
                    pass

            raise HTTPException(
                status_code=500,
                detail=(
                    "Unable to securely create the enterprise "
                    "connection."
                ),
            ) from exc
