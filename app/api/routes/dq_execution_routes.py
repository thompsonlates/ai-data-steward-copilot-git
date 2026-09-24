"""Behavior-preserving route extraction from the former monolithic routes.py."""

from app.api.route_dependencies import *
from app.api.route_dependencies import (
    _get_cached_dq_rule_suggestions,
    _set_cached_dq_rule_suggestions,
    _get_onedrive_connector,
)

router = APIRouter()


@router.post(
    "/dq/recommendations/"
    "{recommendation_id}/validate-execution",
    response_model=DqExecutionResponse,
    status_code=status.HTTP_200_OK,
)
def validate_dq_remediation_execution(
    recommendation_id: str,
    req: DqExecutionValidationRequest,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
) -> DqExecutionResponse:
    """
    Validate a steward-approved DQ remediation against
    a tenant-scoped Enterprise Connection.

    This performs all governance, tenant, connection,
    environment, entitlement, artifact, and SQL safety
    checks WITHOUT executing target SQL.
    """

    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    normalized_recommendation_id = str(
        recommendation_id or ""
    ).strip()

    if not normalized_recommendation_id:
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
            ),
            detail="recommendation_id is required.",
        )

    requested_by = str(
        current_user.email or ""
    ).strip()

    if not requested_by:
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
            ),
            detail=(
                "Authenticated user email is required "
                "for DQ execution validation."
            ),
        )

    try:
        result = (
            dq_execution_service
            .validate_approved_remediation(
                organization_id=organization_id,
                recommendation_id=(
                    normalized_recommendation_id
                ),
                profile_run_id=(
                    req.profile_run_id
                ),
                connection_id=(
                    req.connection_id
                ),
                requested_by=requested_by,
                technical_approved_by=(
                    req.technical_approved_by
                ),
            )
        )

        returned_organization_id = str(
            result.get("organization_id")
            or ""
        ).strip()

        if (
            returned_organization_id
            != organization_id
        ):
            logger.error(
                "Cross-tenant DQ execution "
                "validation response blocked. "
                "authenticated_organization_id=%s "
                "returned_organization_id=%s "
                "recommendation_id=%s "
                "connection_id=%s",
                organization_id,
                returned_organization_id,
                normalized_recommendation_id,
                req.connection_id,
            )

            raise HTTPException(
                status_code=(
                    status.HTTP_500_INTERNAL_SERVER_ERROR
                ),
                detail=(
                    "Security validation failed: "
                    "execution response belongs to "
                    "a different organization."
                ),
            )

        return DqExecutionResponse(
            **result
        )

    except HTTPException:
        raise

    except ValueError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
            ),
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "DQ execution validation failed. "
            "organization_id=%s "
            "recommendation_id=%s "
            "connection_id=%s "
            "error=%s",
            organization_id,
            normalized_recommendation_id,
            req.connection_id,
            type(exc).__name__,
        )

        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "Unable to validate DQ remediation "
                "execution."
            ),
        ) from exc


@router.post(
    "/dq/recommendations/"
    "{recommendation_id}/execute",
    response_model=DqExecutionResponse,
    status_code=status.HTTP_200_OK,
)
def execute_dq_remediation(
    recommendation_id: str,
    req: DqExecutionRequest,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
) -> DqExecutionResponse:
    """
    Execute the exact persisted and steward-approved
    DQ remediation SQL against the selected tenant-scoped
    DEV/STG Enterprise Connection.

    Supported execution vendors:
      - Databricks
      - Snowflake
      - Google BigQuery

    Production connections and raw REGEX artifacts are
    blocked by DqExecutionService.
    """

    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    normalized_recommendation_id = str(
        recommendation_id or ""
    ).strip()

    if not normalized_recommendation_id:
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
            ),
            detail="recommendation_id is required.",
        )

    requested_by = str(
        current_user.email or ""
    ).strip()

    if not requested_by:
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
            ),
            detail=(
                "Authenticated user email is required "
                "for DQ execution."
            ),
        )

    # DqExecutionRequest uses:
    #
    #     confirm_execution: Literal[True]
    #
    # so Pydantic already rejects false/missing values.
    # Keep this explicit defensive check as well.
    if req.confirm_execution is not True:
        raise HTTPException(
            status_code=(
                status.HTTP_409_CONFLICT
            ),
            detail=(
                "Explicit confirmation is required "
                "before executing DQ remediation."
            ),
        )

    try:
        result = (
            dq_execution_service
            .execute_approved_remediation(
                organization_id=organization_id,
                recommendation_id=(
                    normalized_recommendation_id
                ),
                profile_run_id=(
                    req.profile_run_id
                ),
                connection_id=(
                    req.connection_id
                ),
                requested_by=requested_by,
                technical_approved_by=(
                    req.technical_approved_by
                ),
            )
        )

        returned_organization_id = str(
            result.get("organization_id")
            or ""
        ).strip()

        if (
            returned_organization_id
            != organization_id
        ):
            logger.error(
                "Cross-tenant DQ execution "
                "response blocked. "
                "authenticated_organization_id=%s "
                "returned_organization_id=%s "
                "recommendation_id=%s "
                "connection_id=%s",
                organization_id,
                returned_organization_id,
                normalized_recommendation_id,
                req.connection_id,
            )

            raise HTTPException(
                status_code=(
                    status.HTTP_500_INTERNAL_SERVER_ERROR
                ),
                detail=(
                    "Security validation failed: "
                    "execution response belongs to "
                    "a different organization."
                ),
            )

        return DqExecutionResponse(
            **result
        )

    except HTTPException:
        raise

    except ValueError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
            ),
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "DQ remediation execution route failed. "
            "organization_id=%s "
            "recommendation_id=%s "
            "connection_id=%s "
            "error=%s",
            organization_id,
            normalized_recommendation_id,
            req.connection_id,
            type(exc).__name__,
        )

        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "Unable to execute DQ remediation."
            ),
        ) from exc
