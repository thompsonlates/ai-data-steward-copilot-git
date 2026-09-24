from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


SubscriptionStatus = Literal[
    "TRIALING",
    "ACTIVE",
    "PAST_DUE",
    "UNPAID",
    "PAUSED",
    "CANCELED",
    "EXPIRED",
]

PaymentStatus = Literal[
    "NOT_REQUIRED",
    "PENDING",
    "PAID",
    "FAILED",
    "REFUNDED",
]


class CheckoutSessionRequest(BaseModel):
    plan_code: str


class CheckoutSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkout_url: str


class CheckoutConfirmationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    subscription_status: SubscriptionStatus
    payment_status: PaymentStatus
    message: str


class BillingStatusResponse(BaseModel):
    """
    Tenant-scoped billing response.

    Tenant isolation itself is enforced by the authenticated route/service/
    repository layers. This schema validates that the response carries a
    non-empty organization_id and customer_id so tenant context cannot be
    silently omitted.
    """

    model_config = ConfigDict(
        extra="ignore",
        str_strip_whitespace=True,
    )

    customer_id: str | None = None
    organization_id: str
    organization_name: str

    subscription_record_id: str
    subscription_status: SubscriptionStatus

    plan_code: str
    plan_name: str

    billing_interval: str | None = None
    currency: str = "USD"
    unit_amount_cents: int | None = None

    trial_started_at: datetime | None = None
    trial_ends_at: datetime | None = None

    subscription_started_at: datetime | None = None
    current_period_started_at: datetime | None = None
    current_period_ends_at: datetime | None = None

    cancel_at_period_end: bool = False
    canceled_at: datetime | None = None

    days_remaining: int
    hours_remaining: int
    trial_expired: bool

    has_product_access: bool
    access_reason: str

    payment_status: PaymentStatus
    is_paid: bool = False

    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    stripe_price_id: str | None = None

    seat_limit: int | None = None
    connection_limit: int | None = None
    monthly_explanation_limit: int | None = None
    monthly_dq_analysis_limit: int | None = None

    steward_intelligence_enabled: bool = False
    governed_regex_execution_enabled: bool = False
    governed_sql_execution_enabled: bool = False
    policy_auto_execution_enabled: bool = False

    is_current: bool

    @field_validator(
        "customer_id",
        "organization_id",
        "organization_name",
        "subscription_record_id",
        "plan_code",
        "plan_name",
    )
    @classmethod
    def _require_non_empty_identity_fields(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError(
                "Tenant and billing identity fields cannot be empty."
            )

        return normalized

    @field_validator("organization_id")
    @classmethod
    def _validate_organization_id(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()

        if not normalized.startswith("org_"):
            raise ValueError(
                "organization_id must use the org_ identifier standard."
            )

        return normalized

    @field_validator(
        "days_remaining",
        "hours_remaining",
        "unit_amount_cents",
        "seat_limit",
        "connection_limit",
        "monthly_explanation_limit",
        "monthly_dq_analysis_limit",
    )
    @classmethod
    def _validate_non_negative_numbers(
        cls,
        value: int | None,
    ) -> int | None:
        if value is not None and value < 0:
            raise ValueError(
                "Billing limits and remaining-time values "
                "cannot be negative."
            )

        return value


class CustomerPortalSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
