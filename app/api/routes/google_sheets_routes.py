"""Behavior-preserving route extraction from the former monolithic routes.py."""

from app.api.route_dependencies import *
from app.api.route_dependencies import (
    _get_cached_dq_rule_suggestions,
    _set_cached_dq_rule_suggestions,
    _get_onedrive_connector,
)

from app.services.profile_rule_enhancement_service import (
    ProfileRuleEnhancementService,
)

router = APIRouter()


@router.post(
    "/connections/google-sheets/test",
)
def test_google_sheets_connection(
    req: GoogleSheetsConnectionTestRequest,
    current_user: AuthUser = Depends(
        get_current_tenant_user
    ),
):
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    google_credentials = (
    connection_service
    .get_google_oauth_credentials(
        connection_id=req.connection_id,
        organization_id=organization_id,
    )
)

    connector = GoogleSheetsConnector(
        credentials=google_credentials
    )

    # ---------------------------------------------------------
    # Execute actual Google Sheets connectivity test
    # ---------------------------------------------------------

    result = connector.test_connection(
        spreadsheet_id=req.spreadsheet_url
    )

    sheets = connector.list_sheets(
        spreadsheet_id=req.spreadsheet_url
    )

    # ---------------------------------------------------------
    # Persist successful health status
    # Only reached if Google test/list calls succeeded.
    # ---------------------------------------------------------

    connection_repository.update_health_status(
        connection_id=req.connection_id,
        organization_id=organization_id,
        health_status="HEALTHY",
        updated_at=datetime.now(timezone.utc),
    )

    return {
        "success": True,
        "organization_id": organization_id,
        "connection_id": req.connection_id,
        "spreadsheet_id": result["spreadsheet_id"],
        "spreadsheet_title": result.get(
            "spreadsheet_title"
        ),
        "sheets": sheets,
        "health_status": "HEALTHY",
    }


@router.post(
    "/connections/google-sheets/preview",
)
def preview_google_sheet(
    req: GoogleSheetsPreviewRequest,
    current_user: AuthUser = Depends(
        get_current_tenant_user
    ),
):
    organization_id = require_current_organization_id(
    current_user
)

    logger.warning(
        "VANILLA GOOGLE SHEETS PREVIEW ROUTE ACTIVE organization_id=%s",
        organization_id,
    )

    google_credentials = (
        connection_service
        .get_google_oauth_credentials(
            connection_id=req.connection_id,
            organization_id=organization_id,
        )
    )

    connector = GoogleSheetsConnector(
        credentials=google_credentials
    )

    rows = connector.read_rows(
        spreadsheet_id=req.spreadsheet_url,
        sheet_name=req.sheet_name,
        header_row=req.header_row,
        limit=req.preview_limit,
    )

    headers = (
        list(rows[0].keys())
        if rows
        else []
    )

    return {
        "success": True,
        "organization_id": organization_id,
        "sheet_name": req.sheet_name,
        "headers": headers,
        "rows": rows,
    }


