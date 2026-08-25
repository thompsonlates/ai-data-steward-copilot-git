from app.workflow.connectors.google_sheets_connector import (
    GoogleSheetsConnector,
)

from app.services.quality_profiler_service import (
    QualityFieldConfig,
    QualityProfileConfig,
    QualityProfilerService,
)

from app.repositories.quality_profiler_repository import (
    QualityProfilerRepository,
)


SPREADSHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1oFt4-oJwwC_kQv9f4dnGizho_JIP9tV8xAGvyO_HqxA/"
    "edit?gid=0#gid=0"
)

SHEET_NAME = "Sheet1"


def main() -> None:
    print("\n===== GOOGLE SHEETS LOAD =====")

    connector = GoogleSheetsConnector()

    rows = connector.read_rows(
        spreadsheet_id=SPREADSHEET_URL,
        sheet_name=SHEET_NAME,
        header_row=1,
        limit=5000,
    )

    profiler = QualityProfilerService()

    config = QualityProfileConfig(
        domain="PRODUCT",
        source_table="unused.for.google.sheets",
        business_key_field="product_id",
        minimum_record_score=85.0,
        fields=[
            QualityFieldConfig(
                field_name="product_id",
                required=True,
                uniqueness_key=True,
                weight=2.0,
                severity="CRITICAL",
            ),
            QualityFieldConfig(
                field_name="product_name",
                required=True,
                uniqueness_key=False,
                weight=2.0,
                severity="HIGH",
            ),
            QualityFieldConfig(
                field_name="item_category",
                required=True,
                uniqueness_key=False,
                weight=1.0,
                severity="MEDIUM",
        ),
    ],
)

    result = profiler.profile_rows(
        organization_id="org_00003",
        rows=rows,
        config=config,
        source_name=(
            f"GOOGLE_SHEETS:{SHEET_NAME}"
        ),
    )


    repository = QualityProfilerRepository()

    repository.save_profile_result(
        result=result
    )

    print("DQ profile persisted successfully.")


if __name__ == "__main__":
    main()