"""Behavior-preserving route extraction from the former monolithic routes.py."""

from app.api.route_dependencies import *
from app.api.route_dependencies import (
    _get_cached_dq_rule_suggestions,
    _set_cached_dq_rule_suggestions,
    _get_onedrive_connector,
)

router = APIRouter()


@router.post(
    "/organization/users/invite",
    response_model=CustomerUserResponse,
)
def invite_customer_user(
    payload: CustomerUserInviteRequest,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
) -> CustomerUserResponse:
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    result = customer_user_service.invite_user(
        organization_id=organization_id,
        email=str(payload.email),
        display_name=payload.display_name,
        organization_role=payload.organization_role,
        billing_role=payload.billing_role,
        invited_by=str(current_user.email),
    )

    return CustomerUserResponse(
        **result
    )
