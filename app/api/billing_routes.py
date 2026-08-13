from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.auth import (
    AuthUser,
    get_current_user,
)

from app.api.billing_schemas import (
    BillingStatusResponse,
    CheckoutConfirmationResponse,
    CheckoutSessionResponse,
    CustomerPortalSessionResponse,
)
from app.repositories.billing_repository import BillingRepository
from app.services.billing_service import BillingService
from app.services.stripe_billing_service import StripeBillingService

from app.api.billing_schemas import (
    BillingStatusResponse,
    CheckoutConfirmationResponse,
    CheckoutSessionRequest,
    CheckoutSessionResponse,
    CustomerPortalSessionResponse,
)


router = APIRouter(
    prefix="/v1/billing",
    tags=["Billing"],
)

billing_repository = BillingRepository()

billing_service = BillingService(
    repository=billing_repository,
)

stripe_billing_service = StripeBillingService(
    billing_repository=billing_repository,
)


def _get_tenant_billing_status(
    *,
    current_user: AuthUser,
) -> BillingStatusResponse:
    """
    Load billing status for the authenticated organization.

    organization_id is the authoritative tenant boundary.
    customer_id may be absent from an older JWT and is therefore
    treated as an optional additional ownership check.
    """

    if not current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Authenticated organization context "
                "is required."
            ),
        )

    billing_status = billing_service.get_billing_status(
        current_user_email=str(current_user.email),
        organization_id=str(
            current_user.organization_id
        ),
        customer_id=(
            str(current_user.customer_id)
            if current_user.customer_id
            else None
        ),
    )

    if (
        billing_status.organization_id
        != current_user.organization_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Billing account does not belong to "
                "the authenticated organization."
            ),
        )

    # Defense-in-depth:
    # if the JWT already contains customer_id, it must match.
    if (
        current_user.customer_id
        and billing_status.customer_id
        != current_user.customer_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Billing customer does not belong to "
                "the authenticated user context."
            ),
        )

    return billing_status


@router.post(
    "/customer-portal",
    response_model=CustomerPortalSessionResponse,
)
def create_customer_portal_session(
    current_user: AuthUser = Depends(
        get_current_user
    ),
) -> CustomerPortalSessionResponse:
    billing_status = _get_tenant_billing_status(
        current_user=current_user,
    )

    if not billing_status.stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This account does not have a Stripe "
                "customer record."
            ),
        )

    portal_session = (
        stripe_billing_service.create_customer_portal_session(
            stripe_customer_id=(
                billing_status.stripe_customer_id
                ),
                customer_id=(
                billing_status.customer_id
                ),
                organization_id=(
                billing_status.organization_id
                ),
    )
)

    return CustomerPortalSessionResponse(
    url=portal_session,
)


@router.post(
    "/confirm-checkout/{session_id}",
    response_model=CheckoutConfirmationResponse,
)
def confirm_checkout(
    session_id: str,
    current_user: AuthUser = Depends(
        get_current_user
    ),
) -> CheckoutConfirmationResponse:
    billing_status = _get_tenant_billing_status(
        current_user=current_user,
    )

    stripe_billing_service.confirm_checkout_session(
        session_id=session_id,
        expected_customer_id=(
            billing_status.customer_id
        ),
        organization_id=(
            billing_status.organization_id
        ),
        current_user_email=str(
            current_user.email
        ),
    )

    updated_status = _get_tenant_billing_status(
        current_user=current_user,
    )

    return CheckoutConfirmationResponse(
        success=True,
        subscription_status=(
            updated_status.subscription_status
        ),
        payment_status=(
            updated_status.payment_status
        ),
        message="Your subscription is active.",
    )


@router.post(
    "/checkout-session",
    response_model=CheckoutSessionResponse,
)
def create_checkout_session(
    payload: CheckoutSessionRequest,
    current_user: AuthUser = Depends(
        get_current_user
    ),
) -> CheckoutSessionResponse:
    billing_status = _get_tenant_billing_status(
        current_user=current_user,
    )

    if billing_status.subscription_status == "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This account already has an active "
                "subscription. Use Manage Billing "
                "to change plans."
            ),
        )

    checkout_url = (
        stripe_billing_service.create_checkout_session(
            customer_id=billing_status.customer_id,
            organization_id=(
                billing_status.organization_id
            ),
            customer_email=str(current_user.email),
            organization_name=(
                billing_status.organization_name
            ),
            stripe_customer_id=(
                billing_status.stripe_customer_id
            ),
            plan_code=payload.plan_code,
        )
    )

    return CheckoutSessionResponse(
        checkout_url=checkout_url,
    )


@router.get(
    "/status",
    response_model=BillingStatusResponse,
)
def get_billing_status(
    current_user: AuthUser = Depends(
        get_current_user
    ),
) -> BillingStatusResponse:
    return _get_tenant_billing_status(
        current_user=current_user,
    )