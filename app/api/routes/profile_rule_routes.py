"""Profile Rule Enhancement API routes."""

from __future__ import annotations

import json
from typing import Any

from app.api.route_dependencies import *
from app.api.schemas import (
    ProfileRuleAnalyzeRequest,
    ProfileRuleCandidateResponse,
    ProfileRulePreviewResponse,
    ProfileRuleRejectRequest,
    ProfileRuleRejectResponse,
    ProfileRuleApproveRequest,
    ProfileRuleApproveResponse,
    ProfileRuleDiscoveryCandidateRequest,
    ProfileRuleExceptionCreateRequest,
    ProfileRuleExceptionDeactivateRequest,
    ProfileRuleExceptionResponse,
ActiveProfileRuleResponse,
ProfileRuleGovernanceIdentityResponse,
)

from app.services.profile_rule_enhancement_service import (
    ProfileRuleEnhancementService,
)


router = APIRouter(
    prefix="/dq/profile-rules",
    tags=["DQ Profile Rules"],
)


profile_rule_enhancement_service = (
    ProfileRuleEnhancementService(
        quality_repository=quality_profiler_repository,
    )
)


def _decode_json_value(value: Any) -> Any:
    """
    Convert repository JSON storage values back into their
    API-facing Python representation.
    """
    if value is None:
        return None

    if not isinstance(value, str):
        return value

    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value

def _profile_rule_exception_response(
        row: dict[str, Any],
    ) -> ProfileRuleExceptionResponse:
        return ProfileRuleExceptionResponse(
            exception_id=row["exception_id"],
            organization_id=row["organization_id"],
            profile_id=row["profile_id"],
            rule_id=row["rule_id"],
            rule_version=int(row["rule_version"]),
            field_name=row["field_name"],
            match_type=row["match_type"],
            match_value=_decode_json_value(
                row.get("match_value_json")
            ),
            reason=row["reason"],
            status=row["status"],
            created_by=row.get("created_by"),
            created_at=row.get("created_at"),
            approved_by=row.get("approved_by"),
            approved_at=row.get("approved_at"),
            deactivated_by=row.get("deactivated_by"),
            deactivated_at=row.get("deactivated_at"),
            deactivation_reason=row.get("deactivation_reason"),
        )

