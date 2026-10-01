from __future__ import annotations

import os
from typing import Any

from google.cloud import bigquery


class EntitlementRepository:
    SUPPORTED_DOMAINS = frozenset(
        {
            "CUSTOMER",
            "SUPPLIER",
            "PRODUCT",
            "PROVIDER",
            "PATIENT",
            "BANKING",
            "LOCATION",
            "ORGANIZATION",
        }
    )

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

        self.organization_domain_entitlements_table = (
            f"{self.project_id}."
            f"{self.dataset_id}."
            "ORGANIZATION_DOMAIN_ENTITLEMENTS"
        )

        self.plan_entitlements_table = (
            f"{self.project_id}."
            f"{self.dataset_id}."
            "PLAN_ENTITLEMENTS"
        )

        self.subscriptions_table = (
            f"{self.project_id}."
            f"{self.dataset_id}."
            "CUSTOMER_SUBSCRIPTIONS"
        )
        
    def get_current_subscription_for_organization(
        self,
        *,
        organization_id: str,
    ) -> dict[str, Any] | None:
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        query = f"""
        SELECT
            subscription_record_id,
            customer_id,
            organization_id,
            subscription_status,
            plan_code,
            billing_interval,
            trial_started_at,
            trial_ends_at,
            trial_converted_at,
            trial_expired_at,
            subscription_started_at,
            current_period_started_at,
            current_period_ends_at,
            cancel_at_period_end,
            canceled_at,
            ended_at,
            stripe_customer_id,
            stripe_subscription_id,
            stripe_price_id,
            stripe_product_id,
            payment_status,
            last_payment_at,
            next_payment_at,
            payment_failure_at,
            payment_failure_reason,
            seat_limit,
            connection_limit,
            monthly_explanation_limit,
            monthly_dq_analysis_limit,
            steward_intelligence_enabled,
            governed_regex_execution_enabled,
            governed_sql_execution_enabled,
            policy_auto_execution_enabled,
            is_current,
            created_at,
            updated_at
        FROM `{self.subscriptions_table}`
        WHERE organization_id = @organization_id
        AND is_current = TRUE
        ORDER BY updated_at DESC
        LIMIT 1
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                ),
            ]
        )

        rows = self.client.query(
            query,
            job_config=job_config,
        ).result()

        row = next(
            iter(rows),
            None,
        )

        if row is None:
            return None

        result = dict(
            row.items()
        )

        returned_organization_id = str(
            result.get("organization_id")
            or ""
        ).strip()

        if (
            returned_organization_id
            != effective_organization_id
        ):
            raise RuntimeError(
                "Subscription query returned a "
                "different organization than the "
                "authenticated tenant."
            )

        return result

    def get_current_plan_for_organization(
        self,
        *,
        organization_id: str,
    ) -> str | None:
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        query = f"""
        SELECT
            plan_code
        FROM `{self.subscriptions_table}`
        WHERE organization_id = @organization_id
        AND is_current = TRUE
        AND (UPPER(subscription_status) = 'ACTIVE'
        OR (
            UPPER(subscription_status) IN (
                'TRIAL',
                'TRIALING'
            )
            AND trial_ends_at IS NOT NULL
            AND trial_ends_at > CURRENT_TIMESTAMP()
            AND trial_expired_at IS NULL
        )
        )
        AND plan_code IS NOT NULL
        ORDER BY updated_at DESC
        LIMIT 1
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                ),
            ]
        )

        rows = self.client.query(
            query,
            job_config=job_config,
        ).result()

        row = next(iter(rows), None)

        if row is None:
            return None

        plan_code = str(
            row["plan_code"] or ""
        ).strip().upper()

        return plan_code or None
    
    def get_plan_entitlement(
        self,
        *,
        plan_code: str,
    ) -> dict[str, Any] | None:
        normalized_plan_code = str(
            plan_code or ""
        ).strip().upper()

        if not normalized_plan_code:
            raise ValueError("plan_code is required.")

        query = f"""
        SELECT
            plan_code,
            max_users,
            max_domains,
            max_connections,
            all_domains_enabled,
            monthly_price_usd,
            annual_price_usd,
            is_active
        FROM `{self.plan_entitlements_table}`
        WHERE UPPER(plan_code) = @plan_code
        AND is_active = TRUE
        LIMIT 1
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "plan_code",
                    "STRING",
                    normalized_plan_code,
                )
            ]
        )

        rows = self.client.query(
            query,
            job_config=job_config,
        ).result()

        row = next(iter(rows), None)

        return dict(row.items()) if row else None

    def count_occupied_seats(
        self,
        *,
        organization_id: str,
    ) -> int:
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        query = f"""
        SELECT
            COUNT(DISTINCT customer_user_id) AS occupied_seats
        FROM `{self.customer_users_table}`
        WHERE organization_id = @organization_id
        AND (
            is_active = TRUE
            OR UPPER(
                COALESCE(
                    invitation_status,
                    ''
                )
            ) IN (
                'PENDING',
                'INVITED'
            )
        )
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                ),
            ]
        )

        rows = self.client.query(
            query,
            job_config=job_config,
        ).result()

        row = next(iter(rows), None)

        return int(
            row["occupied_seats"]
            if row
            else 0
        )

    def get_customer_user_by_email(
        self,
        *,
        organization_id: str,
        email: str,
    ) -> dict[str, Any] | None:
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        normalized_email = str(
            email or ""
        ).strip().lower()

        if not normalized_email:
            raise ValueError(
                "email is required for seat resolution."
            )

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
        WHERE organization_id = @organization_id
        AND LOWER(email) = @email
        ORDER BY updated_at DESC
        LIMIT 1
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                ),
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

        if row is None:
            return None

        return dict(row.items())

    def count_enabled_domains(
        self,
        *,
        organization_id: str,
    ) -> int:
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        query = f"""
        SELECT
            COUNT(DISTINCT domain) AS enabled_domain_count
        FROM `{self.organization_domain_entitlements_table}`
        WHERE organization_id = @organization_id
        AND is_enabled = TRUE
        AND effective_from <= CURRENT_TIMESTAMP()
        AND (
            effective_to IS NULL
            OR effective_to > CURRENT_TIMESTAMP()
        )
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                )
            ]
        )

        rows = self.client.query(
            query,
            job_config=job_config,
        ).result()

        row = next(iter(rows), None)

        return int(
            row["enabled_domain_count"]
            if row
            else 0
        )


    def grant_domain_entitlement(
        self,
        *,
        organization_id: str,
        domain: str,
        plan_code: str,
        granted_by: str,
        entitlement_source: str = "PLAN",
    ) -> dict[str, Any]:
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        effective_domain = self._require_domain(
            domain
        )

        normalized_plan_code = str(
            plan_code or ""
        ).strip().upper()

        normalized_source = str(
            entitlement_source or "PLAN"
        ).strip().upper()

        normalized_granted_by = str(
            granted_by or "system"
        ).strip()

        query = f"""
        MERGE `{self.organization_domain_entitlements_table}` T
        USING (
            SELECT
                @organization_id AS organization_id,
                @domain AS domain
        ) S
        ON T.organization_id = S.organization_id
        AND T.domain = S.domain

        WHEN MATCHED THEN
            UPDATE SET
                is_enabled = TRUE,
                plan_code = @plan_code,
                entitlement_source = @entitlement_source,
                effective_from = CURRENT_TIMESTAMP(),
                effective_to = NULL,
                updated_by = @granted_by,
                updated_at = CURRENT_TIMESTAMP()

        WHEN NOT MATCHED THEN
            INSERT (
                organization_id,
                domain,
                is_enabled,
                plan_code,
                entitlement_source,
                effective_from,
                effective_to,
                created_by,
                updated_by,
                created_at,
                updated_at
            )
            VALUES (
                @organization_id,
                @domain,
                TRUE,
                @plan_code,
                @entitlement_source,
                CURRENT_TIMESTAMP(),
                NULL,
                @granted_by,
                @granted_by,
                CURRENT_TIMESTAMP(),
                CURRENT_TIMESTAMP()
            )
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "domain",
                    "STRING",
                    effective_domain,
                ),
                bigquery.ScalarQueryParameter(
                    "plan_code",
                    "STRING",
                    normalized_plan_code,
                ),
                bigquery.ScalarQueryParameter(
                    "entitlement_source",
                    "STRING",
                    normalized_source,
                ),
                bigquery.ScalarQueryParameter(
                    "granted_by",
                    "STRING",
                    normalized_granted_by,
                ),
            ]
        )

        self.client.query(
            query,
            job_config=job_config,
        ).result()

        entitlement = self.get_domain_entitlement(
            organization_id=effective_organization_id,
            domain=effective_domain,
        )

        if entitlement is None:
            raise RuntimeError(
                "Domain entitlement grant did not persist."
            )

        return entitlement

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
                    "entitlement resolution."
                )

            if not normalized.startswith("org_"):
                raise ValueError(
                    "organization_id must use the "
                    "org_ identifier standard."
                )

            return normalized

    @staticmethod
    def _require_domain(
            domain: str,
        ) -> str:
            normalized = str(
                domain or ""
            ).strip().upper()

            if not normalized:
                raise ValueError(
                    "domain is required for "
                    "entitlement resolution."
                )

            allowed_domains = {
                "CUSTOMER",
                "SUPPLIER",
                "PRODUCT",
                "PROVIDER",
                "PATIENT",
                "BANKING",
                "LOCATION",
                "ORGANIZATION",
                "OTHER",
            }

            if normalized not in allowed_domains:
                raise ValueError(
                    f"Unsupported entitlement domain: "
                    f"{normalized}"
                )

            return normalized

    def get_domain_entitlement(
            self,
            *,
            organization_id: str,
            domain: str,
        ) -> dict[str, Any] | None:
            effective_organization_id = (
                self._require_organization_id(
                    organization_id
                )
            )

            effective_domain = self._require_domain(
                domain
            )

            query = f"""
            SELECT
                organization_id,
                domain,
                is_enabled,
                plan_code,
                entitlement_source,
                effective_from,
                effective_to,
                created_by,
                updated_by,
                created_at,
                updated_at
            FROM `{self.organization_domain_entitlements_table}`
            WHERE organization_id = @organization_id
            AND domain = @domain
            AND is_enabled = TRUE
            AND effective_from <= CURRENT_TIMESTAMP()
            AND (
                effective_to IS NULL
                OR effective_to > CURRENT_TIMESTAMP()
            )
            ORDER BY updated_at DESC
            LIMIT 1
            """

            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter(
                        "organization_id",
                        "STRING",
                        effective_organization_id,
                    ),
                    bigquery.ScalarQueryParameter(
                        "domain",
                        "STRING",
                        effective_domain,
                    ),
                ]
            )

            rows = self.client.query(
                query,
                job_config=job_config,
            ).result()

            row = next(iter(rows), None)

            if row is None:
                return None

            result = dict(row.items())

            returned_organization_id = str(
                result.get("organization_id") or ""
            ).strip()

            if (
                returned_organization_id
                != effective_organization_id
            ):
                raise RuntimeError(
                    "Entitlement query returned a "
                    "different organization than the "
                    "authenticated tenant."
                )

            return result


    def list_enabled_domains(
            self,
            *,
            organization_id: str,
        ) -> list[dict[str, Any]]:
            effective_organization_id = (
                self._require_organization_id(
                    organization_id
                )
            )

            query = f"""
            SELECT
                organization_id,
                domain,
                is_enabled,
                plan_code,
                entitlement_source,
                effective_from,
                effective_to
            FROM `{self.organization_domain_entitlements_table}`
            WHERE organization_id = @organization_id
            AND is_enabled = TRUE
            AND effective_from <= CURRENT_TIMESTAMP()
            AND (
                effective_to IS NULL
                OR effective_to > CURRENT_TIMESTAMP()
            )
            ORDER BY domain
            """

            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter(
                        "organization_id",
                        "STRING",
                        effective_organization_id,
                    ),
                ]
            )

            rows = self.client.query(
                query,
                job_config=job_config,
            ).result()

            return [
                dict(row.items())
                for row in rows
            ]
        
        