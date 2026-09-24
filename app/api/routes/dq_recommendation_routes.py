"""Behavior-preserving route extraction from the former monolithic routes.py."""

from app.api.route_dependencies import *
from app.api.route_dependencies import (
    _get_cached_dq_rule_suggestions,
    _set_cached_dq_rule_suggestions,
    _get_onedrive_connector,
)

router = APIRouter()


@router.get(
    "/dq/recommendations/approved",
    response_model=DqApprovedRecommendationListResponse,
    status_code=status.HTTP_200_OK,
)
def get_approved_dq_recommendations(
    profile_run_id: str = Query(...),
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
    ),
    current_user: AuthUser = Depends(
        get_current_tenant_user
    ),
):
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    normalized_profile_run_id = str(
        profile_run_id or ""
    ).strip()

    if not normalized_profile_run_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="profile_run_id is required.",
        )

    try:
        recommendations = (
            quality_profiler_repository
            .get_approved_ai_recommendations(
                organization_id=organization_id,
                profile_run_id=(
                    normalized_profile_run_id
                ),
                limit=limit,
            )
        )

        return DqApprovedRecommendationListResponse(
            organization_id=organization_id,
            count=len(recommendations),
            recommendations=[
                DqApprovedRecommendationResponse(
                    **recommendation
                )
                for recommendation
                in recommendations
            ],
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "Unable to load approved DQ recommendations. "
            "organization_id=%s "
            "profile_run_id=%s",
            organization_id,
            normalized_profile_run_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Unable to load approved "
                "DQ recommendations."
            ),
        ) from exc


@router.post(
    "/ai-recommendations/feedback"
)
def submit_ai_recommendation_feedback(
    req: AiRecommendationFeedbackRequest,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
):
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    repository = (
        AiRecommendationFeedbackRepository()
    )

    return repository.save_feedback(
        organization_id=organization_id,
        recommendation_id=(
            req.recommendation_id
        ),
        recommendation_type=(
            req.recommendation_type
        ),
        profile_run_id=req.profile_run_id,
        domain=req.domain,
        rule_id=req.rule_id,
        ai_recommendation=(
            req.ai_recommendation
        ),
        ai_confidence=req.ai_confidence,
        steward_decision=(
            req.steward_decision
        ),
        steward_comment=(
            req.steward_comment
        ),
        override_reason=(
            req.override_reason
        ),
        submitted_by=(
            getattr(
                current_user,
                "email",
                None,
            )
        ),
        source_component=(
            req.source_component
        ),
    )