@router.post(
    "/exceptions",
    response_model=ProfileRuleExceptionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_profile_rule_exception(
    request: ProfileRuleExceptionCreateRequest,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
) -> ProfileRuleExceptionResponse:
    organization_id = require_current_organization_id(
        current_user
    )

    approved_by = str(
        current_user.email or ""
    ).strip()

    if not approved_by:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Authenticated user email is required "
                "to approve a steward exception."
            ),
        )

    try:
        exception = (
            quality_profiler_repository
            .create_active_profile_rule_exception(
                organization_id=organization_id,
                profile_id=request.profile_id,
                rule_id=request.rule_id,
                rule_version=request.rule_version,
                field_name=request.field_name,
                match_type=request.match_type,
                match_value=request.match_value,
                reason=request.reason,
                approved_by=approved_by,
            )
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return _profile_rule_exception_response(
        exception
    )


@router.get(
    "/exceptions",
    response_model=list[ProfileRuleExceptionResponse],
)
def list_profile_rule_exceptions(
    profile_id: str,
    rule_id: str | None = None,
    rule_version: int | None = None,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
) -> list[ProfileRuleExceptionResponse]:
    organization_id = require_current_organization_id(
        current_user
    )

    try:
        exceptions = (
            quality_profiler_repository
            .list_active_profile_rule_exceptions(
                organization_id=organization_id,
                profile_id=profile_id,
                rule_id=rule_id,
                rule_version=rule_version,
            )
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return [
        _profile_rule_exception_response(row)
        for row in exceptions
    ]


@router.post(
    "/exceptions/{exception_id}/deactivate",
    response_model=ProfileRuleExceptionResponse,
)
def deactivate_profile_rule_exception(
    exception_id: str,
    request: ProfileRuleExceptionDeactivateRequest,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
) -> ProfileRuleExceptionResponse:
    organization_id = require_current_organization_id(
        current_user
    )

    deactivated_by = str(
        current_user.email or ""
    ).strip()

    if not deactivated_by:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Authenticated user email is required "
                "to deactivate a steward exception."
            ),
        )

    try:
        exception = (
            quality_profiler_repository
            .deactivate_profile_rule_exception(
                organization_id=organization_id,
                profile_id=request.profile_id,
                exception_id=exception_id,
                deactivated_by=deactivated_by,
                reason=request.reason,
            )
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return _profile_rule_exception_response(
        exception
    )

@router.post(
    "/discovery-candidates",
    response_model=ProfileRuleCandidateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_discovery_candidate(
    request: ProfileRuleDiscoveryCandidateRequest,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
) -> ProfileRuleCandidateResponse:
    """
    Persist a steward-selected AI pattern discovery as a
    profile-scoped candidate rule.

    This endpoint does not activate a rule and does not mutate
    source data.
    """

    organization_id = (
        require_current_organization_id(current_user)
    )
    created_by = str(current_user.email)

    try:
        candidate = (
            profile_rule_enhancement_service
            .create_discovery_candidate(
                organization_id=organization_id,
                profile_id=request.profile_id,
                profile_run_id=request.profile_run_id,
                field_name=request.field_name,
                pattern_summary=request.pattern_summary,
                rule_type=request.rule_type,
                operator=request.operator,
                comparison_value=request.comparison_value,
                remediation_action=request.remediation_action,
                replacement_value=request.replacement_value,
                confidence=request.confidence,
                explanation=request.explanation,
                created_by=created_by,
            )
        )

        return ProfileRuleCandidateResponse(
            organization_id=organization_id,
            candidate_id=candidate["candidate_id"],
            profile_id=candidate["profile_id"],
            profile_run_id=candidate["profile_run_id"],
            domain=candidate["domain"],
            field_name=candidate["field_name"],
            rule_name=candidate["rule_name"],
            rule_type=candidate["rule_type"],
            operator=candidate["operator"],
            comparison_value=_decode_json_value(
                candidate.get(
                    "comparison_value_json",
                    candidate.get("comparison_value"),
                )
            ),
            remediation_action=(
                candidate["remediation_action"]
            ),
            replacement_value=_decode_json_value(
                candidate.get(
                    "replacement_value_json",
                    candidate.get("replacement_value"),
                )
            ),
            candidate_source=candidate["candidate_source"],
            steward_instruction=(
                candidate.get("steward_instruction")
            ),
            discovery_summary=(
                candidate.get("discovery_summary")
            ),
            discovery_confidence=(
                candidate.get("discovery_confidence")
            ),
            ai_explanation=candidate["ai_explanation"],
            status=candidate["status"],
            created_at=candidate["created_at"],
        )

    except ValueError as exc:
        import traceback
        print(
            "[DISCOVERY CANDIDATE 400]",
            repr(exc),
            flush=True,
        )
        traceback.print_exc()

        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
@router.post(
    "/analyze",
    response_model=ProfileRuleCandidateResponse,
    status_code=status.HTTP_201_CREATED,
)
def analyze_profile_rule(
    request: ProfileRuleAnalyzeRequest,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
) -> ProfileRuleCandidateResponse:
    """
    Interpret a steward's natural-language profiling instruction
    and create a tenant/profile-scoped candidate rule.

    This endpoint does not activate a rule and does not mutate
    source data.
    """

    organization_id = (
        require_current_organization_id(current_user)
    )

    created_by = str(current_user.email)

    try:
        candidate = (
            profile_rule_enhancement_service
            .analyze_instruction(
                organization_id=organization_id,
                profile_id=request.profile_id,
                profile_run_id=request.profile_run_id,
                instruction=request.instruction,
                created_by=created_by,
            )
        )

        return ProfileRuleCandidateResponse(
            organization_id=organization_id,
            candidate_id=candidate["candidate_id"],
            profile_id=candidate["profile_id"],
            profile_run_id=candidate["profile_run_id"],
            domain=candidate["domain"],
            field_name=candidate["field_name"],
            rule_name=candidate["rule_name"],
            rule_type=candidate["rule_type"],
            operator=candidate["operator"],
            comparison_value=_decode_json_value(
                candidate.get(
                    "comparison_value_json",
                    candidate.get("comparison_value"),
                )
            ),
            remediation_action=(
                candidate["remediation_action"]
            ),
            replacement_value=_decode_json_value(
                candidate.get(
                    "replacement_value_json",
                    candidate.get("replacement_value"),
                )
            ),
            scope="PROFILE",
            status="CANDIDATE",
            steward_instruction=(
                candidate["steward_instruction"]
            ),
            ai_explanation=candidate["ai_explanation"],
            created_at=candidate["created_at"],
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

@router.post(
"/{candidate_id}/preview",
    response_model=ProfileRulePreviewResponse,
)
def preview_profile_rule_candidate(
        candidate_id: str,
        current_user: AuthUser = Depends(
            require_active_product_access
        ),
    ):
        organization_id = (
            require_current_organization_id(
                current_user
            )
        )

        # ---------------------------------------------------------
        # Load tenant-bound candidate
        # ---------------------------------------------------------

        candidate = (
            quality_profiler_repository
            .get_profile_rule_candidate(
                organization_id=organization_id,
                candidate_id=candidate_id,
            )
        )

        if not candidate:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Profile rule candidate was not found.",
            )

        candidate_status = str(
            candidate.get("status") or ""
        ).strip().upper()

        if candidate_status not in {
            "CANDIDATE",
            "PREVIEWED",
        }:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Only a candidate or previously previewed "
                    "profile rule may be previewed."
                ),
            )

        profile_id = str(
            candidate.get("profile_id") or ""
        ).strip()

        profile_run_id = str(
            candidate.get("profile_run_id") or ""
        ).strip()

        field_name = str(
            candidate.get("field_name") or ""
        ).strip()

        if not profile_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Profile rule candidate is missing profile_id."
                ),
            )

        if not profile_run_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Profile rule candidate is missing "
                    "profile_run_id."
                ),
            )

        if not field_name:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Profile rule candidate is missing field_name."
                ),
            )

        # ---------------------------------------------------------
        # Resolve exact governed source for this profile run
        # ---------------------------------------------------------

        source_context = (
            quality_profiler_repository
            .get_profile_source_context(
                organization_id=organization_id,
                profile_run_id=profile_run_id,
                profile_id=profile_id,
            )
        )

        if not source_context:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Governed profile source context could not "
                    "be resolved."
                ),
            )

        source_type = str(
            source_context.get("source_type") or ""
        ).strip().upper()

        if source_type != "GOOGLE_SHEETS":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This preview path currently supports "
                    "Google Sheets profile sources only."
                ),
            )

        connection_id = str(
            source_context.get("connection_id") or ""
        ).strip()

        spreadsheet_id = str(
            source_context.get("spreadsheet_id") or ""
        ).strip()

        sheet_name = str(
            source_context.get("sheet_name") or ""
        ).strip()

        if not connection_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Governed source context is missing "
                    "connection_id."
                ),
            )

        if not spreadsheet_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Governed source context is missing "
                    "spreadsheet_id."
                ),
            )

        if not sheet_name:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Governed source context is missing "
                    "sheet_name."
                ),
            )

        try:
            header_row = int(
                source_context.get("header_row") or 1
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Governed source context contains an "
                    "invalid header_row."
                ),
            ) from exc

        if header_row < 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Governed source context contains an "
                    "invalid header_row."
                ),
            )

        # ---------------------------------------------------------
        # Resolve persisted profile summary + evaluated population
        # ---------------------------------------------------------

        summary = (
            quality_profiler_repository
            .get_profile_summary(
                organization_id=organization_id,
                profile_run_id=profile_run_id,
            )
        )

        if not summary:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Profile summary could not be resolved "
                    "for this governed profile run."
                ),
            )

        try:
            profile_record_count = int(
                summary.get("total_records") or 0
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Profile summary contains an invalid "
                    "total_records value."
                ),
            ) from exc

        if profile_record_count < 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Profile run contains no governed records "
                    "available for rule preview."
                ),
            )

        # Connector currently protects reads at 100,000 rows.
        if profile_record_count > 100_000:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Profile rule preview exceeds the current "
                    "Google Sheets governed read limit."
                ),
            )

        # ---------------------------------------------------------
        # Resolve governed field -> physical source column
        # ---------------------------------------------------------

        try:
            source_column = (
                profile_rule_enhancement_service
                ._resolve_source_column(
                    organization_id=organization_id,
                    profile_run_id=profile_run_id,
                    field_name=field_name,
                )
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

        # ---------------------------------------------------------
        # Resolve tenant-bound OAuth credentials
        # ---------------------------------------------------------

        google_credentials = (
            connection_service
            .get_google_oauth_credentials(
                connection_id=connection_id,
                organization_id=organization_id,
            )
        )

        connector = GoogleSheetsConnector(
            credentials=google_credentials
        )

        # ---------------------------------------------------------
        # Read raw physical values.
        #
        # read_column_cells intentionally preserves whitespace,
        # which is required for deterministic rule evidence.
        # ---------------------------------------------------------

        cells = connector.read_column_cells(
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name,
            column_name=source_column,
            header_row=header_row,
            limit=profile_record_count,
        )

        # ---------------------------------------------------------
        # Translate connector-specific records into the
        # connector-neutral rule-preview contract.
        # ---------------------------------------------------------

        values = [
            {
                "source_row_id": (
                    str(cell.get("row_number"))
                    if cell.get("row_number") is not None
                    else None
                ),
                "record_id": None,
                "value": cell.get("value"),
            }
            for cell in cells
        ]

        # ---------------------------------------------------------
        # Deterministic preview only.
        # No source mutation occurs.
        # ---------------------------------------------------------

        try:
            result = (
                profile_rule_enhancement_service
                .preview_candidate(
                    organization_id=organization_id,
                    candidate_id=candidate_id,
                    values=values,
                    preview_limit=25,
                )
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

        return ProfileRulePreviewResponse(
            **result
        )

@router.post(
    "/{candidate_id}/reject",
    response_model=ProfileRuleRejectResponse,
)
def reject_profile_rule_candidate(
    candidate_id: str,
    request: ProfileRuleRejectRequest,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
):
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    rejected_by = str(current_user.email)

    # ---------------------------------------------------------
    # Load tenant-bound PREVIEWED candidate
    # ---------------------------------------------------------

    candidate = (
        quality_profiler_repository
        .get_profile_rule_candidate(
            organization_id=organization_id,
            candidate_id=candidate_id,
        )
    )

    if not candidate:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile rule candidate was not found.",
        )

    candidate_status = str(
        candidate.get("status") or ""
    ).strip().upper()

    if candidate_status != "PREVIEWED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Profile rule candidate must be previewed "
                "before steward rejection."
            ),
        )

    profile_id = str(
        candidate.get("profile_id") or ""
    ).strip()

    profile_run_id = str(
        candidate.get("profile_run_id") or ""
    ).strip()

    field_name = str(
        candidate.get("field_name") or ""
    ).strip()

    if not profile_id or not profile_run_id or not field_name:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Profile rule candidate is missing required "
                "governance context."
            ),
        )

    # ---------------------------------------------------------
    # False-positive evidence is optional, but when supplied
    # it MUST be verified against the governed source.
    # ---------------------------------------------------------

    selected_row_ids = {
        str(row_id).strip()
        for row_id in request.false_positive_source_row_ids
        if str(row_id).strip()
    }

    rejection_evidence = []

    if selected_row_ids:
        source_context = (
            quality_profiler_repository
            .get_profile_source_context(
                organization_id=organization_id,
                profile_run_id=profile_run_id,
                profile_id=profile_id,
            )
        )

        if not source_context:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Governed profile source context could "
                    "not be resolved."
                ),
            )

        source_type = str(
            source_context.get("source_type") or ""
        ).strip().upper()

        if source_type != "GOOGLE_SHEETS":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "False-positive evidence verification "
                    "currently supports Google Sheets only."
                ),
            )

        connection_id = str(
            source_context.get("connection_id") or ""
        ).strip()

        spreadsheet_id = str(
            source_context.get("spreadsheet_id") or ""
        ).strip()

        sheet_name = str(
            source_context.get("sheet_name") or ""
        ).strip()

        if (
            not connection_id
            or not spreadsheet_id
            or not sheet_name
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Governed source context is incomplete."
                ),
            )

        try:
            header_row = int(
                source_context.get("header_row") or 1
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Governed source context contains an "
                    "invalid header_row."
                ),
            ) from exc

        summary = (
            quality_profiler_repository
            .get_profile_summary(
                organization_id=organization_id,
                profile_run_id=profile_run_id,
            )
        )

        if not summary:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Profile summary could not be resolved."
                ),
            )

        try:
            profile_record_count = int(
                summary.get("total_records") or 0
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Profile summary contains an invalid "
                    "total_records value."
                ),
            ) from exc

        if (
            profile_record_count < 1
            or profile_record_count > 100_000
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Governed profile record count is "
                    "outside the supported preview range."
                ),
            )

        try:
            source_column = (
                profile_rule_enhancement_service
                ._resolve_source_column(
                    organization_id=organization_id,
                    profile_run_id=profile_run_id,
                    field_name=field_name,
                )
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

        google_credentials = (
            connection_service
            .get_google_oauth_credentials(
                connection_id=connection_id,
                organization_id=organization_id,
            )
        )

        connector = GoogleSheetsConnector(
            credentials=google_credentials
        )

        cells = connector.read_column_cells(
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name,
            column_name=source_column,
            header_row=header_row,
            limit=profile_record_count,
        )

        cells_by_row_id = {
            str(cell.get("row_number")): cell
            for cell in cells
            if cell.get("row_number") is not None
        }

        missing_row_ids = (
            selected_row_ids
            - set(cells_by_row_id)
        )

        if missing_row_ids:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "One or more selected false-positive "
                    "rows could not be verified against the "
                    "governed profile source."
                ),
            )

        for row_id in sorted(
            selected_row_ids,
            key=lambda value: int(value),
        ):
            cell = cells_by_row_id[row_id]

            rejection_evidence.append(
                {
                    "source_row_id": row_id,
                    "field_name": field_name,
                    "current_value": cell.get("value"),
                    "classification": "FALSE_POSITIVE",
                }
            )

    # ---------------------------------------------------------
    # Persist steward decision + verified learning evidence.
    #
    # No rule activation.
    # No source mutation.
    # ---------------------------------------------------------

    try:
        rejected = (
            quality_profiler_repository
            .reject_profile_rule_candidate(
                organization_id=organization_id,
                candidate_id=candidate_id,
                rejected_by=rejected_by,
                rejection_reason=request.reason,
                steward_guidance=(
                    request.steward_guidance
                ),
                rejection_evidence=rejection_evidence,
            )
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return ProfileRuleRejectResponse(
        organization_id=organization_id,
        candidate_id=rejected["candidate_id"],
        profile_id=rejected["profile_id"],
        profile_run_id=rejected["profile_run_id"],
        status="REJECTED",
        false_positive_count=len(
            rejection_evidence
        ),
        rejected_by=rejected["rejected_by"],
        rejected_at=rejected["rejected_at"],
        rejection_reason=rejected[
            "rejection_reason"
        ],
        steward_guidance=rejected.get(
            "steward_guidance"
        ),
        rule_activated=False,
        source_mutated=False,
        requires_reprofile=False,
    )

@router.get(
    "/active",
    response_model=list[ActiveProfileRuleResponse],
)
def list_active_profile_rules(
    profile_id: str,
    field_name: str | None = None,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
):
    organization_id = require_current_organization_id(
        current_user
    )

    rows = (
        quality_profiler_repository
        .list_active_profile_rules(
            organization_id=organization_id,
            profile_id=profile_id,
            field_name=field_name,
        )
    )

    return [
        ActiveProfileRuleResponse(
            rule_id=row["rule_id"],
            version=int(row["version"]),
            logical_key=row["logical_key"],
            scope=row["scope"],
            family=row["family"],
            evaluator=row["evaluator"],
            status=row["status"],
            severity=row["severity"],
            domain=row["domain"],
            field_name=row["field_name"],
            description=row.get("description"),
            remediation_available=bool(
                row.get("remediation_available")
            ),
            remediation_type=row.get(
                "remediation_type"
            ),
        )
        for row in rows
    ]

@router.get(
    "/candidates/{candidate_id}/governance-identity",
    response_model=ProfileRuleGovernanceIdentityResponse,
)
def resolve_profile_rule_governance_identity(
    candidate_id: str,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
):
    organization_id = require_current_organization_id(
        current_user
    )

    resolved_by = str(
        current_user.email
        or current_user.user_id
        or ""
    ).strip()

    if not resolved_by:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Authenticated user identity is required "
                "for governance resolution."
            ),
        )

    return profile_rule_enhancement_service.resolve_candidate_governance_identity(
        organization_id=organization_id,
        candidate_id=candidate_id,
        resolved_by=resolved_by,
    )



