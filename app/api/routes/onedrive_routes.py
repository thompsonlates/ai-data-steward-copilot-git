"""Behavior-preserving route extraction from the former monolithic routes.py."""

from app.api.route_dependencies import *
from app.api.route_dependencies import (
    _get_cached_dq_rule_suggestions,
    _set_cached_dq_rule_suggestions,
    _get_onedrive_connector,
)

router = APIRouter()


@router.post(
    "/connections/onedrive/test",
    response_model=OneDriveConnectionTestResponse,
)
def test_onedrive_connection(
    req: OneDriveConnectionTestRequest,
    current_user: AuthUser = Depends(
        get_current_tenant_user
    ),
):
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    connector = _get_onedrive_connector(
        connection_id=req.connection_id,
        organization_id=organization_id,
    )

    workbook = connector.resolve_workbook(
        sharing_url=req.workbook_url
    )

    result = connector.test_connection(
        drive_id=workbook["drive_id"],
        item_id=workbook["item_id"],
    )

    sheets = connector.list_sheets(
        drive_id=workbook["drive_id"],
        item_id=workbook["item_id"],
    )

    connection_repository.update_health_status(
        connection_id=req.connection_id,
        organization_id=organization_id,
        health_status="HEALTHY",
        updated_at=datetime.now(timezone.utc),
    )

    return OneDriveConnectionTestResponse(
        success=True,
        drive_id=workbook["drive_id"],
        item_id=workbook["item_id"],
        workbook_name=(
            result.get("workbook_name")
            or workbook.get("name")
        ),
        sheets=sheets,
    )


@router.post(
    "/connections/onedrive/preview",
    response_model=OneDrivePreviewResponse,
)
def preview_onedrive_workbook(
    req: OneDrivePreviewRequest,
    current_user: AuthUser = Depends(
        get_current_tenant_user
    ),
):
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    connector = _get_onedrive_connector(
        connection_id=req.connection_id,
        organization_id=organization_id,
    )

    workbook = connector.resolve_workbook(
        sharing_url=req.workbook_url
    )

    rows = connector.read_rows(
        drive_id=workbook["drive_id"],
        item_id=workbook["item_id"],
        sheet_name=req.sheet_name,
        header_row=req.header_row,
        limit=req.preview_limit,
    )

    headers = (
        list(rows[0].keys())
        if rows
        else []
    )

    return OneDrivePreviewResponse(
        headers=headers,
        rows=rows,
        row_count=len(rows),
    )


@router.post("/dq/profile/onedrive")
def profile_onedrive_workbook(
    req: OneDriveProfileRequest,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
):
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    connector = _get_onedrive_connector(
        connection_id=req.connection_id,
        organization_id=organization_id,
    )

    workbook = connector.resolve_workbook(
        sharing_url=req.workbook_url
    )

    raw_rows = connector.read_rows(
        drive_id=workbook["drive_id"],
        item_id=workbook["item_id"],
        sheet_name=req.sheet_name,
        header_row=req.header_row,
        limit=100_000,
    )

    mapping = {
        item.source_column: item.target_field
        for item in req.column_mappings
    }

    normalized_rows = [
        {
            target_field: raw_row.get(source_column)
            for source_column, target_field
            in mapping.items()
        }
        for raw_row in raw_rows
    ]

    normalized_domain = (
        req.domain.strip().upper()
    )

    source_name = (
        f"ONEDRIVE_EXCEL:{req.sheet_name}"
    )

    config = build_quality_profile_config(
        domain=normalized_domain,
        source_name=source_name,
        business_key_field=req.business_key_field,
        mapped_fields={
            item.target_field
            for item in req.column_mappings
        },
        minimum_record_score=85.0,
    )

    profiler = QualityProfilerService()
    print(
    "ONEDRIVE UNIVERSAL SHADOW DEBUG:",
    {
        "env": os.getenv("ADMS_UNIVERSAL_PROFILE_RULES_SHADOW"),
        "service_enabled": profiler.enable_universal_rules_shadow,
    },
)

    result = profiler.profile_rows(
        organization_id=organization_id,
        rows=normalized_rows,
        config=config,
        source_name=source_name,
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
            source_type="ONEDRIVE_EXCEL",
            source_name=source_name,
            domain=result.domain,
        )
    )

    quality_profiler_repository.save_profile_source_context(
        organization_id=organization_id,
        profile_run_id=result.profile_run_id,
        source_type="ONEDRIVE_EXCEL",
        connection_id=req.connection_id,
        drive_id=workbook["drive_id"],
        item_id=workbook["item_id"],
        sheet_name=req.sheet_name,
        header_row=req.header_row,
    )

    logger.info(
        "OneDrive Excel DQ profile staging ready. "
        "organization_id=%s profile_run_id=%s "
        "drive_id=%s item_id=%s table_id=%s",
        organization_id,
        result.profile_run_id,
        workbook["drive_id"],
        workbook["item_id"],
        stage_table_id,
    )

    return {
        "success": True,
        "organization_id": organization_id,
        "profile_run_id": result.profile_run_id,
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
        "total_findings": result.total_findings,
        "duplicate_record_count": (
            result.duplicate_record_count
        ),
        "drive_id": workbook["drive_id"],
        "item_id": workbook["item_id"],
        "workbook_name": workbook.get("name"),
        "universal_rules_shadow_enabled": (
            result.universal_rules_shadow_enabled
        ),
        "universal_rules_shadow": (
            result.universal_rules_shadow
        ),
    }


