from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status

from app.api.billing_schemas import BillingStatusResponse
from app.repositories.billing_repository import BillingRepository


class BillingService:
    def __init__(
        self,
        repository: BillingRepository,
    ) -> None:
        self.repository = repository

    def get_billing_status(
        self,
        *,
        current_user_email: str,
        organization_id: str,
        customer_id: str | None = None,
    ) -> BillingStatusResponse:
        """
        Return billing/access status for the authenticated organization.

        organization_id is the authoritative tenant boundary.

        A commercial customer record and customer_id are optional while an
        organization is in a free trial. Once a customer exists, customer and
        subscription ownership are validated as defense-in-depth checks.
        """
        # Validate authenticated identity even though billing resolution is
        # organization-first.
        self._require_email(current_user_email)

        effective_organization_id = self._require_organization_id(
            organization_id
        )

        expected_customer_id = self._as_optional_string(
            customer_id
        )

        billing_record = (
            self.repository.get_billing_record_by_organization_id(
                organization_id=effective_organization_id,
            )
        )

        if billing_record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    "No current trial or subscription was found for "
                    "the authenticated organization."
                ),
            )

        customer = billing_record.get("customer")
        subscription = billing_record.get("subscription")

        if subscription is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "The organization does not have a current "
                    "trial or subscription record."
                ),
            )

        subscription_organization_id = str(
            subscription.get("organization_id") or ""
        ).strip()

        if (
            subscription_organization_id
            != effective_organization_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Billing subscription does not belong to "
                    "the authenticated organization."
                ),
            )

        resolved_customer_id = (
            str(customer.get("customer_id") or "").strip()
            if customer is not None
            else ""
        )

        subscription_customer_id = str(
            subscription.get("customer_id") or ""
        ).strip()

        if customer is not None:
            customer_organization_id = str(
                customer.get("organization_id") or ""
            ).strip()

            if (
                customer_organization_id
                != effective_organization_id
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "Billing customer does not belong to "
                        "the authenticated organization."
                    ),
                )

            if not resolved_customer_id:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        "Billing customer record is missing "
                        "customer_id."
                    ),
                )

            if (
                expected_customer_id
                and resolved_customer_id
                != expected_customer_id
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "Billing customer does not match the "
                        "authenticated customer."
                    ),
                )

        # During trial, both customer IDs may legitimately be absent.
        # Once both exist, they must agree.
        if (
            resolved_customer_id
            and subscription_customer_id
            and subscription_customer_id
            != resolved_customer_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Billing subscription does not belong to "
                    "the authenticated customer."
                ),
            )

        effective_customer_id = (
            resolved_customer_id
            or subscription_customer_id
            or None
        )

        now = datetime.now(timezone.utc)

        trial_started_at = self._as_utc_datetime(
            subscription.get("trial_started_at")
        )
        trial_ends_at = self._as_utc_datetime(
            subscription.get("trial_ends_at")
        )
        subscription_started_at = self._as_utc_datetime(
            subscription.get("subscription_started_at")
        )
        current_period_started_at = self._as_utc_datetime(
            subscription.get("current_period_started_at")
        )
        current_period_ends_at = self._as_utc_datetime(
            subscription.get("current_period_ends_at")
        )
        canceled_at = self._as_utc_datetime(
            subscription.get("canceled_at")
        )

        raw_subscription_status = self._normalize_code(
            subscription.get("subscription_status"),
            default="EXPIRED",
        )

        # Normalize legacy/current trial naming to the API contract.
        if raw_subscription_status == "TRIAL":
            raw_subscription_status = "TRIALING"

        plan_code = self._normalize_code(
            subscription.get("plan_code"),
            default="FREE_TRIAL",
        )
        payment_status = self._normalize_code(
            subscription.get("payment_status"),
            default="NOT_REQUIRED",
        )

        plan_name = str(
            subscription.get("plan_name")
            or self._get_plan_name(plan_code)
        ).strip()

        billing_interval = self._as_optional_string(
            subscription.get("billing_interval")
        )
        currency = self._normalize_code(
            subscription.get("currency"),
            default="USD",
        )

        unit_amount_cents = self._as_optional_int(
            subscription.get("unit_amount_cents")
        )
        if unit_amount_cents is None:
            unit_amount_cents = self._default_unit_amount_cents(
                plan_code
            )

        cancel_at_period_end = self._as_bool(
            subscription.get("cancel_at_period_end"),
            default=False,
        )
        is_current = self._as_bool(
            subscription.get("is_current"),
            default=True,
        )

        stripe_customer_id = self._as_optional_string(
            subscription.get("stripe_customer_id")
        )
        stripe_subscription_id = self._as_optional_string(
            subscription.get("stripe_subscription_id")
        )
        stripe_price_id = self._as_optional_string(
            subscription.get("stripe_price_id")
        )

        trial_expired = bool(
            trial_ends_at is not None and now >= trial_ends_at
        )

        remaining_seconds = 0
        if (
            raw_subscription_status == "TRIALING"
            and trial_ends_at is not None
            and trial_ends_at > now
        ):
            remaining_seconds = max(
                0,
                int((trial_ends_at - now).total_seconds()),
            )

        days_remaining = (
            math.ceil(remaining_seconds / 86_400)
            if remaining_seconds > 0
            else 0
        )
        hours_remaining = (
            math.ceil(remaining_seconds / 3_600)
            if remaining_seconds > 0
            else 0
        )

        effective_subscription_status = raw_subscription_status
        if (
            raw_subscription_status == "TRIALING"
            and trial_expired
        ):
            effective_subscription_status = "EXPIRED"

        is_paid = self._calculate_is_paid(
            subscription_status=effective_subscription_status,
            payment_status=payment_status,
            plan_code=plan_code,
            stripe_subscription_id=stripe_subscription_id,
        )

        has_product_access, access_reason = self._calculate_access(
            subscription_status=effective_subscription_status,
            plan_code=plan_code,
            trial_expired=trial_expired,
            payment_status=payment_status,
        )

        organization_name = (
            str(customer.get("organization_name") or "").strip()
            if customer is not None
            else ""
        )

        # A trial tenant may not have a commercial CUSTOMER row yet.
        # Keep the field populated without inventing a commercial customer.
        if not organization_name:
            organization_name = effective_organization_id

        return BillingStatusResponse(
            customer_id=effective_customer_id,
            organization_id=effective_organization_id,
            organization_name=organization_name,
            subscription_record_id=str(
                subscription["subscription_record_id"]
            ),
            subscription_status=effective_subscription_status,
            plan_code=plan_code,
            plan_name=plan_name,
            billing_interval=billing_interval,
            currency=currency,
            unit_amount_cents=unit_amount_cents,
            trial_started_at=trial_started_at,
            trial_ends_at=trial_ends_at,
            subscription_started_at=subscription_started_at,
            current_period_started_at=current_period_started_at,
            current_period_ends_at=current_period_ends_at,
            cancel_at_period_end=cancel_at_period_end,
            canceled_at=canceled_at,
            days_remaining=days_remaining,
            hours_remaining=hours_remaining,
            trial_expired=trial_expired,
            has_product_access=has_product_access,
            access_reason=access_reason,
            payment_status=payment_status,
            is_paid=is_paid,
            stripe_customer_id=stripe_customer_id,
            stripe_subscription_id=stripe_subscription_id,
            stripe_price_id=stripe_price_id,
            seat_limit=self._as_optional_int(
                subscription.get("seat_limit")
            ),
            connection_limit=self._as_optional_int(
                subscription.get("connection_limit")
            ),
            monthly_explanation_limit=self._as_optional_int(
                subscription.get("monthly_explanation_limit")
            ),
            monthly_dq_analysis_limit=self._as_optional_int(
                subscription.get("monthly_dq_analysis_limit")
            ),
            steward_intelligence_enabled=self._as_bool(
                subscription.get("steward_intelligence_enabled"),
                default=False,
            ),
            governed_regex_execution_enabled=self._as_bool(
                subscription.get("governed_regex_execution_enabled"),
                default=False,
            ),
            governed_sql_execution_enabled=self._as_bool(
                subscription.get("governed_sql_execution_enabled"),
                default=False,
            ),
            policy_auto_execution_enabled=self._as_bool(
                subscription.get("policy_auto_execution_enabled"),
                default=False,
            ),
            is_current=is_current,
        )

    @staticmethod
    def _require_organization_id(
        organization_id: str,
    ) -> str:
        normalized = str(
            organization_id or ""
        ).strip()

        if not normalized:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "A valid organization is required "
                    "for billing operations."
                ),
            )

        if not normalized.startswith("org_"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "The organization identifier is invalid."
                ),
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
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=(
                    "Authenticated user email is required."
                ),
            )

        return normalized

    @staticmethod
    def _calculate_access(
        *,
        subscription_status: str,
        plan_code: str,
        trial_expired: bool,
        payment_status: str,
    ) -> tuple[bool, str]:
        if plan_code in {"INTERNAL", "FOUNDER", "ADMIN"}:
            return True, "Internal account access is active."

        if subscription_status == "TRIALING":
            if trial_expired:
                return False, "The 14-day free trial has expired."

            return True, "The 14-day free trial is active."

        if payment_status == "FAILED":
            return False, "The most recent payment failed."

        blocked_status_messages = {
            "PAST_DUE": "Payment is past due.",
            "UNPAID": "The subscription has an unpaid balance.",
            "PAUSED": "The subscription is paused.",
            "CANCELED": "The subscription has been canceled.",
            "EXPIRED": "The trial or subscription has expired.",
        }

        blocked_message = blocked_status_messages.get(
            subscription_status
        )
        if blocked_message is not None:
            return False, blocked_message

        if subscription_status == "ACTIVE":
            return True, "The paid subscription is active."

        return False, "A valid subscription is required."

    @staticmethod
    def _calculate_is_paid(
        *,
        subscription_status: str,
        payment_status: str,
        plan_code: str,
        stripe_subscription_id: str | None,
    ) -> bool:
        if plan_code in {
            "INTERNAL",
            "FOUNDER",
            "ADMIN",
            "FREE_TRIAL",
            "ENTERPRISE_TRIAL",
        }:
            return False

        blocked_payment_statuses = {
            "FAILED",
            "UNPAID",
            "PAST_DUE",
            "CANCELED",
        }

        return (
            subscription_status == "ACTIVE"
            and stripe_subscription_id is not None
            and payment_status not in blocked_payment_statuses
        )

    @staticmethod
    def _get_plan_name(plan_code: str) -> str:
        plan_names = {
            "FREE_TRIAL": "Enterprise Trial",
            "ENTERPRISE_TRIAL": "Enterprise Trial",
            "PROFESSIONAL": "Professional Monthly",
            "STEWARD_OPERATIONS": "Steward Operations Edition",
            "GOVERNANCE_INTELLIGENCE": (
                "Governance Intelligence Edition"
            ),
            "ENTERPRISE_GOVERNANCE": (
                "Enterprise Governance Platform"
            ),
            "INTERNAL": "Internal",
            "FOUNDER": "Founder",
            "ADMIN": "Administrator",
        }

        return plan_names.get(
            plan_code,
            plan_code.replace("_", " ").title(),
        )

    @staticmethod
    def _default_unit_amount_cents(
        plan_code: str,
    ) -> int | None:
        prices = {
            "PROFESSIONAL": 49_900,
            "STEWARD_OPERATIONS": 4_500_000,
            "GOVERNANCE_INTELLIGENCE": 7_500_000,
            "ENTERPRISE_GOVERNANCE": 12_000_000,
        }

        return prices.get(plan_code)

    @staticmethod
    def _normalize_code(
        value: Any,
        *,
        default: str,
    ) -> str:
        normalized = str(value or default).strip().upper()
        return normalized or default

    @staticmethod
    def _as_optional_string(
        value: Any,
    ) -> str | None:
        if value is None:
            return None

        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _as_bool(
        value: Any,
        *,
        default: bool = False,
    ) -> bool:
        if value is None:
            return default

        if isinstance(value, bool):
            return value

        if isinstance(value, (int, float)):
            return value != 0

        if isinstance(value, str):
            normalized = value.strip().lower()

            if normalized in {"true", "1", "yes", "y"}:
                return True

            if normalized in {"false", "0", "no", "n", ""}:
                return False

        return default

    @staticmethod
    def _as_optional_int(
        value: Any,
    ) -> int | None:
        if value is None:
            return None

        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_utc_datetime(
        value: Any,
    ) -> datetime | None:
        if value is None:
            return None

        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)

            return value.astimezone(timezone.utc)

        if isinstance(value, str):
            normalized = value.strip()

            if not normalized:
                return None

            normalized = normalized.replace("Z", "+00:00")

            try:
                parsed = datetime.fromisoformat(normalized)
            except ValueError:
                return None

            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)

            return parsed.astimezone(timezone.utc)

        return None

