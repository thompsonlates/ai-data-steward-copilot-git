from __future__ import annotations

import os
from typing import Any

from google.cloud import bigquery


class WorkspaceHealthRepository:
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
                "tenant-isolated workspace health."
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

    def get_workspace_health(
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

        sql = f"""
        WITH current_customer AS (
            SELECT
                customer_id,
                organization_id,
                organization_name
            FROM `{self.customers_table}`
            WHERE LOWER(owner_email) = @current_user_email
              AND organization_id = @organization_id
              AND is_active = TRUE
            QUALIFY ROW_NUMBER() OVER (
                ORDER BY updated_at DESC
            ) = 1
        ),

        subscription_metrics AS (
            SELECT
                COUNTIF(
                    UPPER(subscription_status) IN (
                        'ACTIVE',
                        'TRIAL',
                        'TRIALING'
                    )
                    AND is_current = TRUE
                ) > 0 AS subscription_active,

                COALESCE(
                    ARRAY_AGG(
                        IF(
                            UPPER(subscription_status) IN (
                                'ACTIVE',
                                'TRIAL',
                                'TRIALING'
                            )
                            AND is_current = TRUE,
                            plan_code,
                            NULL
                        )
                        IGNORE NULLS
                        ORDER BY updated_at DESC
                        LIMIT 1
                    )[SAFE_OFFSET(0)],
                    'FREE_TRIAL'
                ) AS plan_code
            FROM `{self.subscriptions_table}`
            WHERE customer_id = (
                SELECT customer_id
                FROM current_customer
            )
            AND organization_id = @organization_id
        ),

        connection_metrics AS (
    SELECT
        COUNT(*) AS total_connection_records,

        COUNTIF(
            is_active = TRUE
            AND disconnected_at IS NULL
        ) AS active_platforms
    FROM `{self.connections_table}`
    WHERE organization_id = @organization_id
),

    explanation_metrics AS (
        SELECT
            COUNT(*) AS total_ai_explanations,

            COUNTIF(
                DATE(created_at) = CURRENT_DATE()
            ) AS ai_explanations_today,

            ROUND(
                AVG(
                    SAFE_CAST(
                        automation_readiness_score
                        AS FLOAT64
                    )
                ),
                1
            ) AS average_automation_readiness
        FROM `{self.explanations_table}`
        WHERE organization_id = @organization_id
    ),

    governance_metrics AS (
            SELECT
                COUNTIF(
                    created_by IS NOT NULL
                ) AS configured_governance_policies,

                COUNTIF(
                    created_by IS NOT NULL
                    AND UPPER(active_flag) = 'Y'
                ) AS active_governance_policies

            FROM `{self.policy_config_table}`
            WHERE organization_id = @organization_id
        )
        
    SELECT
        customer.organization_id,
        customer.organization_name,

        subscription.plan_code,
        subscription.subscription_active,

        connections.active_platforms,
        connections.total_connection_records,

        explanations.total_ai_explanations,
        explanations.ai_explanations_today,
        explanations.average_automation_readiness,

        governance.configured_governance_policies,
        governance.active_governance_policies

    FROM current_customer AS customer
    CROSS JOIN subscription_metrics AS subscription
    CROSS JOIN connection_metrics AS connections
    CROSS JOIN explanation_metrics AS explanations
    CROSS JOIN governance_metrics AS governance
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

        returned_organization_id = str(
            result.get("organization_id") or ""
        ).strip()

        if (
            returned_organization_id
            != effective_organization_id
        ):
            raise RuntimeError(
                "Workspace health query returned a "
                "different organization than the "
                "authenticated tenant."
            )

        return result
