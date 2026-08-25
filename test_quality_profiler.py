from app.services.quality_profiler_service import QualityProfilerService
from app.services.quality_profiler_service import (
    QualityFieldConfig,
    QualityProfileConfig,
    QualityProfilerService,
)


def main():
    profiler = QualityProfilerService()

    rows = [
        {
            "source_row_id": "ROW-001",
            "supplier_id": "SUP001",
            "supplier_name": "Halperns Steak and Seafood",
            "tax_id": "12-3456789",
            "email": "vendor@halperns.com",
            "address": "100 Main Street",
            "source_system": "ERP",
        },
        {
            "source_row_id": "ROW-002",
            "supplier_id": "SUP001",
            "supplier_name": None,
            "tax_id": "BAD-TAX-ID",
            "email": "bad-email",
            "address": "100 Main St",
            "source_system": "SUPPLIER_PORTAL",
        },
        {
            "source_row_id": "ROW-003",
            "supplier_id": "SUP003",
            "supplier_name": "Fresh Foods Distribution",
            "tax_id": "98-7654321",
            "email": "contact@freshfoods.com",
            "address": "500 Distribution Way",
            "source_system": "ERP",
        },
    ]

    config = QualityProfileConfig(
        domain="SUPPLIER",
        source_table="unused.for.sheet",
        business_key_field="supplier_id",
        minimum_record_score=85.0,
        fields=[
            QualityFieldConfig(
                field_name="supplier_id",
                required=True,
                uniqueness_key=True,
                weight=2.0,
                severity="CRITICAL",
            ),
            QualityFieldConfig(
                field_name="supplier_name",
                required=True,
                weight=2.0,
                severity="HIGH",
            ),
            QualityFieldConfig(
                field_name="tax_id",
                required=True,
                regex_pattern=r"^\d{2}-?\d{7}$",
                weight=2.0,
                severity="HIGH",
            ),
            QualityFieldConfig(
                field_name="email",
                regex_pattern=(
                    r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
                ),
                weight=1.0,
                severity="MEDIUM",
            ),
        ],
    )

    result = profiler.profile_rows(
        organization_id="org_00003",
        rows=rows,
        config=config,
        source_name="TEST:IN_MEMORY",
    )

    print("\n===== PROFILE SUMMARY =====")
    print("Profile Run ID:", result.profile_run_id)
    print("Organization:", result.organization_id)
    print("Domain:", result.domain)
    print("Source:", result.source_table)
    print("Total Records:", result.total_records)
    print("Average Record Score:", result.avg_record_score)
    print("Average Completeness:", result.avg_completeness_score)
    print("Average Validity:", result.avg_validity_score)
    print("Average Uniqueness:", result.avg_uniqueness_score)
    print("Duplicate Record Count:", result.duplicate_record_count)
    print("Records Below Threshold:", result.records_below_threshold)
    print("Records With Findings:", result.records_with_findings)
    print("Total Findings:", result.total_findings)
    print("Critical:", result.critical_findings)
    print("High:", result.high_findings)
    print("Medium:", result.medium_findings)
    print("Low:", result.low_findings)

    print("\n===== FINDINGS =====")
    for finding in result.findings:
        print(
            finding.source_row_id,
            "|",
            finding.record_id,
            "|",
            finding.rule_id,
            "|",
            finding.dimension,
            "|",
            finding.severity,
            "|",
            finding.finding_message,
            "| observed:",
            finding.observed_value,
        )

    print("\n===== RECORD SCORES =====")
    for score in result.record_scores:
        print(
            score.source_row_id,
            "|",
            score.record_id,
            "| overall:",
            score.overall_score,
            "| completeness:",
            score.completeness_score,
            "| validity:",
            score.validity_score,
            "| uniqueness:",
            score.uniqueness_score,
            "| issues:",
            score.issue_count,
        )


if __name__ == "__main__":
    main()