from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any


from google.cloud import bigquery


class BillingRepository:
    PLAN_EXECUTION_ENTITLEMENTS: dict[str, dict[str, bool]] = {
        "FREE_TRIAL": {
            "steward_intelligence_enabled": False,
            "governed_regex_execution_enabled": False,
            "governed_sql_execution_enabled": False,
            "policy_auto_execution_enabled": False,
        },
        "ENTERPRISE_TRIAL": {
            "steward_intelligence_enabled": False,
            "governed_regex_execution_enabled": False,
            "governed_sql_execution_enabled": False,
            "policy_auto_execution_enabled": False,
        },
        "PROFESSIONAL": {
            "steward_intelligence_enabled": False,
            "governed_regex_execution_enabled": False,
            "governed_sql_execution_enabled": False,
            "policy_auto_execution_enabled": False,
        },
        "STEWARD_OPERATIONS": {
            "steward_intelligence_enabled": False,
            "governed_regex_execution_enabled": False,
            "governed_sql_execution_enabled": False,
            "policy_auto_execution_enabled": False,
        },
        "GOVERNANCE_INTELLIGENCE": {
            "steward_intelligence_enabled": False,
            "governed_regex_execution_enabled": False,
            "governed_sql_execution_enabled": False,
            "policy_auto_execution_enabled": False,
        },
        "ENTERPRISE_GOVERNANCE": {
            "steward_intelligence_enabled": True,
            "governed_regex_execution_enabled": True,
            "governed_sql_execution_enabled": True,
            "policy_auto_execution_enabled": False,
        },
        "INTERNAL": {
            "steward_intelligence_enabled": True,
            "governed_regex_execution_enabled": True,
            "governed_sql_execution_enabled": True,
            "policy_auto_execution_enabled": False,
        },
        "FOUNDER": {
            "steward_intelligence_enabled": True,
            "governed_regex_execution_enabled": True,
            "governed_sql_execution_enabled": True,
            "policy_auto_execution_enabled": False,
        },
        "ADMIN": {
            "steward_intelligence_enabled": True,
            "governed_regex_execution_enabled": True,
            "governed_sql_execution_enabled": True,
            "policy_auto_execution_enabled": False,
        },
    }

    @classmethod
    def _execution_entitlements_for_plan(
        cls,
        plan_code: str,
    ) -> dict[str, bool]:
        normalized_plan_code = str(plan_code or "").strip().upper()

        entitlements = cls.PLAN_EXECUTION_ENTITLEMENTS.get(
            normalized_plan_code
        )

        if entitlements is None:
            return {
                "steward_intelligence_enabled": False,
                "governed_regex_execution_enabled": False,
                "governed_sql_execution_enabled": False,
                "policy_auto_execution_enabled": False,
            }

        return dict(entitlements)

    def __init__(
        self,
        *,
        client: bigquery.Client | None = None,
    ) -> None:
        self.project_id = (
            os.getenv("BILLING_PROJECT_ID")
            or os.getenv("PROJECT_ID")
            or "api-project-503305938314"
        )

        self.dataset_id = (
            os.getenv("BILLING_DATASET_ID")
            or "ai_data_steward_mvp"
        )

        customers_table_name = os.getenv(
            "BILLING_CUSTOMERS_TABLE",
            "CUSTOMERS",
        )

        subscriptions_table_name = os.getenv(
            "BILLING_SUBSCRIPTIONS_TABLE",
            "CUSTOMER_SUBSCRIPTIONS",
        )

        self.customers_table = (
            f"{self.project_id}."
            f"{self.dataset_id}."
            f"{customers_table_name}"
        )

        self.subscriptions_table = (
            f"{self.project_id}."
            f"{self.dataset_id}."
            f"{subscriptions_table_name}"
        )

        self.client = client or bigquery.Client(
            project=self.project_id
        )

    @staticmethod
    def _require_organization_id(
        organization_id: str,
    ) -> str:
        normalized = str(organization_id or "").strip()

        if not normalized:
            raise ValueError(
                "organization_id is required for tenant-isolated billing operations."
            )

        return normalized

    @staticmethod
    def _stripe_timestamp_to_datetime(
        value: int | None,
    ) -> datetime | None:
        if value is None:
            return None

        return datetime.fromtimestamp(
            value,
            tz=timezone.utc,
        )

    @staticmethod
    def _normalize_email(
        email: str,
    ) -> str:
        normalized = str(email or "").strip().lower()

        if not normalized:
            raise ValueError(
                "email is required for billing operations."
            )

        return normalized

    def get_customer_by_email(
        self,
        *,
        email: str,
        organization_id: str,
    ) -> dict[str, Any] | None:
        normalized_email = self._normalize_email(email)
        effective_organization_id = self._require_organization_id(
            organization_id
        )

        sql = f"""
        SELECT
            customer_id,
            organization_id,
            organization_name,
            organization_slug,
            owner_user_id,
            owner_email,
            billing_email,
            customer_status,
            customer_type,
            default_plan_code,
            default_environment,
            trial_eligible,
            onboarding_status,
            is_active,
            created_at,
            updated_at
        FROM `{self.customers_table}`
        WHERE
            LOWER(owner_email) = @email
            AND organization_id = @organization_id
            AND is_active = TRUE
        ORDER BY updated_at DESC
        LIMIT 1
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "email",
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

        return dict(rows[0].items())

    def sync_subscription_from_stripe(
        self,
        *,
        stripe_customer_id: str,
        stripe_subscription_id: str,
        stripe_price_id: str,
        stripe_product_id: str | None,
        plan_code: str,
        billing_interval: str,
        subscription_status: str,
        current_period_started_at: datetime | None,
        current_period_ends_at: datetime | None,
        cancel_at_period_end: bool,
        canceled_at: datetime | None,
        currency: str | None,
        unit_amount_cents: int | None,
        payment_status: str | None = None,
        updated_by: str = "stripe_webhook",
    ) -> None:
        normalized_subscription_id = str(
            stripe_subscription_id or ""
        ).strip()

        normalized_customer_id = str(
            stripe_customer_id or ""
        ).strip()

        normalized_price_id = str(
            stripe_price_id or ""
        ).strip()

        normalized_plan_code = str(
            plan_code or ""
        ).strip().upper()

        normalized_billing_interval = str(
            billing_interval or ""
        ).strip().upper()

        normalized_subscription_status = str(
            subscription_status or ""
        ).strip().upper()

        execution_entitlements = self._execution_entitlements_for_plan(
            normalized_plan_code
        )

        if not normalized_subscription_id:
            raise ValueError(
                "stripe_subscription_id is required."
            )

        if not normalized_customer_id:
            raise ValueError(
                "stripe_customer_id is required."
            )

        if not normalized_price_id:
            raise ValueError(
                "stripe_price_id is required."
            )

        if not normalized_plan_code:
            raise ValueError(
                "plan_code is required."
            )

        sql = f"""
        UPDATE `{self.subscriptions_table}`
        SET
            subscription_status =
                @subscription_status,

            plan_code =
                @plan_code,

            billing_interval =
                @billing_interval,

            stripe_customer_id =
                @stripe_customer_id,

            stripe_subscription_id =
                @stripe_subscription_id,

            stripe_price_id =
                @stripe_price_id,

            stripe_product_id =
                @stripe_product_id,

            current_period_started_at =
                @current_period_started_at,

            current_period_ends_at =
                @current_period_ends_at,

            next_payment_at =
                @current_period_ends_at,

            cancel_at_period_end =
                @cancel_at_period_end,

            canceled_at =
                @canceled_at,

            currency =
                @currency,

            unit_amount_cents =
                @unit_amount_cents,

            steward_intelligence_enabled =
                @steward_intelligence_enabled,

            governed_regex_execution_enabled =
                @governed_regex_execution_enabled,

            governed_sql_execution_enabled =
                @governed_sql_execution_enabled,

            policy_auto_execution_enabled =
                @policy_auto_execution_enabled,

            payment_status = COALESCE(
                @payment_status,
                payment_status
            ),

            updated_at =
                CURRENT_TIMESTAMP(),

            updated_by =
                @updated_by

        WHERE stripe_subscription_id =
            @stripe_subscription_id
        AND is_current = TRUE
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "subscription_status",
                    "STRING",
                    normalized_subscription_status,
                ),
                bigquery.ScalarQueryParameter(
                    "plan_code",
                    "STRING",
                    normalized_plan_code,
                ),
                bigquery.ScalarQueryParameter(
                    "billing_interval",
                    "STRING",
                    normalized_billing_interval,
                ),
                bigquery.ScalarQueryParameter(
                    "stripe_customer_id",
                    "STRING",
                    normalized_customer_id,
                ),
                bigquery.ScalarQueryParameter(
                    "stripe_subscription_id",
                    "STRING",
                    normalized_subscription_id,
                ),
                bigquery.ScalarQueryParameter(
                    "stripe_price_id",
                    "STRING",
                    normalized_price_id,
                ),
                bigquery.ScalarQueryParameter(
                    "stripe_product_id",
                    "STRING",
                    stripe_product_id,
                ),
                bigquery.ScalarQueryParameter(
                    "current_period_started_at",
                    "TIMESTAMP",
                    current_period_started_at,
                ),
                bigquery.ScalarQueryParameter(
                    "current_period_ends_at",
                    "TIMESTAMP",
                    current_period_ends_at,
                ),
                bigquery.ScalarQueryParameter(
                    "cancel_at_period_end",
                    "BOOL",
                    cancel_at_period_end,
                ),
                bigquery.ScalarQueryParameter(
                    "canceled_at",
                    "TIMESTAMP",
                    canceled_at,
                ),
                bigquery.ScalarQueryParameter(
                    "currency",
                    "STRING",
                    currency,
                ),
                bigquery.ScalarQueryParameter(
                    "unit_amount_cents",
                    "INT64",
                    unit_amount_cents,
                ),
                bigquery.ScalarQueryParameter(
                    "steward_intelligence_enabled",
                    "BOOL",
                    execution_entitlements[
                        "steward_intelligence_enabled"
                    ],
                ),
                bigquery.ScalarQueryParameter(
                    "governed_regex_execution_enabled",
                    "BOOL",
                    execution_entitlements[
                        "governed_regex_execution_enabled"
                    ],
                ),
                bigquery.ScalarQueryParameter(
                    "governed_sql_execution_enabled",
                    "BOOL",
                    execution_entitlements[
                        "governed_sql_execution_enabled"
                    ],
                ),
                bigquery.ScalarQueryParameter(
                    "policy_auto_execution_enabled",
                    "BOOL",
                    execution_entitlements[
                        "policy_auto_execution_enabled"
                    ],
                ),
                bigquery.ScalarQueryParameter(
                    "payment_status",
                    "STRING",
                    payment_status,
                ),
                bigquery.ScalarQueryParameter(
                    "updated_by",
                    "STRING",
                    updated_by,
                ),
            ]
        )

        query_job = self.client.query(
            sql,
            job_config=job_config,
        )

        query_job.result()

        affected_rows = (
            query_job.num_dml_affected_rows
        )

        if affected_rows != 1:
            raise RuntimeError(
                "Stripe subscription sync did not "
                "update exactly one current billing row. "
                f"stripe_subscription_id="
                f"{normalized_subscription_id}, "
                f"rows_updated={affected_rows}"
            )

    def get_current_subscription(
        self,
        *,
        customer_id: str,
        organization_id: str,
    ) -> dict[str, Any] | None:
        effective_organization_id = self._require_organization_id(
            organization_id
        )
        normalized_customer_id = str(customer_id or "").strip()

        if not normalized_customer_id:
            raise ValueError(
                "customer_id is required for billing operations."
            )

        sql = f"""
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
            stripe_checkout_session_id,

            currency,
            unit_amount_cents,

            seat_limit,
            connection_limit,
            monthly_explanation_limit,
            monthly_dq_analysis_limit,
            steward_intelligence_enabled,
            governed_regex_execution_enabled,
            governed_sql_execution_enabled,
            policy_auto_execution_enabled,

            payment_status,
            last_payment_at,
            next_payment_at,
            payment_failure_at,
            payment_failure_reason,

            is_current,
            created_at,
            updated_at
        FROM `{self.subscriptions_table}`
        WHERE
            customer_id = @customer_id
            AND organization_id = @organization_id
            AND is_current = TRUE
        ORDER BY updated_at DESC
        LIMIT 1
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "customer_id",
                    "STRING",
                    normalized_customer_id,
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

    def get_current_subscription_by_organization_id(
        self,
        *,
        organization_id: str,
    ) -> dict[str, Any] | None:
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        sql = f"""
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
            stripe_checkout_session_id,

            currency,
            unit_amount_cents,

            seat_limit,
            connection_limit,
            monthly_explanation_limit,
            monthly_dq_analysis_limit,
            steward_intelligence_enabled,
            governed_regex_execution_enabled,
            governed_sql_execution_enabled,
            policy_auto_execution_enabled,

            payment_status,
            last_payment_at,
            next_payment_at,
            payment_failure_at,
            payment_failure_reason,

            is_current,
            created_at,
            updated_at
        FROM `{self.subscriptions_table}`
        WHERE
            organization_id = @organization_id
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

        rows = list(
            self.client.query(
                sql,
                job_config=job_config,
            ).result()
        )

        if not rows:
            return None

        subscription = dict(rows[0].items())

        resolved_organization_id = str(
            subscription.get("organization_id") or ""
        ).strip()

        if (
            resolved_organization_id
            != effective_organization_id
        ):
            raise RuntimeError(
                "Billing subscription organization does not "
                "match the authenticated organization."
            )

        return subscription

    def activate_subscription_from_checkout(
        self,
        *,
        customer_id: str,
        organization_id: str,
        stripe_customer_id: str,
        stripe_subscription_id: str,
        stripe_checkout_session_id: str,
        stripe_price_id: str | None,
        plan_code: str,
        billing_interval: str,
        current_period_started_at: datetime | None,
        current_period_ends_at: datetime | None,
        updated_by: str,
    ) -> None:
        effective_organization_id = self._require_organization_id(
        organization_id
    )

        normalized_plan_code = str(
            plan_code or ""
        ).strip().upper()

        normalized_billing_interval = str(
            billing_interval or ""
        ).strip().upper()

        normalized_customer_id = str(
            customer_id or ""
        ).strip()

        execution_entitlements = self._execution_entitlements_for_plan(
            normalized_plan_code
        )

        if not normalized_customer_id:
            raise ValueError(
                "customer_id is required for subscription activation."
            )

        sql = f"""
        UPDATE `{self.subscriptions_table}`
        SET
            subscription_status = 'ACTIVE',
            plan_code = @plan_code,
            billing_interval = @billing_interval,

            trial_converted_at = CURRENT_TIMESTAMP(),
            subscription_started_at = COALESCE(
                subscription_started_at,
                CURRENT_TIMESTAMP()
            ),

            current_period_started_at = @current_period_started_at,
            current_period_ends_at = @current_period_ends_at,

            stripe_customer_id = @stripe_customer_id,
            stripe_subscription_id = @stripe_subscription_id,
            stripe_checkout_session_id = @stripe_checkout_session_id,
            stripe_price_id = COALESCE(
                @stripe_price_id,
                stripe_price_id
            ),

            payment_status = 'PAID',
            last_payment_at = CURRENT_TIMESTAMP(),

            steward_intelligence_enabled =
                @steward_intelligence_enabled,

            governed_regex_execution_enabled =
                @governed_regex_execution_enabled,

            governed_sql_execution_enabled =
                @governed_sql_execution_enabled,

            policy_auto_execution_enabled =
                @policy_auto_execution_enabled,

            updated_at = CURRENT_TIMESTAMP(),
            updated_by = @updated_by
        WHERE
            customer_id = @customer_id
            AND organization_id = @organization_id
            AND is_current = TRUE
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "customer_id",
                    "STRING",
                    normalized_customer_id,
                ),
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                ),

                bigquery.ScalarQueryParameter(
                    "plan_code",
                    "STRING",
                    normalized_plan_code,
                ),
                bigquery.ScalarQueryParameter(
                    "billing_interval",
                    "STRING",
                    normalized_billing_interval,
                ),
                bigquery.ScalarQueryParameter(
                    "stripe_customer_id",
                    "STRING",
                    stripe_customer_id,
                ),
                bigquery.ScalarQueryParameter(
                    "stripe_subscription_id",
                    "STRING",
                    stripe_subscription_id,
                ),
                bigquery.ScalarQueryParameter(
                    "stripe_checkout_session_id",
                    "STRING",
                    stripe_checkout_session_id,
                ),
                bigquery.ScalarQueryParameter(
                    "stripe_price_id",
                    "STRING",
                    stripe_price_id,
                ),
                bigquery.ScalarQueryParameter(
                    "current_period_started_at",
                    "TIMESTAMP",
                    current_period_started_at,
                ),
                bigquery.ScalarQueryParameter(
                    "current_period_ends_at",
                    "TIMESTAMP",
                    current_period_ends_at,
                ),
                bigquery.ScalarQueryParameter(
                    "steward_intelligence_enabled",
                    "BOOL",
                    execution_entitlements[
                        "steward_intelligence_enabled"
                    ],
                ),
                bigquery.ScalarQueryParameter(
                    "governed_regex_execution_enabled",
                    "BOOL",
                    execution_entitlements[
                        "governed_regex_execution_enabled"
                    ],
                ),
                bigquery.ScalarQueryParameter(
                    "governed_sql_execution_enabled",
                    "BOOL",
                    execution_entitlements[
                        "governed_sql_execution_enabled"
                    ],
                ),
                bigquery.ScalarQueryParameter(
                    "policy_auto_execution_enabled",
                    "BOOL",
                    execution_entitlements[
                        "policy_auto_execution_enabled"
                    ],
                ),
                bigquery.ScalarQueryParameter(
                    "updated_by",
                    "STRING",
                    updated_by,
                ),
            ]
        )

        query_job = self.client.query(
            sql,
            job_config=job_config,
        )
        query_job.result()

        affected_rows = query_job.num_dml_affected_rows

        if affected_rows != 1:
            raise RuntimeError(
                "Subscription activation did not affect exactly one "
                "row for the authenticated organization. "
                f"customer_id={normalized_customer_id}, "
                f"organization_id={effective_organization_id}, "
                f"rows_updated={affected_rows}"
            )

    def get_billing_record_by_organization_id(
        self,
        *,
        organization_id: str,
    ) -> dict[str, Any] | None:
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        # Commercial customer is optional during trial.
        customer = self.get_customer_by_organization_id(
            organization_id=effective_organization_id,
        )

        # Subscription is authoritative for trial/access status.
        subscription = (
            self.get_current_subscription_by_organization_id(
                organization_id=effective_organization_id,
            )
        )

        if subscription is None:
            return None

        # If a commercial customer exists, validate the relationship.
        if customer is not None:
            customer_id = str(
                customer.get("customer_id") or ""
            ).strip()

            if not customer_id:
                raise RuntimeError(
                    "Billing customer record is missing customer_id."
                )

            subscription_customer_id = str(
                subscription.get("customer_id") or ""
            ).strip()

            if (
                subscription_customer_id
                and subscription_customer_id != customer_id
            ):
                raise RuntimeError(
                    "Billing customer and subscription customer "
                    "IDs do not match."
                )

        return {
            "customer": customer,
            "subscription": subscription,
        }

    def get_billing_record_by_email(
        self,
        *,
        email: str,
        organization_id: str,
    ) -> dict[str, Any] | None:
        effective_organization_id = self._require_organization_id(
            organization_id
        )

        customer = self.get_customer_by_email(
            email=email,
            organization_id=effective_organization_id,
        )

        if customer is None:
            return None

        customer_id = str(
            customer["customer_id"]
        ).strip()

        subscription = self.get_current_subscription(
            customer_id=customer_id,
            organization_id=effective_organization_id,
        )

        if subscription is None:
            return {
                "customer": customer,
                "subscription": None,
            }

        customer_organization_id = str(
            customer["organization_id"]
        ).strip()

        subscription_organization_id = str(
            subscription["organization_id"]
        ).strip()

        if (
            customer_organization_id
            != effective_organization_id
        ):
            raise RuntimeError(
                "Billing customer organization does not match "
                "the authenticated organization."
            )

        if (
            subscription_organization_id
            != effective_organization_id
        ):
            raise RuntimeError(
                "Billing subscription organization does not match "
                "the authenticated organization."
            )

        if (
            str(subscription["customer_id"]).strip()
            != customer_id
        ):
            raise RuntimeError(
                "Billing customer and subscription customer IDs "
                "do not match."
            )

        return {
            "customer": customer,
            "subscription": subscription,
        }

    def get_customer_by_organization_id(
        self,
        *,
        organization_id: str,
    ) -> dict[str, Any] | None:
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        sql = f"""
        SELECT
            customer_id,
            organization_id,
            organization_name,
            organization_slug,
            owner_user_id,
            owner_email,
            billing_email,
            customer_status,
            customer_type,
            default_plan_code,
            default_environment,
            trial_eligible,
            onboarding_status,
            is_active,
            created_at,
            updated_at
        FROM `{self.customers_table}`
        WHERE
            organization_id = @organization_id
            AND is_active = TRUE
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

        rows = list(
            self.client.query(
                sql,
                job_config=job_config,
            ).result()
        )

        if not rows:
            return None

        customer = dict(rows[0].items())

        customer_organization_id = str(
            customer.get("organization_id") or ""
        ).strip()

        if (
            customer_organization_id
            != effective_organization_id
        ):
            raise RuntimeError(
                "Billing customer organization does not "
                "match the requested organization."
            )

        return customer