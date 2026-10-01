"""Behavior-preserving route extraction from the former monolithic routes.py."""

import json

from app.api.route_dependencies import *
from app.api.route_dependencies import (
    _get_cached_dq_rule_suggestions,
    _set_cached_dq_rule_suggestions,
    _get_onedrive_connector,
)

router = APIRouter()


@router.post(
    "/dq/recommendations/{recommendation_id}/generate-sql",
    response_model=DqRemediationGenerateResponse,
    status_code=status.HTTP_200_OK,
)
def generate_dq_remediation_sql(
    recommendation_id: str,
    req: DqRemediationGenerateRequest,
    current_user: AuthUser = Depends(require_active_product_access),
) -> DqRemediationGenerateResponse:
    """Generate one tenant-scoped DQ remediation artifact."""
    organization_id = require_current_organization_id(current_user)
    path_recommendation_id = str(recommendation_id or "").strip()
    request_recommendation_id = str(req.recommendation_id or "").strip()

    if not path_recommendation_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="recommendation_id is required.",
        )

    if request_recommendation_id != path_recommendation_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Request recommendation_id does not match "
                "path recommendation_id."
            ),
        )

    try:
        recommendation = quality_profiler_repository.get_ai_recommendation(
            organization_id=organization_id,
            recommendation_id=path_recommendation_id,
        )
        if recommendation is None:
            raise ValueError("DQ recommendation was not found.")

        persisted_profile_run_id = str(
            recommendation.get("profile_run_id") or ""
        ).strip()
        requested_profile_run_id = str(req.profile_run_id or "").strip()

        if (
            requested_profile_run_id
            and requested_profile_run_id != persisted_profile_run_id
        ):
            raise ValueError(
                "Request profile_run_id does not match the "
                "persisted recommendation profile run."
            )

        requested_connection_id = str(req.connection_id or "").strip()

        # Enterprise Connection path: resolve the Databricks target first.
        # Native Databricks profiles must never require a BigQuery stage table.
        if requested_connection_id:
            connection = connection_repository.get_connection(
                connection_id=requested_connection_id,
                organization_id=organization_id,
            )
            if connection is None:
                raise ValueError("Enterprise connection was not found.")

            connection_org_id = str(
                connection.get("organization_id") or ""
            ).strip()
            if connection_org_id != organization_id:
                raise ValueError(
                    "Enterprise connection does not belong "
                    "to the authenticated organization."
                )

            vendor = str(connection.get("vendor") or "").strip().upper()
            environment = str(
                connection.get("environment") or ""
            ).strip().upper()

            if vendor != "DATABRICKS":
                raise ValueError(
                    "Selected connection is not a Databricks connection."
                )
            if environment not in {"DEV", "STG"}:
                raise ValueError(
                    "Databricks remediation generation is allowed only "
                    "for DEV or STG connections."
                )

            details = connection.get("connection_details") or {}
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        "Databricks connection_details are invalid."
                    ) from exc
            if not isinstance(details, dict):
                raise ValueError("Databricks connection_details are invalid.")

            catalog = str(details.get("catalog") or "").strip()
            schema_name = str(
                details.get("schema") or details.get("schema_name") or ""
            ).strip()
            table_name = str(details.get("table_name") or "").strip()

            if not all([catalog, schema_name, table_name]):
                raise ValueError(
                    "Databricks connection must define catalog, schema, "
                    "and table_name."
                )

            source_table = f"{catalog}.{schema_name}.{table_name}"
            sql_dialect = "DATABRICKS"
        else:
            # Existing file / Google Sheets path: use the server-managed
            # BigQuery profile stage only when no Enterprise Connection exists.
            source_table = (
                quality_profiler_repository.get_profile_stage_table_id(
                    organization_id=organization_id,
                    profile_run_id=persisted_profile_run_id,
                    require_exists=True,
                )
            )
            sql_dialect = "BIGQUERY"

        service = DqAiRecommendationService()
        result = service.generate_remediation(
            organization_id=organization_id,
            recommendation_id=path_recommendation_id,
            source_table=source_table,
            sql_dialect=sql_dialect,
            artifact_preference=req.artifact_preference,
        )

        returned_organization_id = str(
            result.get("organization_id") or ""
        ).strip()
        if returned_organization_id != organization_id:
            logger.error(
                "Cross-tenant DQ remediation response blocked. "
                "authenticated_organization_id=%s "
                "returned_organization_id=%s recommendation_id=%s",
                organization_id,
                returned_organization_id,
                path_recommendation_id,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "Security validation failed: remediation response belongs "
                    "to a different organization."
                ),
            )

        return DqRemediationGenerateResponse(**result)

    except HTTPException:
        raise
    except ValueError as exc:
        message = str(exc)
        if "not found" in message.lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=message,
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        ) from exc
    except Exception as exc:
        logger.exception(
            "DQ generate-sql failed. organization_id=%s recommendation_id=%s",
            organization_id,
            path_recommendation_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to generate DQ remediation.",
        ) from exc

