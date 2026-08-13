from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from google.cloud import bigquery


class ConnectionRepository:
    def __init__(
        self,
        project_id: str,
        dataset_id: str,
        *,
        client: bigquery.Client | None = None,
    ) -> None:
        if not project_id:
            raise ValueError("project_id is required")

        if not dataset_id:
            raise ValueError("dataset_id is required")

        self.project_id = project_id
        self.dataset_id = dataset_id
        self.client = client or bigquery.Client(
            project=project_id
        )

        self.registry_table = (
            f"{project_id}.{dataset_id}."
            "AI_CONNECTION_REGISTRY"
        )

        self.health_table = (
            f"{project_id}.{dataset_id}."
            "AI_CONNECTION_HEALTH"
        )

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

        return normalized

    @staticmethod
    def _require_connection_id(
        connection_id: str,
    ) -> str:
        normalized = str(
            connection_id or ""
        ).strip()

        if not normalized:
            raise ValueError(
                "connection_id is required."
            )

        return normalized

    def create_connection(
        self,
        record: dict[str, Any],
    ) -> dict[str, Any]:
        organization_id = (
            self._require_organization_id(
                str(record.get("organization_id") or "")
            )
        )

        connection_id = self._require_connection_id(
            str(record.get("connection_id") or "")
        )

        customer_id = (
            str(record.get("customer_id") or "").strip()
            or None
        )

        sql = f"""
        INSERT INTO `{self.registry_table}`
        (
            connection_id,
            organization_id,
            customer_id,
            product,
            description,
            connection_details,
            network_config,
            health_check_config,
            tags,
            connection_name,
            vendor,
            connection_type,
            environment,
            api_endpoint,
            workspace_name,
            authentication_type,
            credential_reference,
            connection_capabilities,
            health_status,
            is_active,
            created_by,
            created_at,
            updated_at
        )
        VALUES
        (
            @connection_id,
            @organization_id,
            @customer_id,
            @product,
            @description,
            PARSE_JSON(@connection_details),
            PARSE_JSON(@network_config),
            PARSE_JSON(@health_check_config),
            @tags,
            @connection_name,
            @vendor,
            @connection_type,
            @environment,
            @api_endpoint,
            @workspace_name,
            @authentication_type,
            @credential_reference,
            @connection_capabilities,
            @health_status,
            @is_active,
            @created_by,
            @created_at,
            @updated_at
        )
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "connection_id",
                    "STRING",
                    connection_id,
                ),
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "customer_id",
                    "STRING",
                    customer_id,
                ),
                bigquery.ScalarQueryParameter(
                    "product",
                    "STRING",
                    record.get("product"),
                ),
                bigquery.ScalarQueryParameter(
                    "description",
                    "STRING",
                    record.get("description"),
                ),
                bigquery.ScalarQueryParameter(
                    "connection_details",
                    "STRING",
                    json.dumps(
                        record.get("connection_details")
                        or {}
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "network_config",
                    "STRING",
                    json.dumps(
                        record.get("network_config")
                        or {}
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "health_check_config",
                    "STRING",
                    json.dumps(
                        record.get("health_check_config")
                        or {}
                    ),
                ),
                bigquery.ArrayQueryParameter(
                    "tags",
                    "STRING",
                    record.get("tags") or [],
                ),
                bigquery.ScalarQueryParameter(
                    "connection_name",
                    "STRING",
                    record["connection_name"],
                ),
                bigquery.ScalarQueryParameter(
                    "vendor",
                    "STRING",
                    record["vendor"],
                ),
                bigquery.ScalarQueryParameter(
                    "connection_type",
                    "STRING",
                    record["connection_type"],
                ),
                bigquery.ScalarQueryParameter(
                    "environment",
                    "STRING",
                    record["environment"],
                ),
                bigquery.ScalarQueryParameter(
                    "api_endpoint",
                    "STRING",
                    record.get("api_endpoint"),
                ),
                bigquery.ScalarQueryParameter(
                    "workspace_name",
                    "STRING",
                    record.get("workspace_name"),
                ),
                bigquery.ScalarQueryParameter(
                    "authentication_type",
                    "STRING",
                    record["authentication_type"],
                ),
                bigquery.ScalarQueryParameter(
                    "credential_reference",
                    "STRING",
                    record["credential_reference"],
                ),
                bigquery.ArrayQueryParameter(
                    "connection_capabilities",
                    "STRING",
                    record.get(
                        "connection_capabilities",
                        [],
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "health_status",
                    "STRING",
                    record.get(
                        "health_status",
                        "NOT_TESTED",
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "is_active",
                    "BOOL",
                    record.get("is_active", False),
                ),
                bigquery.ScalarQueryParameter(
                    "created_by",
                    "STRING",
                    record.get("created_by"),
                ),
                bigquery.ScalarQueryParameter(
                    "created_at",
                    "TIMESTAMP",
                    record["created_at"],
                ),
                bigquery.ScalarQueryParameter(
                    "updated_at",
                    "TIMESTAMP",
                    record["updated_at"],
                ),
            ]
        )

        query_job = self.client.query(
            sql,
            job_config=job_config,
        )
        query_job.result()

        if query_job.num_dml_affected_rows != 1:
            raise RuntimeError(
                "Connection insert did not affect exactly "
                "one row."
            )

        saved_record = dict(record)
        saved_record["connection_id"] = connection_id
        saved_record["organization_id"] = (
            organization_id
        )
        saved_record["customer_id"] = customer_id

        return saved_record

    def get_connection(
        self,
        *,
        connection_id: str,
        organization_id: str,
    ) -> dict[str, Any] | None:
        effective_connection_id = (
            self._require_connection_id(
                connection_id
            )
        )
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        sql = f"""
        SELECT
            connection_id,
            organization_id,
            customer_id,
            product,
            description,
            connection_details,
            network_config,
            health_check_config,
            tags,
            connection_name,
            vendor,
            connection_type,
            environment,
            api_endpoint,
            workspace_name,
            authentication_type,
            connection_capabilities,
            health_status,
            is_active,
            created_by,
            created_at,
            updated_at
        FROM `{self.registry_table}`
        WHERE connection_id = @connection_id
          AND organization_id = @organization_id
        LIMIT 1
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "connection_id",
                    "STRING",
                    effective_connection_id,
                ),
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                ),
            ]
        )

        rows = list(
            self.client.query(
                sql,
                job_config=job_config,
            ).result()
        )

        if not rows:
            return None

        return dict(rows[0].items())

    def get_connection_for_test(
        self,
        *,
        connection_id: str,
        organization_id: str,
    ) -> dict[str, Any] | None:
        effective_connection_id = (
            self._require_connection_id(
                connection_id
            )
        )
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        sql = f"""
        SELECT
            connection_id,
            organization_id,
            customer_id,
            connection_name,
            vendor,
            connection_type,
            environment,
            api_endpoint,
            workspace_name,
            connection_details,
            authentication_type,
            credential_reference,
            connection_capabilities,
            health_status,
            is_active,
            created_by,
            created_at,
            updated_at
        FROM `{self.registry_table}`
        WHERE connection_id = @connection_id
          AND organization_id = @organization_id
        LIMIT 1
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "connection_id",
                    "STRING",
                    effective_connection_id,
                ),
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                ),
            ]
        )

        rows = list(
            self.client.query(
                sql,
                job_config=job_config,
            ).result()
        )

        if not rows:
            return None

        return dict(rows[0].items())

    def update_health_status(
        self,
        *,
        connection_id: str,
        organization_id: str,
        health_status: str,
        updated_at: datetime,
    ) -> None:
        effective_connection_id = (
            self._require_connection_id(
                connection_id
            )
        )
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        sql = f"""
        UPDATE `{self.registry_table}`
        SET
            health_status = @health_status,
            updated_at = @updated_at
        WHERE connection_id = @connection_id
          AND organization_id = @organization_id
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "connection_id",
                    "STRING",
                    effective_connection_id,
                ),
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "health_status",
                    "STRING",
                    health_status,
                ),
                bigquery.ScalarQueryParameter(
                    "updated_at",
                    "TIMESTAMP",
                    updated_at,
                ),
            ]
        )

        query_job = self.client.query(
            sql,
            job_config=job_config,
        )
        query_job.result()

        if query_job.num_dml_affected_rows != 1:
            raise RuntimeError(
                "Connection health status was not "
                "updated for the authenticated "
                "organization."
            )

    def complete_google_oauth_connection(
        self,
        *,
        connection_id: str,
        organization_id: str,
        created_by: str,
        credential_reference: str,
        updated_at: datetime,
    ) -> None:
        effective_connection_id = (
            self._require_connection_id(
                connection_id
            )
        )
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )
        normalized_created_by = str(
            created_by or ""
        ).strip().lower()

        if not normalized_created_by:
            raise ValueError(
                "created_by is required for Google OAuth "
                "connection completion."
            )

        sql = f"""
        UPDATE `{self.registry_table}`
        SET
            authentication_type = 'GOOGLE_OAUTH',
            credential_reference = @credential_reference,
            health_status = 'HEALTHY',
            is_active = TRUE,
            updated_at = @updated_at
        WHERE connection_id = @connection_id
          AND organization_id = @organization_id
          AND LOWER(created_by) = @created_by
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "connection_id",
                    "STRING",
                    effective_connection_id,
                ),
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "created_by",
                    "STRING",
                    normalized_created_by,
                ),
                bigquery.ScalarQueryParameter(
                    "credential_reference",
                    "STRING",
                    credential_reference,
                ),
                bigquery.ScalarQueryParameter(
                    "updated_at",
                    "TIMESTAMP",
                    updated_at,
                ),
            ]
        )

        query_job = self.client.query(
            sql,
            job_config=job_config,
        )
        query_job.result()

        if query_job.num_dml_affected_rows != 1:
            raise RuntimeError(
                "Google OAuth completed, but the "
                "enterprise connection record was not "
                "updated for the authenticated organization. "
                f"connection_id={effective_connection_id}, "
                f"organization_id={effective_organization_id}, "
                f"created_by={normalized_created_by}, "
                "rows_updated="
                f"{query_job.num_dml_affected_rows}"
            )

    def create_health_record(
        self,
        *,
        record: dict[str, Any],
    ) -> None:
        organization_id = (
            self._require_organization_id(
                str(record.get("organization_id") or "")
            )
        )
        connection_id = self._require_connection_id(
            str(record.get("connection_id") or "")
        )

        # INSERT ... SELECT + EXISTS prevents a health row from being
        # written against a connection owned by another organization.
        sql = f"""
        INSERT INTO `{self.health_table}`
        (
            health_check_id,
            connection_id,
            organization_id,
            connection_name,
            vendor,
            environment,
            authentication_status,
            connectivity_status,
            metadata_status,
            read_status,
            write_status,
            overall_status,
            ai_health_score,
            ai_confidence,
            ai_recommendation,
            latency_ms,
            response_time_ms,
            datasets_discovered,
            tables_discovered,
            metadata_objects_discovered,
            error_code,
            error_message,
            checked_by,
            checked_at,
            created_at
        )
        SELECT
            @health_check_id,
            @connection_id,
            @organization_id,
            @connection_name,
            @vendor,
            @environment,
            @authentication_status,
            @connectivity_status,
            @metadata_status,
            @read_status,
            @write_status,
            @overall_status,
            @ai_health_score,
            @ai_confidence,
            @ai_recommendation,
            @latency_ms,
            @response_time_ms,
            @datasets_discovered,
            @tables_discovered,
            @metadata_objects_discovered,
            @error_code,
            @error_message,
            @checked_by,
            @checked_at,
            @created_at
        FROM (SELECT 1) AS tenant_guard
        WHERE EXISTS (
            SELECT 1
            FROM `{self.registry_table}`
            WHERE connection_id = @connection_id
              AND organization_id = @organization_id
        )
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "health_check_id",
                    "STRING",
                    record["health_check_id"],
                ),
                bigquery.ScalarQueryParameter(
                    "connection_id",
                    "STRING",
                    connection_id,
                ),
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "connection_name",
                    "STRING",
                    record.get("connection_name"),
                ),
                bigquery.ScalarQueryParameter(
                    "vendor",
                    "STRING",
                    record.get("vendor"),
                ),
                bigquery.ScalarQueryParameter(
                    "environment",
                    "STRING",
                    record.get("environment"),
                ),
                bigquery.ScalarQueryParameter(
                    "authentication_status",
                    "STRING",
                    record.get(
                        "authentication_status",
                        "NOT_TESTED",
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "connectivity_status",
                    "STRING",
                    record.get(
                        "connectivity_status",
                        "NOT_TESTED",
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "metadata_status",
                    "STRING",
                    record.get(
                        "metadata_status",
                        "NOT_TESTED",
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "read_status",
                    "STRING",
                    record.get(
                        "read_status",
                        "NOT_TESTED",
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "write_status",
                    "STRING",
                    record.get(
                        "write_status",
                        "NOT_TESTED",
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "overall_status",
                    "STRING",
                    record.get(
                        "overall_status",
                        "UNKNOWN",
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "ai_health_score",
                    "FLOAT64",
                    record.get("ai_health_score"),
                ),
                bigquery.ScalarQueryParameter(
                    "ai_confidence",
                    "FLOAT64",
                    record.get("ai_confidence"),
                ),
                bigquery.ScalarQueryParameter(
                    "ai_recommendation",
                    "STRING",
                    record.get("ai_recommendation"),
                ),
                bigquery.ScalarQueryParameter(
                    "latency_ms",
                    "INT64",
                    record.get("latency_ms"),
                ),
                bigquery.ScalarQueryParameter(
                    "response_time_ms",
                    "INT64",
                    record.get("response_time_ms"),
                ),
                bigquery.ScalarQueryParameter(
                    "datasets_discovered",
                    "INT64",
                    record.get(
                        "datasets_discovered",
                        0,
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "tables_discovered",
                    "INT64",
                    record.get(
                        "tables_discovered",
                        0,
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "metadata_objects_discovered",
                    "INT64",
                    record.get(
                        "metadata_objects_discovered",
                        0,
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "error_code",
                    "STRING",
                    record.get("error_code"),
                ),
                bigquery.ScalarQueryParameter(
                    "error_message",
                    "STRING",
                    record.get("error_message"),
                ),
                bigquery.ScalarQueryParameter(
                    "checked_by",
                    "STRING",
                    record.get("checked_by"),
                ),
                bigquery.ScalarQueryParameter(
                    "checked_at",
                    "TIMESTAMP",
                    record["checked_at"],
                ),
                bigquery.ScalarQueryParameter(
                    "created_at",
                    "TIMESTAMP",
                    record.get(
                        "created_at",
                        record["checked_at"],
                    ),
                ),
            ]
        )

        query_job = self.client.query(
            sql,
            job_config=job_config,
        )
        query_job.result()

        if query_job.num_dml_affected_rows != 1:
            raise RuntimeError(
                "Connection health record was not "
                "created for the authenticated "
                "organization."
            )

    def delete_connection(
        self,
        *,
        connection_id: str,
        organization_id: str,
    ) -> None:
        effective_connection_id = (
            self._require_connection_id(
                connection_id
            )
        )
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        sql = f"""
        DELETE FROM `{self.registry_table}`
        WHERE connection_id = @connection_id
          AND organization_id = @organization_id
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "connection_id",
                    "STRING",
                    effective_connection_id,
                ),
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                ),
            ]
        )

        query_job = self.client.query(
            sql,
            job_config=job_config,
        )
        query_job.result()

        if query_job.num_dml_affected_rows != 1:
            raise RuntimeError(
                "Connection was not deleted for the "
                "authenticated organization."
            )

    def list_connections(
        self,
        *,
        page: int,
        page_size: int,
        organization_id: str,
    ) -> tuple[list[dict[str, Any]], int]:
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        if page < 1:
            raise ValueError(
                "page must be at least 1."
            )

        if page_size < 1 or page_size > 100:
            raise ValueError(
                "page_size must be between 1 and 100."
            )

        offset = (page - 1) * page_size

        count_query = f"""
        SELECT COUNT(*) AS total
        FROM `{self.registry_table}`
        WHERE organization_id = @organization_id
        """

        count_job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                )
            ]
        )

        count_result = self.client.query(
            count_query,
            job_config=count_job_config,
        ).result()

        count_row = next(
            iter(count_result),
            None,
        )

        total = (
            int(count_row.total)
            if count_row
            else 0
        )

        list_query = f"""
        SELECT
            organization_id,
            customer_id,
            connection_id,
            product,
            description,
            connection_name,
            vendor,
            connection_type,
            environment,
            api_endpoint,
            workspace_name,
            authentication_type,
            connection_capabilities,
            health_status,
            is_active,
            created_by,
            created_at,
            updated_at
        FROM `{self.registry_table}`
        WHERE organization_id = @organization_id
        ORDER BY created_at DESC
        LIMIT @page_size
        OFFSET @offset
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "page_size",
                    "INT64",
                    page_size,
                ),
                bigquery.ScalarQueryParameter(
                    "offset",
                    "INT64",
                    offset,
                ),
            ]
        )

        rows = self.client.query(
            list_query,
            job_config=job_config,
        ).result()

        connections = [
            dict(row.items())
            for row in rows
        ]

        return connections, total

