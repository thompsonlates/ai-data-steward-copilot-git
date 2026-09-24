"""Behavior-preserving route extraction from the former monolithic routes.py."""

from app.api.route_dependencies import *
from app.api.route_dependencies import (
    _get_cached_dq_rule_suggestions,
    _set_cached_dq_rule_suggestions,
    _get_onedrive_connector,
)

router = APIRouter()


@router.get(
    "/onboarding/status",
    response_model=OnboardingStatusResponse,
)
def get_onboarding_status(
    current_user: AuthUser = Depends(get_current_user),
) -> OnboardingStatusResponse:
    result = onboarding_service.get_onboarding_status(
        current_user=current_user,
    )

    return OnboardingStatusResponse.model_validate(result)


@router.post(
    "/onboarding/organization",
    response_model=OrganizationOnboardingResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_onboarding_organization(
    request: OrganizationOnboardingRequest,
    current_user: AuthUser = Depends(get_current_user),
) -> OrganizationOnboardingResponse:
    result = onboarding_service.create_organization_and_owner(
        current_user=current_user,
        request=request,
    )

    return OrganizationOnboardingResponse.model_validate(result)
