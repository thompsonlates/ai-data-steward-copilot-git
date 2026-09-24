from pprint import pprint

from app.services.policy_intelligence import PolicyIntelligenceEngine


engine = PolicyIntelligenceEngine()

result = engine.evaluate_profile_against_policy(
    organization_id="org_00003",
    profile_run_id="dqp_6f8d776196734516bd9b",
    domain="PRODUCT",
)

pprint(result)