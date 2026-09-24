"""Behavior-preserving route extraction from the former monolithic routes.py."""

from app.api.route_dependencies import *
from app.api.route_dependencies import (
    _get_cached_dq_rule_suggestions,
    _set_cached_dq_rule_suggestions,
    _get_onedrive_connector,
)

router = APIRouter()


@router.post(
    "/dq/profiles/{profile_run_id}/ai-analysis"
)
def analyze_dq_profile(
    profile_run_id: str,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
):
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    try:
        service = (
            DqAiRecommendationService()
        )

        return service.analyze_profile(
            organization_id=organization_id,
            profile_run_id=profile_run_id,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
