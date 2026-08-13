from __future__ import annotations

import logging
import os

import stripe

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
    status,
)

from app.repositories.billing_repository import (
    BillingRepository,
)
from app.services.stripe_billing_service import (
    StripeBillingService,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/billing/stripe",
    tags=["Stripe Webhooks"],
)
billing_repository = BillingRepository()

stripe_billing_service = StripeBillingService(
    billing_repository=billing_repository,
)

STRIPE_WEBHOOK_SECRET = (
    os.getenv("STRIPE_WEBHOOK_SECRET")
    or ""
).strip()


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
) -> dict[str, bool]:

    logger.info(
    "Stripe webhook request received: "
    "method=%s path=%s "
    "content_type=%s "
    "has_stripe_signature=%s "
    "content_length=%s",
    request.method,
    request.url.path,
    request.headers.get("content-type"),
    "stripe-signature" in request.headers,
    request.headers.get("content-length"),
)
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "STRIPE_WEBHOOK_SECRET "
                "is not configured."
            ),
        )

    payload = await request.body()

    signature = request.headers.get(
        "stripe-signature"
    )

    if not signature:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Stripe-Signature header "
                "is missing."
            ),
        )

    try:
        event = stripe.Webhook.construct_event(
            payload,
            signature,
            STRIPE_WEBHOOK_SECRET,
        )

    except ValueError as exc:
        logger.warning(
            "Invalid Stripe webhook payload: %s",
            str(exc),
        )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Stripe webhook payload.",
        ) from exc

    except stripe.SignatureVerificationError as exc:
        logger.warning(
            "Stripe webhook signature "
            "verification failed: %s",
            str(exc),
        )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Invalid Stripe webhook signature."
            ),
        ) from exc

    event_type = str(
        event.type or ""
    ).strip()

    event_object = event.data.object

    logger.info(
        "Stripe webhook received: %s",
        event_type,
    )

    if event_type in {
        "customer.subscription.created",
        "customer.subscription.updated",
    }:
        stripe_billing_service.sync_subscription_event(
            subscription=event_object,
        )

    elif event_type == (
        "customer.subscription.deleted"
    ):
        stripe_billing_service.sync_subscription_event(
            subscription=event_object,
        )

    elif event_type == "invoice.paid":
        subscription_id = getattr(
            event_object,
            "subscription",
            None,
        )

        if subscription_id:
            subscription = (
                stripe.Subscription.retrieve(
                    str(subscription_id),
                )
            )

            stripe_billing_service.sync_subscription_event(
                subscription=subscription,
                payment_status="PAID",
            )

    elif event_type == "invoice.payment_failed":
        subscription_id = getattr(
            event_object,
            "subscription",
            None,
        )

        if subscription_id:
            subscription = (
                stripe.Subscription.retrieve(
                    str(subscription_id),
                )
            )

            stripe_billing_service.sync_subscription_event(
                subscription=subscription,
                payment_status="FAILED",
            )

    else:
        logger.info(
            "Ignoring unhandled Stripe event: %s",
            event_type,
        )

    return {"received": True}