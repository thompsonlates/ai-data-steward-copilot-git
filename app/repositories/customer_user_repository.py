from __future__ import annotations

import os
import uuid
from typing import Any

from google.cloud import bigquery


class CustomerUserRepository:
    def __init__(
        self,
        *,
        client: bigquery.Client | None = None,
    ) -> None:
        self.project_id = os.getenv(
            "PROJECT_ID",
            "api-project-503305938314",
        )

        self.dataset_id = os.getenv(
            "BIGQUERY_DATASET",
            "ai_data_steward_mvp",
        )

        self.client = client or bigquery.Client(
            project=self.project_id,
        )

        self.customer_users_table = (
            f"{self.project_id}."
            f"{self.dataset_id}."
            "CUSTOMER_USERS"
        )

        self.customers_table = (
            f"{self.project_id}."
            f"{self.dataset_id}."
            "CUSTOMERS"
        )

    def get_customer_id_for_organization(
        self,
        *,
        organization_id: str,
    ) -> str | None:
        query = f"""
        SELECT
            customer_id
        FROM `{self.customers_table}`
        WHERE organization_id = @organization_id
          AND is_active = TRUE
        ORDER BY updated_at DESC
        LIMIT 1
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    organization_id,
                ),
            ]
        )

        rows = self.client.query(
            query,
            job_config=job_config,
        ).result()

        row = next(iter(rows), None)

        return (
            str(row["customer_id"])
            if row
            else None
        )

    def create_invitation(
        self,
        *,
        organization_id: str,
        customer_id: str,
        email: str,
        display_name: str | None,
        organization_role: str,
        billing_role: str,
        created_by: str,
    ) -> dict[str, Any]:
        customer_user_id = (
            f"cu_{uuid.uuid4().hex[:16]}"
        )

        normalized_email = str(
            email or ""
        ).strip().lower()

        query = f"""
        INSERT INTO `{self.customer_users_table}`
        (
            customer_user_id,
            customer_id,
            organization_id,
            user_id,
            email,
            display_name,
            organization_role,
            billing_role,
            invitation_status,
            invited_at,
            accepted_at,
            is_active,
            created_at,
            created_by,
            updated_at
        )
        VALUES
        (
            @customer_user_id,
            @customer_id,
            @organization_id,
            NULL,
            @email,
            @display_name,
            @organization_role,
            @billing_role,
            'PENDING',
            CURRENT_TIMESTAMP(),
            NULL,
            FALSE,
            CURRENT_TIMESTAMP(),
            @created_by,
            CURRENT_TIMESTAMP()
        )
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "customer_user_id",
                    "STRING",
                    customer_user_id,
                ),
                bigquery.ScalarQueryParameter(
                    "customer_id",
                    "STRING",
                    customer_id,
                ),
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "email",
                    "STRING",
                    normalized_email,
                ),
                bigquery.ScalarQueryParameter(
                    "display_name",
                    "STRING",
                    display_name,
                ),
                bigquery.ScalarQueryParameter(
                    "organization_role",
                    "STRING",
                    organization_role,
                ),
                bigquery.ScalarQueryParameter(
                    "billing_role",
                    "STRING",
                    billing_role,
                ),
                bigquery.ScalarQueryParameter(
                    "created_by",
                    "STRING",
                    created_by,
                ),
            ]
        )

        self.client.query(
            query,
            job_config=job_config,
        ).result()

        lookup_sql = f"""
        SELECT
            customer_user_id,
            customer_id,
            organization_id,
            user_id,
            email,
            display_name,
            organization_role,
            billing_role,
            invitation_status,
            invited_at,
            accepted_at,
            is_active,
            created_at,
            created_by,
            updated_at
        FROM `{self.customer_users_table}`
        WHERE customer_user_id = @customer_user_id
        LIMIT 1
        """

        lookup_job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "customer_user_id",
                    "STRING",
                    customer_user_id,
                ),
            ]
        )

        rows = self.client.query(
            lookup_sql,
            job_config=lookup_job_config,
        ).result()

        row = next(iter(rows), None)

        if row is None:
            raise RuntimeError(
                "Customer user invitation was created "
                "but could not be reloaded."
            )

        return dict(row.items())
                