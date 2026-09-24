from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import stripe
from fastapi import HTTPException, status

from app.repositories.billing_repository import BillingRepository


logger = logging.getLogger(__name__)


class StripeBillingService:
    def __init__(
        self,
        *,
        billing_repository: BillingRepository,
    ) -> None:
        self.billing_repository = billing_repository

        self.secret_key = (
            os.getenv("STRIPE_SECRET_KEY") or ""
        ).strip()

        self.price_ids = {
            "PROFESSIONAL": (
                os.getenv("STRIPE_PRICE_PROFESSIONAL") or ""
            ).strip(),
            "PROFESSIONAL_PLUS": (
                os.getenv("STRIPE_PRICE_PROFESSIONAL_PLUS") or ""
            ).strip(),
            "STEWARD_OPERATIONS": (
                os.getenv("STRIPE_PRICE_STEWARD_OPERATIONS") or ""
            ).strip(),
            "GOVERNANCE_INTELLIGENCE": (
                os.getenv("STRIPE_PRICE_GOVERNANCE_INTELLIGENCE") or ""
            ).strip(),
            "ENTERPRISE_GOVERNANCE_PLATFORM": (
                os.getenv(
                    "STRIPE_PRICE_ENTERPRISE_GOVERNANCE_PLATFORM"
                )
                or ""
            ).strip(),
        }

        self.success_url = (
            os.getenv("STRIPE_SUCCESS_URL") or ""
        ).strip()
        self.cancel_url = (
            os.getenv("STRIPE_CANCEL_URL") or ""
        ).strip()

        if not self.secret_key:
            raise RuntimeError(
                "STRIPE_SECRET_KEY is not configured."
            )

        required_self_service_plans = {
            "PROFESSIONAL",
            "PROFESSIONAL_PLUS",
            "STEWARD_OPERATIONS",
            "GOVERNANCE_INTELLIGENCE",
        }

        for plan_code in required_self_service_plans:
            if not self.price_ids.get(plan_code):
                raise RuntimeError(
                    f"Stripe price is not configured for {plan_code}."
                )

        if not self.success_url:
            raise RuntimeError(
                "STRIPE_SUCCESS_URL is not configured."
            )

        if not self.cancel_url:
            raise RuntimeError(
                "STRIPE_CANCEL_URL is not configured."
            )

        stripe.api_key = self.secret_key

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
    def _require_customer_id(
        customer_id: str,
    ) -> str:
        normalized = str(
            customer_id or ""
        ).strip()

        if not normalized:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "A valid customer is required "
                    "for billing operations."
                ),
            )

        return normalized

    @staticmethod
    def _normalize_plan_code(
        plan_code: str,
    ) -> str:
        normalized = str(
            plan_code or ""
        ).strip().upper()

        allowed = {
            "PROFESSIONAL",
            "PROFESSIONAL_PLUS",
            "STEWARD_OPERATIONS",
            "GOVERNANCE_INTELLIGENCE",
            "ENTERPRISE_GOVERNANCE_PLATFORM",
        }

        if normalized not in allowed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unsupported plan: {normalized}",
            )

        return normalized

    def _get_checkout_price_id(
        self,
        *,
        plan_code: str,
    ) -> str:
        normalized_plan = self._normalize_plan_code(
            plan_code
        )

        if normalized_plan == (
            "ENTERPRISE_GOVERNANCE_PLATFORM"
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Enterprise Governance Platform "
                    "requires Contact Sales."
                ),
            )

        price_id = self.price_ids.get(
            normalized_plan
        )

        if not price_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    f"Stripe price is not configured "
                    f"for {normalized_plan}."
                ),
            )

        return price_id

    def _get_plan_code_for_price_id(
        self,
        *,
        stripe_price_id: str,
    ) -> str | None:
        normalized_price_id = str(
            stripe_price_id or ""
        ).strip()

        for plan_code, configured_price_id in self.price_ids.items():
            if configured_price_id == normalized_price_id:
                return plan_code

        return None

    def _get_tenant_subscription(
        self,
        *,
        customer_id: str,
        organization_id: str,
    ) -> dict[str, Any] | None:
        return self.billing_repository.get_current_subscription(
            customer_id=customer_id,
            organization_id=organization_id,
        )

    def _verify_stripe_customer_for_tenant(
        self,
        *,
        customer_id: str,
        organization_id: str,
        stripe_customer_id: str,
    ) -> None:
        subscription = self._get_tenant_subscription(
            customer_id=customer_id,
            organization_id=organization_id,
        )

        if subscription is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    "Billing subscription was not found "
                    "for the authenticated organization."
                ),
            )

        stored_stripe_customer_id = str(
            subscription.get("stripe_customer_id")
            or ""
        ).strip()

        # Existing trial records may not have a Stripe customer yet.
        if (
            stored_stripe_customer_id
            and stored_stripe_customer_id
            != stripe_customer_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Stripe customer does not belong to "
                    "the authenticated organization."
                ),
            )

    def create_customer_portal_session(
        self,
        *,
        customer_id: str,
        organization_id: str,
        stripe_customer_id: str,
    ) -> str:
        effective_customer_id = (
            self._require_customer_id(
                customer_id
            )
        )
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )
        normalized_stripe_customer_id = str(
            stripe_customer_id or ""
        ).strip()

        if not normalized_stripe_customer_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Stripe customer ID is required.",
            )

        self._verify_stripe_customer_for_tenant(
            customer_id=effective_customer_id,
            organization_id=effective_organization_id,
            stripe_customer_id=normalized_stripe_customer_id,
        )

        return_url = (
            os.getenv("STRIPE_PORTAL_RETURN_URL")
            or os.getenv("FRONTEND_URL")
            or "http://localhost:5173/?page=dashboard"
        )

        try:
            # Confirm that the stored customer exists in the Stripe
            # account/mode configured by STRIPE_SECRET_KEY before
            # attempting to create a Customer Portal session.
            stripe_customer = stripe.Customer.retrieve(
                normalized_stripe_customer_id
            )

            if getattr(stripe_customer, "deleted", False):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "The stored Stripe customer has been deleted. "
                        "Start a new checkout session or repair the "
                        "Stripe customer mapping before opening billing."
                    ),
                )

            session = stripe.billing_portal.Session.create(
                customer=normalized_stripe_customer_id,
                return_url=return_url,
            )

        except HTTPException:
            raise

        except stripe.StripeError as exc:
            stripe_message = str(exc)
            logger.warning(
                "Stripe portal session creation failed: %s - %s",
                type(exc).__name__,
                stripe_message,
            )

            if "No such customer" in stripe_message:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "The stored Stripe customer does not exist in "
                        "the Stripe account/mode configured for this "
                        "environment. Verify STRIPE_SECRET_KEY and the "
                        "stored stripe_customer_id, or complete a new "
                        "checkout in the same Stripe mode."
                    ),
                ) from exc

            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "Unable to create the Stripe billing portal session."
                ),
            ) from exc

        if not session.url:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "Stripe did not return a billing portal URL."
                ),
            )

        return str(session.url)

    def create_checkout_session(
        self,
        *,
        customer_id: str,
        organization_id: str,
        customer_email: str,
        organization_name: str,
        stripe_customer_id: str | None,
        plan_code: str,
    ) -> str:
        effective_customer_id = (
            self._require_customer_id(
                customer_id
            )
        )
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )
        normalized_email = str(
            customer_email or ""
        ).strip().lower()
        normalized_organization_name = str(
            organization_name or ""
        ).strip()
        normalized_stripe_customer_id = (
            str(stripe_customer_id).strip()
            if stripe_customer_id
            else None
        )

        if not normalized_email:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Customer email is required.",
            )

        if not normalized_organization_name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Organization name is required.",
            )

        if normalized_stripe_customer_id:
            self._verify_stripe_customer_for_tenant(
                customer_id=effective_customer_id,
                organization_id=effective_organization_id,
                stripe_customer_id=normalized_stripe_customer_id,
            )

        normalized_plan_code = (
            self._normalize_plan_code(
                plan_code
            )
        )

        checkout_price_id = (
            self._get_checkout_price_id(
                plan_code=normalized_plan_code
            )
        )

        try:
            session_args: dict[str, Any] = {
                "mode": "subscription",
                "line_items": [
                    {
                        "price": checkout_price_id,
                        "quantity": 1,
                    }
                ],
                "success_url": (
                    self.success_url
                    + "&session_id={CHECKOUT_SESSION_ID}"
                ),
                "cancel_url": self.cancel_url,
                "client_reference_id": (
                    effective_customer_id
                ),
                "allow_promotion_codes": True,
                "metadata": {
                    "customer_id": (
                        effective_customer_id
                    ),
                    "organization_id": (
                        effective_organization_id
                    ),
                    "organization_name": (
                        normalized_organization_name
                    ),
                    "plan_code": normalized_plan_code,
                },
                "subscription_data": {
                    "metadata": {
                        "customer_id": (
                            effective_customer_id
                        ),
                        "organization_id": (
                            effective_organization_id
                        ),
                        "plan_code": normalized_plan_code,
                    }
                },
            }

            if normalized_stripe_customer_id:
                session_args["customer"] = (
                    normalized_stripe_customer_id
                )
            else:
                session_args["customer_email"] = (
                    normalized_email
                )

            checkout_session = (
                stripe.checkout.Session.create(
                    **session_args
                )
            )

            if not checkout_session.url:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=(
                        "Stripe did not return a checkout URL."
                    ),
                )

            return str(checkout_session.url)

        except HTTPException:
            raise

        except stripe.StripeError as exc:
            logger.warning(
                "Stripe Checkout session creation "
                "failed: %s",
                type(exc).__name__,
            )

            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    getattr(exc, "user_message", None)
                    or "Unable to create Stripe "
                    "Checkout session."
                ),
            ) from exc

        except Exception as exc:
            logger.exception(
                "Unexpected Stripe Checkout error."
            )

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "An unexpected error occurred while "
                    "creating the Stripe Checkout session."
                ),
            ) from exc

    def sync_subscription_event(
        self,
        *,
        subscription: Any,
        payment_status: str | None = None,
    ) -> None:
        subscription_id = str(
            subscription.id or ""
        ).strip()

        stripe_customer_id = str(
            subscription.customer or ""
        ).strip()

        if not subscription_id:
            raise ValueError(
                "Stripe subscription ID is missing."
            )

        if not stripe_customer_id:
            raise ValueError(
                "Stripe customer ID is missing."
            )

        items = (
            subscription.items.data
            if subscription.items
            else []
        )

        if not items:
            raise ValueError(
                "Stripe subscription has no items."
            )

        item = items[0]

        price = item.price

        if not price:
            raise ValueError(
                "Stripe subscription item "
                "does not contain a price."
            )

        stripe_price_id = str(
            price.id or ""
        ).strip()

        if not stripe_price_id:
            raise ValueError(
                "Stripe price ID is missing."
            )

        plan_code = self._get_plan_code_for_price_id(
            stripe_price_id=stripe_price_id
        )

        if not plan_code:
            raise ValueError(
                "Stripe price is not mapped to "
                "an AI Data Steward Copilot plan. "
                f"stripe_price_id={stripe_price_id}"
            )

        recurring = getattr(
            price,
            "recurring",
            None,
        )

        interval = (
            str(
                getattr(
                    recurring,
                    "interval",
                    "",
                )
                or ""
            )
            .strip()
            .upper()
        )

        if interval == "MONTH":
            billing_interval = "MONTH"

        elif interval == "YEAR":
            billing_interval = "YEAR"

        else:
            raise ValueError(
                "Unsupported Stripe billing interval. "
                f"interval={interval}"
            )

        stripe_product_id = str(
            getattr(
                price,
                "product",
                "",
            )
            or ""
        ).strip() or None

        currency = str(
            getattr(
                price,
                "currency",
                "",
            )
            or ""
        ).upper() or None

        unit_amount_cents = getattr(
            price,
            "unit_amount",
            None,
        )

        stripe_status = str(
            subscription.status or ""
        ).strip().lower()

        status_map = {
            "trialing": "TRIALING",
            "active": "ACTIVE",
            "past_due": "PAST_DUE",
            "unpaid": "UNPAID",
            "paused": "PAUSED",
            "canceled": "CANCELED",
            "incomplete_expired": "EXPIRED",
        }

        subscription_status = (
            status_map.get(
                stripe_status,
                "PAST_DUE",
            )
        )

        # Stripe's newer API versions expose
        # billing periods at the subscription-item level.
        current_period_started_at = (
            self._stripe_timestamp_to_datetime(
                getattr(
                    item,
                    "current_period_start",
                    None,
                )
            )
        )

        current_period_ends_at = (
            self._stripe_timestamp_to_datetime(
                getattr(
                    item,
                    "current_period_end",
                    None,
                )
            )
        )

        cancel_at_period_end = bool(
            getattr(
                subscription,
                "cancel_at_period_end",
                False,
            )
        )

        canceled_at = (
            self._stripe_timestamp_to_datetime(
                getattr(
                    subscription,
                    "canceled_at",
                    None,
                )
            )
        )

        self.billing_repository.sync_subscription_from_stripe(
            stripe_customer_id=stripe_customer_id,
            stripe_subscription_id=subscription_id,
            stripe_price_id=stripe_price_id,
            stripe_product_id=stripe_product_id,
            plan_code=plan_code,
            billing_interval=billing_interval,
            subscription_status=subscription_status,
            current_period_started_at=(
                current_period_started_at
            ),
            current_period_ends_at=(
                current_period_ends_at
            ),
            cancel_at_period_end=(
                cancel_at_period_end
            ),
            canceled_at=canceled_at,
            currency=currency,
            unit_amount_cents=(
                unit_amount_cents
            ),
            payment_status=payment_status,
            updated_by="stripe_webhook",
        )

    def confirm_checkout_session(
        self,
        *,
        session_id: str,
        expected_customer_id: str,
        organization_id: str,
        current_user_email: str,
    ) -> None:
        normalized_session_id = str(
            session_id or ""
        ).strip()
        effective_customer_id = (
            self._require_customer_id(
                expected_customer_id
            )
        )
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )
        normalized_user_email = str(
            current_user_email or ""
        ).strip().lower()

        if not normalized_session_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Stripe Checkout session ID is required.",
            )

        if not normalized_user_email:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authenticated user email is required.",
            )

        try:
            checkout_session = (
                stripe.checkout.Session.retrieve(
                    normalized_session_id,
                    expand=[
                        "subscription",
                        "subscription.items.data.price",
                    ],
                )
            )

            session_customer_id = str(
                checkout_session.client_reference_id
                or ""
            ).strip()

            session_metadata = (
                checkout_session.metadata.to_dict()
                if checkout_session.metadata
                else {}
            )

            session_organization_id = str(
                session_metadata.get(
                    "organization_id"
                )
                or ""
            ).strip()

            metadata_customer_id = str(
                session_metadata.get(
                    "customer_id"
                )
                or ""
            ).strip()

            if (
                session_customer_id
                != effective_customer_id
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "This Stripe Checkout Session "
                        "does not belong to the "
                        "authenticated customer."
                    ),
                )

            if (
                metadata_customer_id
                and metadata_customer_id
                != effective_customer_id
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "Stripe Checkout customer metadata "
                        "does not match the authenticated "
                        "customer."
                    ),
                )

            if (
                not session_organization_id
                or session_organization_id
                != effective_organization_id
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "This Stripe Checkout Session "
                        "does not belong to the "
                        "authenticated organization."
                    ),
                )

            if checkout_session.status != "complete":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Stripe Checkout has not "
                        "completed yet."
                    ),
                )

            if checkout_session.payment_status not in {
                "paid",
                "no_payment_required",
            }:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Stripe has not confirmed payment yet."
                    ),
                )

            subscription = checkout_session.subscription

            if not subscription:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Stripe did not return a subscription."
                    ),
                )

            subscription_metadata = (
                    subscription.metadata.to_dict()
                    if subscription.metadata
                    else {}
                )

            subscription_organization_id = str(
                subscription_metadata.get(
                    "organization_id"
                )
                or ""
            ).strip()

            subscription_customer_id = str(
                subscription_metadata.get(
                    "customer_id"
                )
                or ""
            ).strip()

            if (
                subscription_organization_id
                and subscription_organization_id
                != effective_organization_id
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "Stripe subscription metadata "
                        "does not match the authenticated "
                        "organization."
                    ),
                )

            if (
                subscription_customer_id
                and subscription_customer_id
                != effective_customer_id
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "Stripe subscription metadata "
                        "does not match the authenticated "
                        "customer."
                    ),
                )

            stripe_subscription_id = str(
                subscription.id
            )
            stripe_customer_id = str(
                checkout_session.customer or ""
            ).strip()

            if not stripe_customer_id:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Stripe customer ID is missing.",
                )

            current_period_start = getattr(
                subscription,
                "current_period_start",
                None,
            )

            current_period_end = getattr(
                subscription,
                "current_period_end",
                None,
            )

            current_period_started_at = (
                datetime.fromtimestamp(
                    current_period_start,
                    tz=timezone.utc,
                )
                if current_period_start
                else None
            )

            current_period_ends_at = (
                datetime.fromtimestamp(
                    current_period_end,
                    tz=timezone.utc,
                )
                if current_period_end
                else None
            )

            stripe_price_id: str | None = None

            subscription_items = getattr(
                subscription,
                "items",
                None,
            )

            if (
                subscription_items
                and subscription_items.data
            ):
                price = (
                    subscription_items.data[0].price
                )

                if price:
                    stripe_price_id = str(price.id)

                    resolved_plan_code = None

            if stripe_price_id:
             resolved_plan_code = (
                self._get_plan_code_for_price_id(
                    stripe_price_id=stripe_price_id
                )
            )

            if not resolved_plan_code:
                raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Stripe subscription price does not "
                    "map to a supported application plan."
                ),
            )

            billing_interval = (
            "MONTH"
            if resolved_plan_code
            in {
                "PROFESSIONAL",
                "PROFESSIONAL_PLUS",
            }
            else "YEAR"
        )

            self.billing_repository.activate_subscription_from_checkout(
                customer_id=effective_customer_id,
                organization_id=effective_organization_id,
                stripe_customer_id=stripe_customer_id,
                stripe_subscription_id=(
                    stripe_subscription_id
                ),
                stripe_checkout_session_id=str(
                    checkout_session.id
                ),
                stripe_price_id=stripe_price_id,
                plan_code=resolved_plan_code,
                billing_interval=billing_interval,
                current_period_started_at=(
                    current_period_started_at
                ),
                current_period_ends_at=(
                    current_period_ends_at
                ),
                updated_by=normalized_user_email,
)

        except HTTPException:
            raise

        except stripe.StripeError as exc:
            logger.warning(
                "Stripe Checkout confirmation failed: %s",
                type(exc).__name__,
            )

            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    getattr(exc, "user_message", None)
                    or "Unable to confirm Stripe Checkout."
                ),
            ) from exc
