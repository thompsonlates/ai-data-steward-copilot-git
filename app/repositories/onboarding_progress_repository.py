from __future__ import annotations

import os
from typing import Any

from google.cloud import bigquery


class OnboardingProgressRepository:
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

        self.customers_table = (
            f"{self.project_id}."
            f"{self.dataset_id}."
            "CUSTOMERS"
        )

        self.subscriptions_table = (
            f"{self.project_id}."
            f"{self.dataset_id}."
            "CUSTOMER_SUBSCRIPTIONS"
        )

        self.connections_table = (
            f"{self.project_id}."
            f"{self.dataset_id}."
            "AI_CONNECTION_REGISTRY"
        )

        self.explanations_table = (
            f"{self.project_id}."
            f"{self.dataset_id}."
            "Match_Explanations_Log"
        )

        self.policy_config_table = (
            f"{self.project_id}."
            f"{self.dataset_id}."
            "Policy_Config"
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
                "tenant-isolated onboarding progress."
            )

        if not normalized.startswith("org_"):
            raise ValueError(
                "organization_id must use the org_ identifier standard."
            )

        return normalized

    @staticmethod
    def _require_email(
        email: str,
    ) -> str:
        normalized = str(
            email or ""
        ).strip().lower()

        if not normalized:
            raise ValueError(
                "current_user_email is required."
            )

        return normalized

    def get_progress_signals(
        self,
        *,
        current_user_email: str,
        organization_id: str,
    ) -> dict[str, Any] | None:
        normalized_email = self._require_email(
            current_user_email
        )

        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        print(
                        "ONBOARDING CLIENT TYPE:",
                        type(self.client),
                        self.client,
                        flush=True,
        )

        sql = f"""
        WITH current_customer AS (
            SELECT
                customer_id,
                organization_id
            FROM `{self.customers_table}`
            WHERE LOWER(owner_email) = @current_user_email
              AND organization_id = @organization_id
              AND is_active = TRUE
            QUALIFY ROW_NUMBER() OVER (
                ORDER BY updated_at DESC
            ) = 1
        ),

        subscription_signal AS (
            SELECT
                COUNTIF(
                    subscription_status = 'ACTIVE'
                    AND is_current = TRUE
                ) > 0 AS subscription_active
            FROM `{self.subscriptions_table}`
            WHERE customer_id = (
                SELECT customer_id
                FROM current_customer
            )
              AND organization_id = @organization_id
        ),


        connection_signal AS (
            SELECT
                COUNTIF(
                    is_active = TRUE
                    AND disconnected_at IS NULL
                ) > 0 AS platform_connected
            FROM `{self.connections_table}`
            WHERE organization_id = @organization_id
        ),

        explanation_signal AS (
            SELECT
                COUNT(*) > 0 AS explanation_completed
            FROM `{self.explanations_table}`
            WHERE organization_id = @organization_id
        ),

        governance_signal AS (
            SELECT
                COUNT(*) > 0 AS governance_configured
            FROM `{self.policy_config_table}`
            WHERE organization_id = @organization_id
        )

        SELECT
            customer.customer_id,
            customer.organization_id,
            subscription.subscription_active,
            connection.platform_connected,
            explanation.explanation_completed,
            governance.governance_configured
        FROM current_customer AS customer
        CROSS JOIN subscription_signal AS subscription
        CROSS JOIN connection_signal AS connection
        CROSS JOIN explanation_signal AS explanation
        CROSS JOIN governance_signal AS governance
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "current_user_email",
                    "STRING",
                    normalized_email,
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

        result = dict(rows[0].items())

        if (
            str(
                result.get("organization_id")
                or ""
            ).strip()
            != effective_organization_id
        ):
            raise RuntimeError(
                "Onboarding progress query returned "
                "a different organization than the "
                "authenticated tenant."
            )

        return result
