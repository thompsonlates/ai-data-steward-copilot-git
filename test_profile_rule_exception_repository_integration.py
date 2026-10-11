from datetime import datetime, timezone

from app.repositories.quality_profiler_repository import (
    QualityProfilerRepository,
)
from app.services.profile_rule_registry import (
    ProfileRuleDefinition,
    ProfileRuleRegistry,
    RuleEvaluator,
    RuleFamily,
    RuleScope,
    RuleStatus,
)
from app.services.quality_profiler_service import (
    QualityFieldConfig,
    QualityProfileConfig,
    QualityProfilerService,
)
from app.services.universal_profile_rules_engine import (
    UniversalProfileRulesEngine,
)


ORG = "org_00003"
PROFILE_ID = "prof_ceaca4a87b5142b98fc9"
RULE_ID = "PROFILE_F2C2E8F22A824492A0A0"
RULE_VERSION = 1
FIELD = "product_variant"

EXCEPTED_VALUE = "3/4  IN"
CONTROL_VALUE = "5/8  IN"
ACTOR = "matt.demetre@gmail.com"


repository = QualityProfilerRepository()


print("\n=== CREATE TEMP ACTIVE EXCEPTION ===")

exception = repository.create_active_profile_rule_exception(
    organization_id=ORG,
    profile_id=PROFILE_ID,
    rule_id=RULE_ID,
    rule_version=RULE_VERSION,
    field_name=FIELD,
    match_type="EXACT_VALUE",
    match_value=EXCEPTED_VALUE,
    reason="Repository integration test exception.",
    approved_by=ACTOR,
)

exception_id = exception["exception_id"]

print("exception_id:", exception_id)
print("status:", exception["status"])
print("match_value_json:", exception["match_value_json"])


try:
    print("\n=== BUILD GOVERNED RULE ===")

    rule = ProfileRuleDefinition(
        rule_id=RULE_ID,
        version=str(RULE_VERSION),
        logical_key=(
            f"PROFILE:{PROFILE_ID}:"
            "PRODUCT_VARIANT:INVALID_PATTERN:WHITESPACE"
        ),
        scope=RuleScope.PROFILE,
        organization_id=ORG,
        profile_id=PROFILE_ID,
        family=RuleFamily.STANDARDIZATION,
        evaluator=RuleEvaluator.WHITESPACE,
        status=RuleStatus.ACTIVE,
        severity="LOW",
        field_names=(FIELD,),
        parameters={},
        deterministic=True,
        remediation_available=False,
        remediation_type=None,
        approved_by=ACTOR,
        approved_at=datetime.now(timezone.utc),
        activated_by=ACTOR,
        activated_at=datetime.now(timezone.utc),
    )

    registry = ProfileRuleRegistry(
        rules=[rule],
    )

    shared_engine = UniversalProfileRulesEngine(
        rule_registry=registry,
    )

    profiler = QualityProfilerService(
        client=repository.client,
        quality_repository=repository,
        universal_rules_engine=shared_engine,
        enable_universal_rules_shadow=True,
    )


    print("\n=== BUILD PROFILE CONFIG ===")

    config = QualityProfileConfig(
        profile_id=PROFILE_ID,
        domain="PRODUCT",
        source_table="IN_MEMORY_REPOSITORY_TEST",
        business_key_field="product_id",
        fields=[
            QualityFieldConfig(
                field_name=FIELD,
        ),
    ],
)


    rows = [
        {
            "product_id": "TEST-001",
            FIELD: EXCEPTED_VALUE,
        },
        {
            "product_id": "TEST-002",
            FIELD: CONTROL_VALUE,
        },
    ]


    print("\n=== REPOSITORY-LOADED EVALUATION ===")

    result = profiler._evaluate_universal_rules_shadow(
        organization_id=ORG,
        profile_run_id="dqp_repository_exception_test",
        domain="PRODUCT",
        source_name="IN_MEMORY_REPOSITORY_TEST",
        business_key_field="product_id",
        rows=rows,
        config=config,
    )


    execution = next(
        (
            item
            for item in result.rule_executions
            if item.rule_id == RULE_ID
        ),
        None,
    )

    if execution is None:
        raise RuntimeError(
            "FAIL: governed PROFILE rule was not executed."
        )


    print("\n=== RULE ACCOUNTING ===")
    print("evaluated:", execution.evaluated_record_count)
    print("passed:", execution.passed_record_count)
    print("failed:", execution.failed_record_count)

    assert execution.evaluated_record_count == 2
    assert execution.passed_record_count == 0
    assert execution.failed_record_count == 2


    findings = [
        finding
        for finding in result.findings
        if finding.rule_id == RULE_ID
    ]


    print("\n=== ACTIONABLE FINDINGS ===")
    print("finding_count:", len(findings))

    for finding in findings:
        print(
            finding.record_id,
            repr(finding.observed_value),
            "->",
            repr(finding.proposed_value),
        )


    assert len(findings) == 1

    remaining = findings[0]

    assert remaining.record_id == "TEST-002"
    assert remaining.observed_value == CONTROL_VALUE
    assert remaining.proposed_value == "5/8 IN"

    assert not any(
        finding.record_id == "TEST-001"
        for finding in findings
    )


    print("\n=== CRITICAL INTEGRATION PROOF ===")
    print("DQ failures: 2")
    print("Actionable governed findings: 1")
    print("Repository-loaded exception suppressions: 1")

    print(
        "\nGREEN: persisted ACTIVE exception was loaded "
        "through the repository, converted into the "
        "evaluation-scoped resolver, and suppressed only "
        "the matching actionable finding."
    )

finally:
    print("\n=== CLEANUP ===")

    deactivated = (
        repository.deactivate_profile_rule_exception(
            organization_id=ORG,
            profile_id=PROFILE_ID,
            exception_id=exception_id,
            deactivated_by=ACTOR,
            reason=(
                "Completed repository integration test."
            ),
        )
    )

    print("status:", deactivated["status"])
    print("exception_id:", deactivated["exception_id"])
