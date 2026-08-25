from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from google.cloud import bigquery


class CustomConnectionRepository:
    def __init__(
        self,
        *,
        project_id: str,
        dataset_id: str,
    ) -> None:
        self.project_id = project_id
        self.dataset_id = dataset_id
        self.client = bigquery.Client(
            project=project_id
        )

        self.table_id = (
            f"{project_id}."
            f"{dataset_id}."
            "CUSTOM_CONNECTION_REQUESTS"
        )

    def create_request(
        self,
        *,
        request_id: str,
        organization_id: str,
        customer_id: str,
        requested_by: str,
        contact_email: str,
        vendor_name: str,
        connection_type: str,
        environment: str,
        auth_preference: str | None,
        read_data_required: bool,
        metadata_required: bool,
        use_case: str | None,
        request_status: str = "REQUESTED",
    ) -> dict[str, Any]:

        now = datetime.now(timezone.utc)

        sql = f"""
        INSERT INTO `{self.table_id}`
        (
            request_id,
            organization_id,
            customer_id,
            requested_by,
            vendor_name,
            connection_type,
            environment,
            auth_preference,
            read_data_required,
            metadata_required,
            use_case,
            contact_email,
            request_status,
            created_at,
            updated_at
        )
        VALUES
        (
            @request_id,
            @organization_id,
            @customer_id,
            @requested_by,
            @vendor_name,
            @connection_type,
            @environment,
            @auth_preference,
            @read_data_required,
            @metadata_required,
            @use_case,
            @contact_email,
            @request_status,
            @created_at,
            @updated_at
        )
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "request_id",
                    "STRING",
                    request_id,
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
                    "requested_by",
                    "STRING",
                    requested_by,
                ),
                bigquery.ScalarQueryParameter(
                    "vendor_name",
                    "STRING",
                    vendor_name,
                ),
                bigquery.ScalarQueryParameter(
                    "connection_type",
                    "STRING",
                    connection_type,
                ),
                bigquery.ScalarQueryParameter(
                    "environment",
                    "STRING",
                    environment,
                ),
                bigquery.ScalarQueryParameter(
                    "auth_preference",
                    "STRING",
                    auth_preference,
                ),
                bigquery.ScalarQueryParameter(
                    "read_data_required",
                    "BOOL",
                    read_data_required,
                ),
                bigquery.ScalarQueryParameter(
                    "metadata_required",
                    "BOOL",
                    metadata_required,
                ),
                bigquery.ScalarQueryParameter(
                    "use_case",
                    "STRING",
                    use_case,
                ),
                bigquery.ScalarQueryParameter(
                    "contact_email",
                    "STRING",
                    contact_email,
                ),
                bigquery.ScalarQueryParameter(
                    "request_status",
                    "STRING",
                    request_status,
                ),
                bigquery.ScalarQueryParameter(
                    "created_at",
                    "TIMESTAMP",
                    now,
                ),
                bigquery.ScalarQueryParameter(
                    "updated_at",
                    "TIMESTAMP",
                    now,
                ),
            ]
        )

        self.client.query(
            sql,
            job_config=job_config,
        ).result()

        return {
            "request_id": request_id,
            "organization_id": organization_id,
            "customer_id": customer_id,
            "requested_by": requested_by,
            "vendor_name": vendor_name,
            "connection_type": connection_type,
            "environment": environment,
            "auth_preference": auth_preference,
            "read_data_required": read_data_required,
            "metadata_required": metadata_required,
            "use_case": use_case,
            "contact_email": contact_email,
            "request_status": request_status,
            "created_at": now,
        }