@router.post(
    "/dq/profile/google-sheets",
)
def profile_google_sheet(
    req: GoogleSheetsProfileRequest,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
):
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    google_credentials = (
    connection_service
    .get_google_oauth_credentials(
        connection_id=req.connection_id,
        organization_id=organization_id,
    )
)

    connector = GoogleSheetsConnector(
        credentials=google_credentials
    )

    raw_rows = connector.read_rows(
            spreadsheet_id=req.spreadsheet_url,
            sheet_name=req.sheet_name,
            header_row=req.header_row,
            limit=100_000,
        )
        
    mapping = {
            item.source_column: item.target_field
            for item in req.column_mappings
        }

    normalized_rows = []

    for raw_row in raw_rows:
        normalized_row = {}

        for source_column, target_field in mapping.items():
            normalized_row[target_field] = (
                raw_row.get(source_column)
            )

        normalized_rows.append(
            normalized_row
        )

    normalized_domain = (
        req.domain.strip().upper()
    )
    # ---------------------------------------------------------
    # Durable governed profile identity
    # ---------------------------------------------------------

    if req.profile_id:
        durable_profile = quality_profiler_repository.get_profile(
            organization_id=organization_id,
            profile_id=req.profile_id,
        )

        if durable_profile is None:
            raise ValueError(
                "Durable profile was not found for this organization."
            )

        profile_id = durable_profile["profile_id"]

        if str(durable_profile.get("domain") or "").strip().upper() != normalized_domain:
            raise ValueError(
                "Durable profile domain does not match the requested domain."
            )

    else:
        source_name = f"GOOGLE_SHEETS:{req.sheet_name}"

        durable_profile = (
            quality_profiler_repository.find_active_profile(
                organization_id=organization_id,
                domain=normalized_domain,
                source_name=source_name,
                business_key_field=req.business_key_field,
            )
        )

        if durable_profile is None:
            profile_name = (
                (req.profile_name or "").strip()
                or f"{normalized_domain} - {req.sheet_name}"
            )

            durable_profile = (
                quality_profiler_repository.create_profile(
                    organization_id=organization_id,
                    profile_name=profile_name,
                    domain=normalized_domain,
                    source_name=source_name,
                    business_key_field=req.business_key_field,
                    created_by=str(
                        getattr(current_user, "email", None)
                        or getattr(current_user, "user_id", None)
                        or "system"
                    ),
            )
        )

    profile_id = durable_profile["profile_id"]

    config = build_quality_profile_config(
        profile_id=profile_id,
        domain=normalized_domain,
        source_name=f"GOOGLE_SHEETS:{req.sheet_name}",
        business_key_field=req.business_key_field,
        mapped_fields={
            item.target_field
            for item in req.column_mappings
        },
        minimum_record_score=85.0,
    )

    profile_rule_enhancement_service = (
    ProfileRuleEnhancementService(
        quality_repository=quality_profiler_repository,
    )
)

    profiler = QualityProfilerService()


    result = profiler.profile_rows(
        organization_id=organization_id,
        rows=normalized_rows,
        config=config,
        source_name=f"GOOGLE_SHEETS:{req.sheet_name}",
    )

    quality_profiler_repository.save_profile_result(
        result=result,
        column_mappings=[
            {
                "source_column": item.source_column,
                "target_field": item.target_field,
            }
            for item in req.column_mappings
        ],
    )

    stage_table_id = (
        quality_profiler_repository
        .materialize_profile_rows(
            organization_id=organization_id,
            profile_run_id=result.profile_run_id,
            rows=normalized_rows,
            source_type="GOOGLE_SHEETS",
            source_name=(
                f"GOOGLE_SHEETS:{req.sheet_name}"
            ),
            domain=result.domain,
        )
    )

    quality_profiler_repository.save_profile_source_context(
        organization_id=organization_id,
        profile_run_id=result.profile_run_id,
        profile_id=profile_id,
        source_type="GOOGLE_SHEETS",
        connection_id=req.connection_id,
        spreadsheet_id=req.spreadsheet_url,
        sheet_name=req.sheet_name,
        header_row=req.header_row,
    )
    # ---------------------------------------------------------
    # Universal Intelligence - Profile Rule Pattern Discovery
    # Run only after the profile run has been persisted and
    # associated with its durable governed profile.
    # ---------------------------------------------------------

    print(
        "UNIVERSAL DISCOVERY GATE DEBUG",
        {
            "enabled": result.universal_rules_shadow_enabled,
            "shadow_is_none": (
                result.universal_rules_shadow is None
            ),
            "shadow_type": type(
                result.universal_rules_shadow
            ).__name__,
            "profile_id": profile_id,
            "profile_run_id": result.profile_run_id,
        },
        flush=True,
    )

    if (
        result.universal_rules_shadow_enabled
        and result.universal_rules_shadow is not None
    ):
        values_by_field: dict[
            str,
            list[dict[str, Any]],
        ] = {}

        for field_config in config.fields:
            field_name = str(
                field_config.field_name or ""
            ).strip().lower()

            if not field_name:
                continue

            values_by_field[field_name] = [
                {
                    "source_row_id": str(
                        row_index + 1
                    ),
                    "record_id": str(
                        row.get(
                            config.business_key_field
                        )
                        or ""
                    ),
                    "value": row.get(field_name),
                }
                for row_index, row in enumerate(
                    normalized_rows
                )
            ]

        candidate_opportunities = (
            profile_rule_enhancement_service
            .discover_rule_opportunities(
                organization_id=organization_id,
                profile_id=profile_id,
                profile_run_id=result.profile_run_id,
                values_by_field=values_by_field,
            )
        )
        print(
            "UNIVERSAL DISCOVERY RESULT DEBUG",
            {
                "opportunity_count": len(
                    candidate_opportunities
                ),
                "opportunities": [
                    {
                        "field_name": item.field_name,
                        "pattern_summary": (
                            item.pattern_summary
                        ),
                        "affected_record_count": (
                            item.affected_record_count
                        ),
                    }
                    for item in candidate_opportunities
                ],
            },
            flush=True,
        )

        result.universal_rules_shadow[
            "candidate_opportunities"
        ] = [
            {
                "field_name": item.field_name,
                "pattern_summary": item.pattern_summary,
                "affected_record_count": (
                    item.affected_record_count
                ),
                "sample_values": item.sample_values,
                "suggested_rule_type": (
                    item.suggested_rule_type
                ),
                "evidence_examples": [
                {
                    "current_value": (
                        evidence.current_value
                    ),
                    "proposed_value": (
                        evidence.proposed_value
                    ),
                    "affected_record_count": (
                        evidence.affected_record_count
                    ),
                }
                    for evidence in item.evidence_examples
            ],
                "suggested_operator": (
                    item.suggested_operator
                ),
                "suggested_comparison_value": (
                    item.suggested_comparison_value
                ),
                "suggested_action": (
                    item.suggested_action
                ),
                "suggested_replacement_value": (
                    item.suggested_replacement_value
                ),
                "confidence": item.confidence,
                "explanation": item.explanation,
                "requires_steward_review": (
                    item.requires_steward_review
                ),
            }
            for item in candidate_opportunities
            
        ]

        logger.info(
            "Universal profile rule discovery complete. "
            "organization_id=%s profile_id=%s "
            "profile_run_id=%s opportunity_count=%s",
            organization_id,
            profile_id,
            result.profile_run_id,
            len(candidate_opportunities),
        )

    logger.info(
        "Google Sheets DQ profile staging ready. "
        "organization_id=%s profile_run_id=%s table_id=%s",
        organization_id,
        result.profile_run_id,
        stage_table_id,
    )

    return {
        "success": True,
        "organization_id": organization_id,
        "profile_run_id": result.profile_run_id,
        "profile_id": profile_id,
        "universal_rules_shadow_enabled": (
            result.universal_rules_shadow_enabled
        ),
        "universal_rules_shadow": (
            result.universal_rules_shadow
        ),
        "domain": result.domain,
        "total_records": result.total_records,
        "avg_record_score": result.avg_record_score,
        "records_below_threshold": (
            result.records_below_threshold
        ),
        "records_with_findings": (
            result.records_with_findings
        ),
        "total_findings": (
            result.total_findings
        ),
        "duplicate_record_count": (
            result.duplicate_record_count
        ),
    }


