from __future__ import annotations

from unittest.mock import MagicMock

from app.services.quality_profiler_service import (
    QualityFieldConfig,
    QualityProfileConfig,
    QualityProfilerService,
)


def _config() -> QualityProfileConfig:
    return QualityProfileConfig(
        domain="CUSTOMER",
        source_table="local.test.rows",
        business_key_field="member_id",
        fields=(
            QualityFieldConfig(
                field_name="member_id",
                required=True,
            ),
            QualityFieldConfig(
                field_name="email",
            ),
            QualityFieldConfig(
                field_name="full_name",
                standardization_required=True,
            ),
        ),
    )


def _rows():
    return [
        {
            "member_id": "1",
            "email": "bad email",
            "full_name": "  Matt   Thompson  ",
        },
        {
            "member_id": "2",
            "email": "matt@example.com",
            "full_name": "Jane Doe",
        },
    ]


def test_universal_rules_shadow_disabled_by_default():
    service = QualityProfilerService(
        client=MagicMock(),
        enable_universal_rules_shadow=False,
    )

    result = service.profile_rows(
        organization_id="org_test",
        rows=_rows(),
        config=_config(),
        source_name="UNIT_TEST",
    )

    assert result.universal_rules_shadow_enabled is False
    assert result.universal_rules_shadow is None


def test_universal_rules_shadow_runs_without_replacing_legacy_profile():
    service = QualityProfilerService(
        client=MagicMock(),
        enable_universal_rules_shadow=True,
    )

    result = service.profile_rows(
        organization_id="org_test",
        rows=_rows(),
        config=_config(),
        source_name="UNIT_TEST",
    )

    shadow = result.universal_rules_shadow

    assert result.universal_rules_shadow_enabled is True
    assert shadow is not None
    assert shadow["mode"] == "SHADOW"
    assert shadow["authoritative"] is False
    assert shadow["profile_run_id"] == result.profile_run_id
    assert shadow["total_records"] == 2
    assert shadow["resolved_rule_count"] > 0
    assert shadow["executed_rule_count"] > 0

    rule_ids = {
        item["rule_id"]
        for item in shadow["findings"]
    }

    assert "EMAIL_FORMAT_VALIDITY" in rule_ids
    assert "UNIVERSAL_WHITESPACE_STANDARDIZATION" in rule_ids

    # Phase 1 contract: universal rules are diagnostic/shadow only.
    # Existing QualityProfileResult scoring remains authoritative.
    assert result.scored_record_count == 2
    assert isinstance(result.avg_record_score, float)
    assert isinstance(result.rule_executions, list)
