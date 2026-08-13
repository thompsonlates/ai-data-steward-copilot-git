from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from app.api.auth import (
    AuthUser,
    get_current_user,
)

from app.api.onboarding_progress_schemas import (
    OnboardingProgressResponse,
)
from app.repositories.onboarding_progress_repository import (
    OnboardingProgressRepository,
)
from app.services.onboarding_progress_service import (
    OnboardingProgressService,
)


router = APIRouter(
    prefix="/v1/onboarding-progress",
    tags=["Onboarding Progress"],
)

repository = OnboardingProgressRepository()

service = OnboardingProgressService(
    repository=repository,
)


@router.get(
    "",
    response_model=OnboardingProgressResponse,
)
def get_onboarding_progress(
    current_user: AuthUser = Depends(
        get_current_user
    ),
) -> OnboardingProgressResponse:
    """
    Return onboarding progress for the authenticated organization.

    Commercial customer context is not required because a tenant
    may still be operating within its free-trial lifecycle.
    """

    if not current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Authenticated organization context "
                "is required."
            ),
        )

    return service.get_progress(
        current_user_email=str(
            current_user.email
        ),
        organization_id=str(
            current_user.organization_id
        ),
    )