@router.post(
    "/dq/recommendations/{recommendation_id}/execute-onedrive",
    response_model=DqExecutionResponse,
)
def execute_onedrive_dq_remediation(
    recommendation_id: str,
    req: OneDriveDqExecutionRequest,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
):
    """
    Execute one exact, steward-approved REGEX remediation against
    an Excel workbook stored in OneDrive / SharePoint.

    Governance:
      * tenant identity comes only from the authenticated user
      * governed REGEX execution entitlement is enforced server-side
      * only approved, non-blocked REGEX artifacts may execute
      * only allow-listed deterministic rules may execute
      * only DEV / STG Microsoft connections may write
      * exact target cells are write-safety checked before mutation
      * successful execution requires re-profile verification
    """

    organization_id = require_current_organization_id(
        current_user
    )

    # Defense-in-depth at the API boundary.
    # DqExecutionService enforces this again before execution.
    entitlement_service.require_governed_regex_execution(
        organization_id=organization_id,
    )

    requested_by = str(current_user.email).strip().lower()

    if not requested_by:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Authenticated user email is required for "
                "governed OneDrive execution."
            ),
        )

    technical_approved_by = str(
        req.technical_approved_by or ""
    ).strip().lower()

    if not technical_approved_by:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="technical_approved_by is required.",
        )

    # Do not allow the browser to attribute technical approval
    # to another identity.
    if technical_approved_by != requested_by:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "technical_approved_by must match the "
                "authenticated user."
            ),
        )

    result = (
        dq_execution_service
        .execute_approved_onedrive_regex_remediation(
            organization_id=organization_id,
            recommendation_id=recommendation_id,
            profile_run_id=req.profile_run_id,
            connection_id=req.connection_id,
            drive_id=req.drive_id,
            item_id=req.item_id,
            sheet_name=req.sheet_name,
            header_row=req.header_row,
            requested_by=requested_by,
            technical_approved_by=technical_approved_by,
        )
    )

    # Final tenant boundary check before returning execution
    # information to the browser.
    result_organization_id = str(
        result.get("organization_id") or ""
    ).strip()

    if result_organization_id != organization_id:
        logger.error(
            "Cross-tenant OneDrive execution response blocked. "
            "authenticated_organization_id=%s "
            "result_organization_id=%s "
            "recommendation_id=%s",
            organization_id,
            result_organization_id,
            recommendation_id,
        )

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "OneDrive execution result does not belong "
                "to the authenticated organization."
            ),
        )

    return result
