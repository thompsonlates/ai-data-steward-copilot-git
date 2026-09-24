"""Behavior-preserving route extraction from the former monolithic routes.py."""

from app.api.route_dependencies import *
from app.api.route_dependencies import (
    _get_cached_dq_rule_suggestions,
    _set_cached_dq_rule_suggestions,
    _get_onedrive_connector,
)

router = APIRouter()


@router.post(
    "/connections/csv/preview",
    response_model=CsvPreviewResponse,
)
async def preview_csv_upload(
    file: UploadFile = File(...),
    preview_rows: int = Form(25),
    current_user: AuthUser = Depends(
        get_current_tenant_user
    ),
) -> CsvPreviewResponse:
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    try:
        file_bytes = await file.read()

        result = (
            csv_ingestion_service.preview_csv(
                organization_id=organization_id,
                file_name=file.filename,
                file_bytes=file_bytes,
                preview_rows=preview_rows,
            )
        )

        return CsvPreviewResponse(
            organization_id=organization_id,
            file_name=result.file_name,
            columns=result.columns,
            rows=result.rows,
            total_preview_rows=(
                result.total_preview_rows
            ),
            delimiter=result.delimiter,
            encoding=result.encoding,
        )

    except CsvIngestionError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "CSV preview failed for "
            "organization_id=%s",
            organization_id,
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to preview CSV file: "
                f"{str(exc)}"
            ),
        ) from exc


@router.post(
    "/dq/profile/csv",
    response_model=CsvProfileResponse,
)
async def profile_csv_upload(
    file: UploadFile = File(...),
    domain: str = Form(...),
    business_key_field: str = Form(...),
    column_mappings: str = Form(...),
    minimum_record_score: float = Form(80.0),
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
) -> CsvProfileResponse:
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    try:
        normalized_domain = str(
            domain or ""
        ).strip().upper()

        if not normalized_domain:
            raise ValueError(
                "domain is required."
            )

        normalized_business_key = str(
            business_key_field or ""
        ).strip()

        if not normalized_business_key:
            raise ValueError(
                "business_key_field is required."
            )

        try:
            raw_mappings = json.loads(
                column_mappings
            )
        except json.JSONDecodeError as exc:
            raise ValueError(
                "column_mappings must be valid JSON."
            ) from exc

        if not isinstance(raw_mappings, list):
            raise ValueError(
                "column_mappings must be a JSON array."
            )

        normalized_mappings = []

        seen_source_columns: set[str] = set()
        seen_target_fields: set[str] = set()

        for item in raw_mappings:
            if not isinstance(item, dict):
                raise ValueError(
                    "Each column mapping must be "
                    "a JSON object."
                )

            source_column = str(
                item.get("source_column")
                or ""
            ).strip()

            target_field = str(
                item.get("target_field")
                or ""
            ).strip()

            if not source_column:
                raise ValueError(
                    "source_column is required "
                    "for every mapping."
                )

            if not target_field:
                raise ValueError(
                    "target_field is required "
                    "for every mapping."
                )

            if (
                source_column
                in seen_source_columns
            ):
                raise ValueError(
                    "Each CSV source column can "
                    "only be mapped once."
                )

            if (
                target_field
                in seen_target_fields
            ):
                raise ValueError(
                    "Each target field can only "
                    "be mapped once."
                )

            seen_source_columns.add(
                source_column
            )

            seen_target_fields.add(
                target_field
            )

            normalized_mappings.append(
                {
                    "source_column":
                        source_column,
                    "target_field":
                        target_field,
                }
            )

        if not normalized_mappings:
            raise ValueError(
                "At least one column mapping "
                "is required."
            )

        if (
            normalized_business_key
            not in seen_target_fields
        ):
            raise ValueError(
                "business_key_field must be "
                "included in column_mappings."
            )

        file_bytes = await file.read()

        parsed = (
            csv_ingestion_service
            .parse_csv_records(
                organization_id=(
                    organization_id
                ),
                file_name=file.filename,
                file_bytes=file_bytes,
            )
        )

        source_columns = set(
            parsed["columns"]
        )

        for mapping in normalized_mappings:
            if (
                mapping["source_column"]
                not in source_columns
            ):
                raise ValueError(
                    "Mapped CSV column was not "
                    "found in the uploaded file: "
                    f"{mapping['source_column']}"
                )

        mapped_rows = []

        for source_row in parsed["records"]:
            mapped_row = {
                mapping["target_field"]:
                    source_row.get(
                        mapping[
                            "source_column"
                        ]
                    )
                for mapping
                in normalized_mappings
            }

            mapped_rows.append(
                mapped_row
            )

        if not mapped_rows:
            raise ValueError(
                "CSV file contains no data rows."
            )


        source_name = (
            f"CSV:{parsed['file_name']}"
        )

        config = build_quality_profile_config(
                domain=normalized_domain,
                source_name=source_name,
                business_key_field=normalized_business_key,
                mapped_fields={
                    mapping["target_field"]
                    for mapping in normalized_mappings
                },
                minimum_record_score=minimum_record_score,
)

        result = (
            csv_quality_profiler_service
            .profile_rows(
                organization_id=(
                    organization_id
                ),
                rows=mapped_rows,
                config=config,
                source_name=source_name,
            )
        )

        csv_quality_profiler_repository.save_profile_result(
            result=result
        )

        stage_table_id = (
            csv_quality_profiler_repository
            .materialize_profile_rows(
                organization_id=organization_id,
                profile_run_id=result.profile_run_id,
                rows=mapped_rows,
                source_type="CSV",
                source_name=source_name,
                domain=result.domain,
            )
        )

        logger.info(
            "CSV DQ profile staging ready. "
            "organization_id=%s profile_run_id=%s table_id=%s",
            organization_id,
            result.profile_run_id,
            stage_table_id,
        )

        return CsvProfileResponse(
            organization_id=organization_id,
            profile_run_id=(
                result.profile_run_id
            ),
            universal_rules_shadow_enabled=(
            result.universal_rules_shadow_enabled
            ),
            universal_rules_shadow=(
                result.universal_rules_shadow
            ),
            file_name=parsed["file_name"],
            domain=result.domain,
            record_count=parsed[
                "record_count"
            ],
            total_records=(
                result.total_records
            ),
            avg_record_score=(
                result.avg_record_score
            ),
            records_below_threshold=(
                result.records_below_threshold
            ),
            records_with_findings=(
                result.records_with_findings
            ),
            total_findings=(
                result.total_findings
            ),
            duplicate_record_count=(
                result.duplicate_record_count
            ),
            source_type="CSV",
            generated_at=(
                result.generated_at
            ),
        )

    except (
        CsvIngestionError,
        ValueError,
    ) as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "CSV DQ profile failed for "
            "organization_id=%s",
            organization_id,
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to profile CSV file: "
                f"{str(exc)}"
            ),
        ) from exc
