from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
import snowflake.connector
from fastapi import HTTPException
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.cloud import bigquery
from google.oauth2 import credentials as user_credentials

from app.api.schemas import (
    ConnectionTestResponse,
    EnterpriseConnectionCreate,
    EnterpriseConnectionListResponse,
    EnterpriseConnectionResponse,
)
from app.repositories.connection_repository import ConnectionRepository
from app.services.azure_sql_connector import AzureSQLConnector
from app.services.mongodb_connector import MongoDBConnector
from app.services.secret_manager_service import SecretManagerService
from app.services.snowflake_connection_factory import (
    SnowflakeConnectionConfigurationError,
    connect_snowflake,
)


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

    def delete_connection(
        self,
        *,
        connection_id: str,
        organization_id: str,
    ) -> None:
        effective_connection_id = self._require_connection_id(
            connection_id
        )
        effective_organization_id = self._require_organization_id(
            organization_id
        )

        self.repository.delete_connection(
            connection_id=effective_connection_id,
            organization_id=effective_organization_id,
        )
    
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
    def _normalize_environment(
        environment: str,
    ) -> str:
        value = str(environment or "").strip().upper()

        aliases = {
            "DEVELOPMENT": "DEV",
            "DEV": "DEV",
            "STAGE": "STG",
            "STAGING": "STG",
            "STG": "STG",
            "TEST": "TEST",
            "QA": "TEST",
            "UAT": "TEST",
            "PROD": "PRODUCTION",
            "PRD": "PRODUCTION",
            "PRODUCTION": "PRODUCTION",
        }

        normalized = aliases.get(value)

        if normalized is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "environment is required and must be one of "
                    "DEV, STG, TEST, or PRODUCTION."
                ),
            )

        return normalized

    @staticmethod
    def _validate_execution_capabilities_for_environment(
        *,
        environment: str,
        capabilities: list[str],
    ) -> None:
        execution_capabilities = {
            "EXECUTE_DQ_REMEDIATION",
            "EXECUTE_SQL",
            "WRITE_DATA",
        }

        normalized_capabilities = {
            str(value or "").strip().upper()
            for value in capabilities
            if str(value or "").strip()
        }

        if (
            normalized_capabilities.intersection(execution_capabilities)
            and environment not in {"DEV", "STG"}
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "DQ execution capabilities may be assigned only to "
                    "DEV or STG Enterprise Connections."
                ),
            )

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

    def update_connection_capabilities(
        self,
        *,
        connection_id: str,
        organization_id: str,
        connection_capabilities: list[str],
    ) -> EnterpriseConnectionResponse:
        effective_organization_id = self._require_organization_id(
            organization_id
        )
        effective_connection_id = self._require_connection_id(
            connection_id
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

        if not bool(record.get("is_active")):
            raise HTTPException(
                status_code=400,
                detail="Enterprise connection must be active.",
            )

        effective_environment = self._normalize_environment(
            record.get("environment")
        )

        effective_capabilities = list(
            dict.fromkeys(
                str(value or "").strip().upper()
                for value in connection_capabilities
                if str(value or "").strip()
            )
        )

        allowed_capabilities = {
            "READ_DATA",
            "READ_METADATA",
            "TEST_CONNECTION",
            "EXECUTE_DQ_REMEDIATION",
            "EXECUTE_SQL",
            "WRITE_DATA",
        }

        unsupported = sorted(
            set(effective_capabilities) - allowed_capabilities
        )

        if unsupported:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Unsupported Enterprise Connection capabilities: "
                    + ", ".join(unsupported)
                ),
            )

        self._validate_execution_capabilities_for_environment(
            environment=effective_environment,
            capabilities=effective_capabilities,
        )

        self.repository.update_connection_capabilities(
            connection_id=effective_connection_id,
            organization_id=effective_organization_id,
            connection_capabilities=effective_capabilities,
            updated_at=datetime.now(timezone.utc),
        )

        return self.get_connection(
            connection_id=effective_connection_id,
            organization_id=effective_organization_id,
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
        snowflake_connection = None
        try:
            try:
                snowflake_connection = connect_snowflake(
                    connection=connection,
                    credentials=credentials,
                    login_timeout=60,
                )
            except Exception as exc:
                logger.exception(
                    "Snowflake connection test failed: connection_id=%s",
                    connection.get("connection_id"),
                )
                return False, (
                    f"Snowflake test failed: "
                    f"{type(exc).__name__}: {str(exc)}"
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
                scopes=(
                    credentials.get("scopes")
                    or [
                        "https://www.googleapis.com/auth/bigquery",
                        "https://www.googleapis.com/auth/spreadsheets.readonly",
                    ]
                ),

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

    def _test_mongodb(
        self,
        *,
        connection: dict[str, Any],
        credentials: dict[str, Any],
    ) -> tuple[bool, str]:
        connection_details = connection.get("connection_details") or {}

        if isinstance(connection_details, str):
            try:
                connection_details = json.loads(connection_details)
            except json.JSONDecodeError:
                connection_details = {}

        additional_properties = credentials.get("additional_properties")

        if isinstance(additional_properties, dict):
            effective_credentials = {
                **additional_properties,
                **credentials,
            }
        else:
            effective_credentials = dict(credentials)

        connection_uri = (
            effective_credentials.get("connection_uri")
            or effective_credentials.get("mongodb_uri")
            or effective_credentials.get("uri")
        )

        database = (
            effective_credentials.get("database")
            or connection_details.get("database")
            or connection.get("workspace_name")
        )

        if not connection_uri:
            return False, "MongoDB connection URI is missing"

        if not database:
            return False, "MongoDB database is missing"

        connector = MongoDBConnector(
            organization_id=str(connection.get("organization_id") or ""),
            connection_id=str(connection.get("connection_id") or ""),
            connection_uri=str(connection_uri),
            database=str(database),
        )

        result = connector.test_connection()

        if result.get("status") == "HEALTHY":
            return True, "MongoDB connection succeeded"

        return False, "MongoDB connection test failed"


    def _test_azure_sql(
        self,
        *,
        connection: dict[str, Any],
        credentials: dict[str, Any],
    ) -> tuple[bool, str]:
        connection_details = connection.get("connection_details") or {}

        if isinstance(connection_details, str):
            try:
                connection_details = json.loads(connection_details)
            except json.JSONDecodeError:
                connection_details = {}

        additional_properties = credentials.get("additional_properties")

        if isinstance(additional_properties, dict):
            effective_credentials = {
                **additional_properties,
                **credentials,
            }
        else:
            effective_credentials = dict(credentials)

        server = (
            effective_credentials.get("server")
            or connection_details.get("server")
            or connection.get("api_endpoint")
        )

        database = (
            effective_credentials.get("database")
            or connection_details.get("database")
            or connection.get("workspace_name")
        )

        username = effective_credentials.get("username")
        password = effective_credentials.get("password")

        port = (
            effective_credentials.get("port")
            or connection_details.get("port")
            or 1433
        )

        if not server:
            return False, "Azure SQL server is missing"

        if not database:
            return False, "Azure SQL database is missing"

        if not username or not password:
            return False, "Azure SQL username and password are missing"

        connector = AzureSQLConnector(
            organization_id=str(connection.get("organization_id") or ""),
            connection_id=str(connection.get("connection_id") or ""),
            server=str(server),
            database=str(database),
            username=str(username),
            password=str(password),
            port=int(port),
        )

        result = connector.test_connection()

        if result.get("status") == "HEALTHY":
            return True, "Azure SQL connection succeeded"

        return False, "Azure SQL connection test failed"

    def get_google_oauth_credentials(
            self,
            *,
            connection_id: str,
            organization_id: str,
        ):
            effective_organization_id = (
                self._require_organization_id(
                    organization_id
                )
            )

            effective_connection_id = str(
                connection_id or ""
            ).strip()

            if not effective_connection_id:
                raise HTTPException(
                    status_code=400,
                    detail="connection_id is required.",
                )

            connection = (
                self.repository.get_connection(
                    connection_id=(
                        effective_connection_id
                    ),
                    organization_id=(
                        effective_organization_id
                    ),
                )
            )

            if connection is None:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        "Google connection was not found "
                        "for this organization."
                    ),
                )

            self._assert_connection_tenant(
                connection=connection,
                organization_id=(
                    effective_organization_id
                ),
            )

            if not connection.get(
                "is_active",
                False,
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Google connection is inactive."
                    ),
                )

            credential_reference = str(
                connection.get(
                    "credential_reference"
                )
                or ""
            ).strip()

            if not credential_reference:
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "Google connection credential "
                        "reference is missing."
                    ),
                )

            try:
                credentials = self.secret_manager.access_secret(
                    credential_reference
                )
            except Exception as exc:
                logger.exception(
                    "Google OAuth credential retrieval failed. "
                    "organization_id=%s "
                    "connection_id=%s "
                    "credential_reference=%s "
                    "error=%s",
                    effective_organization_id,
                    effective_connection_id,
                    credential_reference,
                    type(exc).__name__,
                )

                raise HTTPException(
                    status_code=500,
                    detail=(
                        "Unable to retrieve Google "
                        "connection credentials."
                    ),
                ) from exc

            authentication_type = str(
                credentials.get(
                    "authentication_type"
                )
                or ""
            ).strip().upper()

            if authentication_type not in {
                "GOOGLE_OAUTH",
                "OAUTH",
                "OAUTH2",
                "U2M",
                "USER_OAUTH",
            }:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Selected connection is not "
                        "Google OAuth authorized."
                    ),
                )

            access_token = (
                credentials.get(
                    "access_token"
                )
                or credentials.get(
                    "token"
                )
            )

            if not access_token:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Google authorization is missing. "
                        "Please reconnect Google."
                    ),
                )

            credentials_object = (
                user_credentials.Credentials(
                    token=access_token,
                    refresh_token=credentials.get(
                        "refresh_token"
                    ),
                    token_uri=(
                        credentials.get(
                            "token_url"
                        )
                        or (
                            "https://oauth2.googleapis.com/"
                            "token"
                        )
                    ),
                    client_id=credentials.get(
                        "client_id"
                    ),
                    client_secret=credentials.get(
                        "client_secret"
                    ),
                    scopes=(
                        credentials.get("scopes")
                        or [
                            (
                                "https://www.googleapis.com/"
                                "auth/bigquery"
                            ),
                            (
                                "https://www.googleapis.com/"
                                "auth/spreadsheets.readonly"
                            ),
                        ]
                    ),
                )
            )

            if credentials_object.refresh_token:
                try:
                    credentials_object.refresh(
                        GoogleAuthRequest()
                    )
                except Exception as exc:
                    logger.warning(
                        "Google OAuth refresh failed. "
                        "organization_id=%s "
                        "connection_id=%s "
                        "error_type=%s "
                        "error_message=%s "
                        "has_refresh_token=%s "
                        "stored_scopes=%s",
                        organization_id,
                        connection_id,
                        type(exc).__name__,
                        str(exc),
                        bool(credentials.get("refresh_token")),
                        credentials.get("scopes"),
                    )

                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Google authorization expired. "
                            "Please reconnect your "
                            "Google account."
                        ),
                    ) from exc

            return credentials_object

    def get_microsoft_oauth_credentials(
        self,
        *,
        connection_id: str,
        organization_id: str,
    ) -> dict[str, Any]:
        """
        Return a fresh server-side Microsoft OAuth credential payload for a
        tenant-isolated OneDrive/SharePoint connection.

        Tokens remain server-side. If the stored access token is expired (or
        near expiry), refresh it using the persisted refresh token and update
        Secret Manager before returning the credential payload.
        """
        effective_organization_id = self._require_organization_id(
            organization_id
        )
        effective_connection_id = self._require_connection_id(
            connection_id
        )

        connection = self.repository.get_connection(
            connection_id=effective_connection_id,
            organization_id=effective_organization_id,
        )

        if connection is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Microsoft OneDrive connection was not found "
                    "for this organization."
                ),
            )

        self._assert_connection_tenant(
            connection=connection,
            organization_id=effective_organization_id,
        )

        if not connection.get("is_active", False):
            raise HTTPException(
                status_code=409,
                detail="Microsoft OneDrive connection is inactive.",
            )

        vendor = str(connection.get("vendor") or "").strip().upper()
        if vendor not in {
            "ONEDRIVE",
            "MICROSOFT_ONEDRIVE",
            "SHAREPOINT",
        }:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Selected connection is not a Microsoft "
                    "OneDrive/SharePoint connection."
                ),
            )

        credential_reference = str(
            connection.get("credential_reference") or ""
        ).strip()

        if not credential_reference:
            raise HTTPException(
                status_code=500,
                detail=(
                    "Microsoft OneDrive connection credential "
                    "reference is missing."
                ),
            )

        try:
            credentials = self.secret_manager.access_secret(
                credential_reference
            )

            print(
            "MICROSOFT OAUTH DEBUG:",
            {
                "connection_id": effective_connection_id,
                "credential_reference": credential_reference,
                "credential_keys": sorted(credentials.keys())
                if isinstance(credentials, dict)
                else str(type(credentials)),
                "has_access_token": bool(
                    credentials.get("access_token")
                    if isinstance(credentials, dict)
                    else False
                ),
                "has_refresh_token": bool(
                    credentials.get("refresh_token")
                    if isinstance(credentials, dict)
                    else False
                ),
                "scopes": credentials.get("scopes")
                if isinstance(credentials, dict)
                else None,
    },
)
        except Exception as exc:
            logger.exception(
                "Microsoft OAuth credential retrieval failed. "
                "organization_id=%s connection_id=%s error=%s",
                effective_organization_id,
                effective_connection_id,
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail=(
                    "Unable to retrieve Microsoft OneDrive "
                    "connection credentials."
                ),
            ) from exc

        authentication_type = str(
            credentials.get("authentication_type")
            or connection.get("authentication_type")
            or ""
        ).strip().upper()

        if authentication_type not in {
            "MICROSOFT_OAUTH",
            "OAUTH",
            "OAUTH2",
            "U2M",
            "USER_OAUTH",
        }:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Selected connection is not Microsoft OAuth authorized."
                ),
            )

        access_token = str(
            credentials.get("access_token")
            or credentials.get("token")
            or ""
        ).strip()
        refresh_token = str(
            credentials.get("refresh_token") or ""
        ).strip()

        if not access_token and not refresh_token:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Microsoft authorization is missing. "
                    "Please reconnect Microsoft."
                ),
            )

        scopes = {
            str(scope).strip()
            for scope in (credentials.get("scopes") or [])
            if str(scope).strip()
        }

        if "Files.ReadWrite" not in scopes:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Microsoft authorization does not include "
                    "Files.ReadWrite. Please reconnect Microsoft."
                ),
            )

        expiry = self._parse_oauth_expiry(
            credentials.get("token_expiry")
        )
        refresh_required = (
            not access_token
            or expiry is None
            or expiry <= (
                datetime.now(timezone.utc)
                + timedelta(minutes=2)
            )
        )

        if refresh_required:
            if not refresh_token:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Microsoft authorization expired and no refresh "
                        "token is available. Please reconnect Microsoft."
                    ),
                )

            token_url = str(
                credentials.get("token_url") or ""
            ).strip()
            client_id = str(
                credentials.get("client_id") or ""
            ).strip()
            client_secret = str(
                credentials.get("client_secret") or ""
            ).strip()

            if not token_url or not client_id or not client_secret:
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "Stored Microsoft OAuth configuration is incomplete."
                    ),
                )

            try:
                response = requests.post(
                    token_url,
                    data={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "scope": " ".join(
                            credentials.get("scopes") or []
                        ),
                    },
                    timeout=30,
                )
            except requests.RequestException as exc:
                logger.warning(
                    "Microsoft OAuth refresh request failed. "
                    "organization_id=%s connection_id=%s error=%s",
                    effective_organization_id,
                    effective_connection_id,
                    type(exc).__name__,
                )
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Microsoft authorization refresh failed. "
                        "Please reconnect Microsoft."
                    ),
                ) from exc

            if not response.ok:
                logger.warning(
                    "Microsoft OAuth refresh returned HTTP %s. "
                    "organization_id=%s connection_id=%s",
                    response.status_code,
                    effective_organization_id,
                    effective_connection_id,
                )
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Microsoft authorization expired. "
                        "Please reconnect Microsoft."
                    ),
                )

            token_payload = response.json()
            refreshed_access_token = str(
                token_payload.get("access_token") or ""
            ).strip()

            if not refreshed_access_token:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Microsoft token refresh returned no access token. "
                        "Please reconnect Microsoft."
                    ),
                )

            expires_in = int(token_payload.get("expires_in") or 3600)
            refreshed_scopes = [
                str(scope).strip()
                for scope in str(
                    token_payload.get("scope") or ""
                ).split()
                if str(scope).strip()
            ] or list(credentials.get("scopes") or [])

            updated_credentials = {
                **credentials,
                "access_token": refreshed_access_token,
                "refresh_token": (
                    str(token_payload.get("refresh_token") or "").strip()
                    or refresh_token
                ),
                "scopes": refreshed_scopes,
                "token_expiry": (
                    datetime.now(timezone.utc)
                    + timedelta(seconds=max(0, expires_in))
                ).isoformat(),
                "oauth_refreshed_at": datetime.now(
                    timezone.utc
                ).isoformat(),
            }

            self._replace_secret_payload(
                credential_reference=credential_reference,
                payload=updated_credentials,
            )
            credentials = updated_credentials

        return credentials

    @staticmethod
    def _parse_oauth_expiry(value: Any) -> datetime | None:
        if not value:
            return None

        if isinstance(value, datetime):
            parsed = value
        else:
            try:
                parsed = datetime.fromisoformat(
                    str(value).replace("Z", "+00:00")
                )
            except (TypeError, ValueError):
                return None

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc)

    def _replace_secret_payload(
        self,
        *,
        credential_reference: str,
        payload: dict[str, Any],
    ) -> None:
        """
        Persist a refreshed OAuth payload using the same Secret Manager
        abstraction already used by connection creation/OAuth completion.
        """
        reference_to_resource = getattr(
            self.secret_manager,
            "_reference_to_resource_name",
            None,
        )
        if not callable(reference_to_resource):
            raise HTTPException(
                status_code=500,
                detail=(
                    "Secret Manager does not support OAuth credential refresh."
                ),
            )

        secret_version_name = reference_to_resource(
            credential_reference
        )
        secret_name = secret_version_name.rsplit(
            "/versions/",
            1,
        )[0]
        secret_id = secret_name.rsplit("/", 1)[-1]

        self.secret_manager.create_or_update_secret(
            secret_id=secret_id,
            credential_payload=payload,
        )

    def get_databricks_connection_context(
        self,
        *,
        connection_id: str,
        organization_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        effective_organization_id = self._require_organization_id(
            organization_id
        )
        effective_connection_id = self._require_connection_id(
            connection_id
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
                detail="Enterprise connection is inactive",
            )

        vendor = str(
            connection.get("vendor") or ""
        ).strip().upper()

        if vendor != "DATABRICKS":
            raise HTTPException(
                status_code=409,
                detail="Enterprise connection is not Databricks",
            )

        credential_reference = connection.get(
            "credential_reference"
        )

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
                "Databricks credential retrieval failed. "
                "organization_id=%s connection_id=%s error=%s",
                effective_organization_id,
                effective_connection_id,
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Unable to retrieve Databricks credentials",
            ) from exc

        if not isinstance(credentials, dict):
            raise HTTPException(
                status_code=500,
                detail="Stored Databricks credentials are invalid",
            )

        return connection, credentials

    def get_snowflake_connection_context(
        self,
        *,
        connection_id: str,
        organization_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Resolve an active Snowflake connection and its tenant-bound secret."""
        effective_org = self._require_organization_id(organization_id)
        effective_id = self._require_connection_id(connection_id)
        connection = self.repository.get_connection_for_test(
            connection_id=effective_id, organization_id=effective_org,
        )
        if connection is None:
            raise HTTPException(status_code=404, detail="Enterprise connection not found")
        self._assert_connection_tenant(
            connection=connection, organization_id=effective_org,
        )
        if not connection.get("is_active", False):
            raise HTTPException(status_code=409, detail="Enterprise connection is inactive")
        vendor = str(connection.get("vendor") or "").strip().upper()
        connection_type = str(connection.get("connection_type") or "").strip().upper()
        snowflake_vendor = bool(re.match(r"^SNOWFLAKE(?:\b|[_-])", vendor))
        snowflake_type = connection_type in {
            "SNOWFLAKE", "SNOWFLAKE_DATABASE", "SNOWFLAKE_SQL",
        }
        other_vendor = vendor in {
            "BIGQUERY", "GOOGLE_BIGQUERY", "DATABRICKS", "MONGODB", "AZURE_SQL",
        }
        if not snowflake_vendor and (not snowflake_type or other_vendor):
            logger.warning(
                "Snowflake connection metadata rejected. organization_id=%s connection_id=%s vendor=%s connection_type=%s",
                effective_org, effective_id, vendor or "MISSING", connection_type or "MISSING",
            )
            raise HTTPException(
                status_code=409,
                detail=("Selected connection is not configured as Snowflake "
                        f"(vendor={vendor or 'MISSING'}, connection_type={connection_type or 'MISSING'})."),
            )
        reference = str(connection.get("credential_reference") or "").strip()
        if not reference:
            raise HTTPException(status_code=500, detail="Connection credential reference is missing")
        try:
            credentials = self.secret_manager.access_secret(reference)
        except Exception as exc:
            logger.exception(
                "Snowflake credential retrieval failed. organization_id=%s connection_id=%s",
                effective_org, effective_id,
            )
            raise HTTPException(status_code=500, detail="Unable to retrieve Snowflake credentials") from exc
        if not isinstance(credentials, dict):
            raise HTTPException(status_code=500, detail="Stored Snowflake credentials are invalid")
        return connection, credentials

    def get_azure_sql_connection_context(
        self,
        *,
        connection_id: str,
        organization_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Resolve an active Azure SQL connection and its tenant-bound secret."""
        effective_org = self._require_organization_id(organization_id)
        effective_id = self._require_connection_id(connection_id)

        connection = self.repository.get_connection_for_test(
            connection_id=effective_id,
            organization_id=effective_org,
        )

        if connection is None:
            raise HTTPException(
                status_code=404,
                detail="Enterprise connection not found",
            )

        self._assert_connection_tenant(
            connection=connection,
            organization_id=effective_org,
        )

        if not connection.get("is_active", False):
            raise HTTPException(
                status_code=409,
                detail="Enterprise connection is inactive",
            )

        vendor = str(connection.get("vendor") or "").strip().upper()

        if vendor != "AZURE_SQL":
            raise HTTPException(
                status_code=409,
                detail="Enterprise connection is not Azure SQL",
            )

        credential_reference = str(
            connection.get("credential_reference") or ""
        ).strip()

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
                organization_id,
                connection_id,
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Unable to retrieve connection credentials",
            ) from exc

        if not isinstance(credentials, dict):
            raise HTTPException(
                status_code=500,
                detail="Stored Azure SQL credentials are invalid",
            )

        return connection, credentials

    def get_bigquery_connection_context(
        self,
        *,
        connection_id: str,
        organization_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Load one tenant-bound active BigQuery connection and its secret."""
        effective_org = self._require_organization_id(organization_id)
        effective_id = self._require_connection_id(connection_id)
        connection = self.repository.get_connection_for_test(
            connection_id=effective_id,
            organization_id=effective_org,
        )
        if connection is None:
            raise HTTPException(status_code=404, detail="Enterprise connection not found")
        self._assert_connection_tenant(
            connection=connection,
            organization_id=effective_org,
        )
        if not connection.get("is_active", False):
            raise HTTPException(status_code=409, detail="Enterprise connection is inactive")
        vendor = str(connection.get("vendor") or "").strip().upper()
        if vendor not in {"BIGQUERY", "GOOGLE_BIGQUERY"}:
            raise HTTPException(status_code=409, detail="Enterprise connection is not BigQuery")
        credential_reference = str(
            connection.get("credential_reference") or ""
        ).strip()
        if not credential_reference:
            raise HTTPException(status_code=500, detail="Connection credential reference is missing")
        try:
            credentials = self.secret_manager.access_secret(credential_reference)
        except Exception as exc:
            logger.exception(
                "BigQuery credential retrieval failed. organization_id=%s connection_id=%s",
                effective_org,
                effective_id,
            )
            raise HTTPException(status_code=500, detail="Unable to retrieve BigQuery credentials") from exc
        if not isinstance(credentials, dict):
            raise HTTPException(status_code=500, detail="Stored BigQuery credentials are invalid")
        return connection, credentials

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

        # TEMP DEBUG - never log secret values
        additional_properties = credentials.get("additional_properties")
        additional_properties = (
            additional_properties
            if isinstance(additional_properties, dict)
            else {}
        )

        logger.warning(
            "CONNECTION_TEST_DEBUG "
            "connection_id=%s "
            "vendor=%s "
            "credential_keys=%s "
            "additional_property_keys=%s "
            "top_username=%r "
            "nested_username=%r "
            "top_password_present=%s "
            "nested_password_present=%s "
            "top_password_length=%s "
            "nested_password_length=%s",
            effective_connection_id,
            connection.get("vendor"),
            sorted(credentials.keys()),
            sorted(additional_properties.keys()),
            credentials.get("username"),
            additional_properties.get("username"),
            bool(credentials.get("password")),
            bool(additional_properties.get("password")),
            len(str(credentials.get("password") or "")),
            len(str(additional_properties.get("password") or "")),
        )

        vendor = str(connection.get("vendor") or "").strip().upper()
        connection_type = str(connection.get("connection_type") or "").strip().upper()
        if (
            re.match(r"^SNOWFLAKE(?:\b|[_-])", vendor)
            or (
                connection_type in {"SNOWFLAKE", "SNOWFLAKE_DATABASE", "SNOWFLAKE_SQL"}
                and vendor not in {"BIGQUERY", "GOOGLE_BIGQUERY", "DATABRICKS", "MONGODB", "AZURE_SQL"}
            )
        ):
            vendor = "SNOWFLAKE"
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

            elif vendor == "MONGODB":
                success, message = self._test_mongodb(
                    connection=connection,
                    credentials=credentials,
                )

            elif vendor == "AZURE_SQL":
                success, message = self._test_azure_sql(
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

        # Environment must be intentionally supplied by the caller.
        # This prevents a schema/default from silently classifying a new
        # Enterprise Connection as PRODUCTION.
        if "environment" not in request.model_fields_set:
            raise HTTPException(
                status_code=400,
                detail=(
                    "environment must be explicitly selected when creating "
                    "an Enterprise Connection."
                ),
            )

        effective_environment = self._normalize_environment(
            request.environment
        )

        effective_capabilities = [
            str(value or "").strip().upper()
            for value in request.connection_capabilities
            if str(value or "").strip()
        ]

        self._validate_execution_capabilities_for_environment(
            environment=effective_environment,
            capabilities=effective_capabilities,
        )

        connection_id = f"conn_{uuid.uuid4().hex[:24]}"
        now = datetime.now(timezone.utc)

        secret_id = self.secret_manager.build_secret_id(
            connection_id=connection_id,
            environment=effective_environment,
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
                "environment": effective_environment,
                "api_endpoint": request.api_endpoint,
                "workspace_name": request.workspace_name,
                "authentication_type": (
                    request.credentials.authentication_type
                ),
                "credential_reference": credential_reference,
                "connection_capabilities": (
                    effective_capabilities
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
