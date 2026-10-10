from datetime import datetime, timezone

from app.services.profile_rule_registry import (
    ProfileRuleDefinition,
    ProfileRuleRegistry,
    RuleEvaluator,
    RuleFamily,
    RuleResolutionContext,
    RuleScope,
    RuleStatus,
)

now = datetime.now(timezone.utc)

rule = ProfileRuleDefinition(
    rule_id="TEST_PROFILE_UOM",
    version="1.0",
    logical_key="PRODUCT_VARIANT_UOM_POLICY",
    scope=RuleScope.PROFILE,
    family=RuleFamily.VALIDITY,
    evaluator=RuleEvaluator.ALLOWED_VALUES,
    status=RuleStatus.ACTIVE,
    severity="HIGH",
    description="Halperns Product UOM policy",
    organization_id="org_halperns",
    profile_id="prof_halperns_product",
    domains=("PRODUCT",),
    field_names=("product_variant",),
    parameters={
        "allowed_values": ["EA", "CS", "LB"],
    },
    approved_by="test_steward",
    approved_at=now,
    activated_by="test_steward",
    activated_at=now,
)

registry = ProfileRuleRegistry(
    rules=[rule],
    include_adms_defaults=False,
)


def resolves(org: str, profile: str | None) -> bool:
    resolved = registry.resolve_active_rules(
        context=RuleResolutionContext(
            organization_id=org,
            domain="PRODUCT",
            profile_id=profile,
            mapped_fields=("product_variant",),
            semantic_types_by_field={},
        )
    )
    return any(r.rule_id == "TEST_PROFILE_UOM" for r in resolved)


assert resolves("org_halperns", "prof_halperns_product") is True
assert resolves("org_halperns", "prof_halperns_supplier") is False
assert resolves("org_other", "prof_halperns_product") is False
assert resolves("org_halperns", None) is False

print("PASS: PROFILE rule isolation is fail-closed.")