@router.post(
    "/{candidate_id}/approve",
    response_model=ProfileRuleApproveResponse,
)
def approve_profile_rule_candidate(
    candidate_id: str,
    request: ProfileRuleApproveRequest,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
):
    organization_id = require_current_organization_id(
        current_user
    )

    approved_by = str(
        current_user.email or ""
    ).strip()

    if not approved_by:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Authenticated user email is required "
                "to approve a profile rule."
            ),
        )

    try:
        service = ProfileRuleEnhancementService(
            quality_repository=quality_profiler_repository,
        )

        result = service.approve_candidate(
            organization_id=organization_id,
            candidate_id=candidate_id,
            approved_by=approved_by,
        )

        return ProfileRuleApproveResponse(
            organization_id=result["organization_id"],
            candidate_id=result["candidate_id"],
            rule_id=result["rule_id"],
            profile_id=result["profile_id"],
            logical_key=result["logical_key"],
            profile_run_id=result["profile_run_id"],
            scope=result["scope"],
            status=result["status"],
            version=result["version"],
            field_name=result["field_name"],
            remediation_action=result[
                "remediation_action"
            ],
            approved_by=result["approved_by"],
            approved_at=result["approved_at"],
            requires_reprofile=result[
                "requires_reprofile"
            ],
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Profile rule approval failed. "
                f"{exc}"
            ),
        ) from exc