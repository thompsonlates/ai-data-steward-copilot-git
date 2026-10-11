from app.services.profile_rule_exception_service import (
    ProfileRuleExceptionResolver,
)
from app.services.profile_rule_registry import (
    ProfileRuleDefinition,
    ProfileRuleRegistry,
    RuleEvaluator,
    RuleFamily,
    RuleScope,
    RuleStatus,
)
from app.services.universal_profile_rules_engine import (
    UniversalProfileRulesEngine,
)

from datetime import datetime, timezone


RULE_ID = "PROFILE_F2C2E8F22A824492A0A0"
RULE_VERSION = "1"
PROFILE_ID = "prof_test_exception_engine"


rule = ProfileRuleDefinition(
    rule_id=RULE_ID,
    version=RULE_VERSION,
    logical_key=(
        "PROFILE:prof_test_exception_engine:"
        "PRODUCT_VARIANT:INVALID_PATTERN:WHITESPACE"
    ),
    scope=RuleScope.PROFILE,
    organization_id="org_00003",
    family=RuleFamily.STANDARDIZATION,
    evaluator=RuleEvaluator.WHITESPACE,
    status=RuleStatus.ACTIVE,
    severity="LOW",
    activated_by="matt.demetre@gmail.com",
    approved_by="matt.demetre@gmail.com",
    activated_at=datetime.now(timezone.utc),
    approved_at=datetime.now(timezone.utc),
    profile_id=PROFILE_ID,
    field_names=("product_variant",),
    parameters={},
    deterministic=True,
    remediation_available=False,
    remediation_type=None,
)


registry = ProfileRuleRegistry(
    rules=[rule],
)


exception_resolver = ProfileRuleExceptionResolver(
    [
        {
            "exception_id": "pre_engine_test_001",
            "rule_id": RULE_ID,
            "rule_version": 1,
            "field_name": "product_variant",
            "match_type": "EXACT_VALUE",
            "match_value_json": '"3/4  IN"',
            "reason": "Approved business formatting exception.",
            "status": "ACTIVE",
        }
    ]
)


engine = UniversalProfileRulesEngine(
    rule_registry=registry,
    exception_resolver=exception_resolver,
)


rows = [
    {
        "product_id": "TEST-001",
        "product_variant": "3/4  IN",
    },
    {
        "product_id": "TEST-002",
        "product_variant": "5/8  IN",
    },
]


print("\n=== ENGINE EVALUATION ===")

result = engine.evaluate_profile(
    organization_id="org_00003",
    profile_id=PROFILE_ID,
    domain="PRODUCT",
    profile_run_id="dqp_exception_engine_test",
    rows=rows,
    source_name="IN_MEMORY_EXCEPTION_TEST",
    business_key_field="product_id",
    mapped_fields=[
        "product_id",
        "product_variant",
    ],
)


target_execution = None

for execution in result.rule_executions:
    if execution.rule_id == RULE_ID:
        target_execution = execution
        break


if target_execution is None:
    raise RuntimeError(
        "FAIL: governed PROFILE rule was not executed."
    )


print("\n=== RULE ACCOUNTING ===")
print(
    "evaluated:",
    target_execution.evaluated_record_count,
)
print(
    "passed:",
    target_execution.passed_record_count,
)
print(
    "failed:",
    target_execution.failed_record_count,
)
print(
    "compliance_rate:",
    target_execution.compliance_rate,
)


assert target_execution.evaluated_record_count == 2
assert target_execution.passed_record_count == 0
assert target_execution.failed_record_count == 2

print(
    "PASS: both rows remain genuine DQ rule failures"
)


target_findings = [
    finding
    for finding in result.findings
    if finding.rule_id == RULE_ID
]


print("\n=== ACTIONABLE FINDINGS ===")
print("finding_count:", len(target_findings))

for finding in target_findings:
    print(
        finding.record_id,
        repr(finding.observed_value),
        "->",
        repr(finding.proposed_value),
    )


assert len(target_findings) == 1

remaining = target_findings[0]

assert remaining.record_id == "TEST-002"
assert remaining.observed_value == "5/8  IN"
assert remaining.proposed_value == "5/8 IN"

print(
    "PASS: non-excepted failed value remains actionable"
)


excepted_findings = [
    finding
    for finding in target_findings
    if finding.record_id == "TEST-001"
]

assert len(excepted_findings) == 0

print(
    "PASS: excepted failed value produced no actionable finding"
)


print("\n=== CRITICAL DISTINCTION ===")

assert target_execution.failed_record_count == 2
assert len(target_findings) == 1

print(
    "DQ failures: 2"
)
print(
    "Actionable governed findings: 1"
)
print(
    "Suppressed by steward exception: 1"
)


print(
    "\nGREEN: failed DQ evaluation remains failed while "
    "ACTIVE steward exception suppresses only the "
    "actionable governed finding."
)
