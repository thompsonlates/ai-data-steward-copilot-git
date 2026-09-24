from app.services.policy_intelligence import PolicyIntelligenceEngine


engine = PolicyIntelligenceEngine()

result = engine.upsert_policy_dq_rule_mapping(
    organization_id="org_00003",
    policy_id="POL-PRODUCT-V1",
    policy_version="v1",
    domain="PRODUCT",
    dq_rule_id="PRODUCT_VARIANT_VALIDITY",
    compliance_operator=">=",
    compliance_threshold=0.95,
    severity="HIGH",
    required_flag=True,
    active_flag=True,
    updated_by="matt",
)

print(result)