@router.post(
    "/dq/recommendations/"
    "{recommendation_id}/execute-google-sheets",
    response_model=DqExecutionResponse,
    status_code=status.HTTP_200_OK,
)
def execute_google_sheets_dq_remediation(
    recommendation_id: str,
    req: GoogleSheetsDqExecutionRequest,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
) -> DqExecutionResponse:
    """
    Execute the exact persisted and steward-approved REGEX remediation
    against a tenant-authorized Google Sheet.

    This route is intentionally separate from SQL execution. It never
    interprets SQL for Google Sheets and requires explicit confirmation.
    """

    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    # Subscription entitlement gate: governed Google Sheets REGEX
    # execution requires Steward Intelligence + explicit REGEX execution access.
    entitlement_service.require_governed_regex_execution(
        organization_id=organization_id,
    )

    normalized_recommendation_id = str(
        recommendation_id or ""
    ).strip()

    if not normalized_recommendation_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="recommendation_id is required.",
        )

    requested_by = str(
        current_user.email or ""
    ).strip()

    if not requested_by:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Authenticated user email is required "
                "for Google Sheets DQ execution."
            ),
        )

    if req.confirm_execution is not True:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Explicit confirmation is required before "
                "executing Google Sheets DQ remediation."
            ),
        )

    try:
        result = (
            dq_execution_service
            .execute_approved_google_sheets_regex_remediation(
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
                spreadsheet_id=(
                    req.spreadsheet_id
                ),
                sheet_name=(
                    req.sheet_name
                ),
                header_row=(
                    req.header_row
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

        if returned_organization_id != organization_id:
            logger.error(
                "Cross-tenant Google Sheets DQ execution "
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
                    "Security validation failed: Google Sheets "
                    "execution response belongs to a different "
                    "organization."
                ),
            )

        return DqExecutionResponse(
            **result
        )

    except HTTPException:
        raise

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "Google Sheets DQ remediation execution route failed. "
            "organization_id=%s recommendation_id=%s "
            "connection_id=%s error=%s",
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
                "Unable to execute Google Sheets DQ remediation."
            ),
        ) from exc