@router.get(
    "/dq/recommendations/{recommendation_id}/remediation-versions",
    response_model=DqRemediationVersionListResponse,
    status_code=status.HTTP_200_OK,
)
def get_dq_remediation_versions(
    recommendation_id: str,
    current_user: AuthUser = Depends(
        get_current_tenant_user
    ),
) -> DqRemediationVersionListResponse:
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
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="recommendation_id is required.",
        )

    try:
        versions = (
            quality_profiler_repository
            .get_remediation_versions(
                organization_id=organization_id,
                recommendation_id=(
                    normalized_recommendation_id
                ),
            )
        )

        return DqRemediationVersionListResponse(
            recommendation_id=(
                normalized_recommendation_id
            ),
            versions=[
                DqRemediationVersionResponse(
                    **version
                )
                for version in versions
            ],
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "Unable to load DQ remediation versions. "
            "organization_id=%s "
            "recommendation_id=%s",
            organization_id,
            normalized_recommendation_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Unable to load remediation versions."
            ),
        ) from exc


@router.post(
    "/dq/recommendations/{recommendation_id}/remediation-feedback",
    response_model=DqRemediationFeedbackResponse,
    status_code=status.HTTP_200_OK,
)
def submit_dq_remediation_feedback(
    recommendation_id: str,
    req: DqRemediationFeedbackRequest,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
) -> DqRemediationFeedbackResponse:
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
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="recommendation_id is required.",
        )

    steward_user = str(
        current_user.email or ""
    ).strip()

    if not steward_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authenticated steward email is required.",
        )

    try:
        result = (
            quality_profiler_repository
            .save_remediation_feedback(
                organization_id=organization_id,
                recommendation_id=(
                    normalized_recommendation_id
                ),
                decision=req.decision,
                steward_user=steward_user,
                reason_code=req.reason_code,
                note=req.note,
            )
        )

        returned_organization_id = str(
            result.get("organization_id")
            or ""
        ).strip()

        if returned_organization_id != organization_id:
            logger.error(
                "Cross-tenant remediation feedback "
                "response blocked. "
                "authenticated_organization_id=%s "
                "returned_organization_id=%s "
                "recommendation_id=%s",
                organization_id,
                returned_organization_id,
                normalized_recommendation_id,
            )

            raise HTTPException(
                status_code=(
                    status.HTTP_500_INTERNAL_SERVER_ERROR
                ),
                detail=(
                    "Security validation failed: "
                    "feedback response belongs to "
                    "a different organization."
                ),
            )

        return DqRemediationFeedbackResponse(
            **result
        )

    except HTTPException:
        raise

    except ValueError as exc:
        message = str(exc)

        if "not found" in message.lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=message,
            ) from exc

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        ) from exc

    except Exception as exc:
        logger.exception(
            "DQ remediation feedback failed. "
            "organization_id=%s "
            "recommendation_id=%s "
            "decision=%s",
            organization_id,
            normalized_recommendation_id,
            req.decision,
        )

        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "Unable to submit DQ remediation feedback."
            ),
        ) from exc


@router.post(
    "/dq/recommendations/"
    "{recommendation_id}/generate-remediation",
    response_model=DqRemediationGenerateResponse,
)
def generate_dq_remediation_legacy(
    recommendation_id: str,
    req: DqRemediationGenerateRequest,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
) -> DqRemediationGenerateResponse:
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    path_recommendation_id = str(
        recommendation_id or ""
    ).strip()

    request_recommendation_id = str(
        req.recommendation_id or ""
    ).strip()

    if not path_recommendation_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "recommendation_id is required."
            ),
        )

    if (
        request_recommendation_id
        != path_recommendation_id
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Request recommendation_id does "
                "not match path recommendation_id."
            ),
        )

    try:
        service = DqAiRecommendationService()

        result = service.generate_remediation(
            organization_id=organization_id,
            recommendation_id=(
                path_recommendation_id
            ),
            source_table=req.source_table,
            sql_dialect=req.sql_dialect,
        )

        returned_organization_id = str(
            result.get("organization_id")
            or ""
        ).strip()

        if (
            returned_organization_id
            != organization_id
        ):
            raise HTTPException(
                status_code=500,
                detail=(
                    "Security validation failed: "
                    "remediation response belongs "
                    "to a different organization."
                ),
            )

        return DqRemediationGenerateResponse(
            **result
        )

    except HTTPException:
        raise

    except ValueError as exc:
        message = str(exc)

        if "was not found" in message.lower():
            raise HTTPException(
                status_code=404,
                detail=message,
            ) from exc

        raise HTTPException(
            status_code=400,
            detail=message,
        ) from exc

    except Exception as exc:
        logger.exception(
            "DQ remediation generation failed. "
            "organization_id=%s "
            "recommendation_id=%s",
            organization_id,
            path_recommendation_id,
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to generate DQ "
                "remediation."
            ),
        ) from exc
