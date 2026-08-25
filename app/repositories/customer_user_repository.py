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

        self.ai_users_table = (
            f"{self.project_id}."
            f"{self.dataset_id}."
            "AI_USERS"
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

    def get_organization_name_for_organization(
        self,
        *,
        organization_id: str,
    ) -> str | None:
        query = f"""
        SELECT
            organization_name
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
            str(row["organization_name"])
            if row and row["organization_name"]
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

    def get_active_user_by_email(
        self,
        *,
        email: str,
    ) -> dict[str, Any] | None:
        normalized_email = str(
            email or ""
        ).strip().lower()

        if not normalized_email:
            return None

        sql = f"""
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
            is_active,
            created_at,
            updated_at
        FROM `{self.customer_users_table}`
        WHERE LOWER(email) = @email
        AND is_active = TRUE
        AND user_id IS NOT NULL
        AND organization_id IS NOT NULL
        ORDER BY created_at DESC
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "email",
                    "STRING",
                    normalized_email,
                )
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

        distinct_orgs = {
            str(row["organization_id"]).strip()
            for row in rows
            if row["organization_id"]
        }

        if len(distinct_orgs) > 1:
            raise RuntimeError(
                "Multiple active organization memberships "
                f"found for email={normalized_email}: "
                f"{sorted(distinct_orgs)}"
            )

        return dict(rows[0].items())


    def get_pending_invitation_by_email(
        self,
        *,
        email: str,
    ) -> dict[str, Any] | None:
        normalized_email = str(
            email or ""
        ).strip().lower()

        if not normalized_email:
            return None

        query = f"""
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
        WHERE LOWER(email) = @email
        AND UPPER(
            COALESCE(
                invitation_status,
                ''
            )
        ) IN ('PENDING', 'INVITED')
        AND is_active = FALSE
        ORDER BY invited_at DESC
        LIMIT 1
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "email",
                    "STRING",
                    normalized_email,
                ),
            ]
        )

        rows = self.client.query(
            query,
            job_config=job_config,
        ).result()

        row = next(iter(rows), None)

        return (
            dict(row.items())
            if row
            else None
        )

    def activate_invitation(
        self,
        *,
        customer_user_id: str,
        organization_id: str,
        user_id: str,
        email: str,
        full_name: str | None,
        organization_role: str,
        auth_provider: str,
    ) -> dict[str, Any]:
        normalized_email = str(
            email or ""
        ).strip().lower()

        sql = f"""
        BEGIN TRANSACTION;

        MERGE `{self.ai_users_table}` AS target
        USING (
            SELECT
                @user_id AS user_id,
                @organization_id AS organization_id,
                @email AS email
        ) AS source
        ON target.organization_id = source.organization_id
        AND LOWER(target.email) = LOWER(source.email)

        WHEN NOT MATCHED THEN
        INSERT (
            user_id,
            organization_id,
            email,
            full_name,
            role,
            auth_provider,
            last_login,
            is_active,
            created_at,
            updated_at
        )
        VALUES (
            @user_id,
            @organization_id,
            @email,
            @full_name,
            @organization_role,
            @auth_provider,
            CURRENT_TIMESTAMP(),
            TRUE,
            CURRENT_TIMESTAMP(),
            CURRENT_TIMESTAMP()
        )

        WHEN MATCHED THEN
        UPDATE SET
            user_id = COALESCE(
                target.user_id,
                @user_id
            ),
            full_name = COALESCE(
                @full_name,
                target.full_name
            ),
            role = @organization_role,
            auth_provider = @auth_provider,
            last_login = CURRENT_TIMESTAMP(),
            is_active = TRUE,
            updated_at = CURRENT_TIMESTAMP();

        UPDATE `{self.customer_users_table}`
        SET
            user_id = @user_id,
            invitation_status = 'ACCEPTED',
            accepted_at = COALESCE(
                accepted_at,
                CURRENT_TIMESTAMP()
            ),
            is_active = TRUE,
            updated_at = CURRENT_TIMESTAMP()
        WHERE customer_user_id = @customer_user_id
        AND organization_id = @organization_id
        AND LOWER(email) = @email
        AND UPPER(
            COALESCE(
                invitation_status,
                ''
            )
        ) IN ('PENDING', 'INVITED')
        AND is_active = FALSE;

        COMMIT TRANSACTION;
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "customer_user_id",
                    "STRING",
                    customer_user_id,
                ),
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "user_id",
                    "STRING",
                    user_id,
                ),
                bigquery.ScalarQueryParameter(
                    "email",
                    "STRING",
                    normalized_email,
                ),
                bigquery.ScalarQueryParameter(
                    "full_name",
                    "STRING",
                    full_name,
                ),
                bigquery.ScalarQueryParameter(
                    "organization_role",
                    "STRING",
                    organization_role,
                ),
                bigquery.ScalarQueryParameter(
                    "auth_provider",
                    "STRING",
                    auth_provider,
                ),
            ]
        )

        self.client.query(
            sql,
            job_config=job_config,
        ).result()

        query = f"""
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

        rows = self.client.query(
            query,
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter(
                        "customer_user_id",
                        "STRING",
                        customer_user_id,
                    ),
                ]
            ),
        ).result()

        row = next(iter(rows), None)

        if row is None:
            raise RuntimeError(
                "Activated invitation could not be reloaded."
            )

        return dict(row.items())