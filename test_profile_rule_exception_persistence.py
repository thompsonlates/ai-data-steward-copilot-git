from app.repositories.quality_profiler_repository import QualityProfilerRepository

ORG_ID = "org_00003"
PROFILE_ID = "prof_ceaca4a87b5142b98fc9"
RULE_ID = "PROFILE_F2C2E8F22A824492A0A0"
RULE_VERSION = 1
FIELD_NAME = "product_variant"

repo = QualityProfilerRepository()

print("\n=== CREATE ===")
created = repo.create_active_profile_rule_exception(
    organization_id=ORG_ID,
    profile_id=PROFILE_ID,
    rule_id=RULE_ID,
    rule_version=RULE_VERSION,
    field_name=FIELD_NAME,
    match_type="EXACT_VALUE",
    match_value="3/4  IN",
    reason="Approved business formatting exception.",
    approved_by="matt.demetre@gmail.com",
)

print("exception_id:", created["exception_id"])
print("status:", created["status"])
print("match_type:", created["match_type"])
print("match_value_json:", created["match_value_json"])
print("rule_id:", created["rule_id"])
print("rule_version:", created["rule_version"])

exception_id = created["exception_id"]

print("\n=== LIST ACTIVE ===")
active = repo.list_active_profile_rule_exceptions(
    organization_id=ORG_ID,
    profile_id=PROFILE_ID,
    rule_id=RULE_ID,
    rule_version=RULE_VERSION,
)

print("active_count:", len(active))

for item in active:
    print(
        item["exception_id"],
        item["status"],
        item["match_value_json"],
    )

print("\n=== TENANT ISOLATION ===")
try:
    repo.get_profile_rule_exception(
        organization_id="org_NOT_THE_OWNER",
        profile_id=PROFILE_ID,
        exception_id=exception_id,
    )
except Exception as exc:
    print("tenant isolation:", type(exc).__name__, str(exc))
else:
    raise RuntimeError(
        "FAIL: cross-tenant exception lookup did not fail closed."
    )

print("\n=== DEACTIVATE ===")
deactivated = repo.deactivate_profile_rule_exception(
    organization_id=ORG_ID,
    profile_id=PROFILE_ID,
    exception_id=exception_id,
    deactivated_by="matt.demetre@gmail.com",
    reason="Repository smoke test completed.",
)

print("status:", deactivated["status"])
print("effective_to:", deactivated["effective_to"])
print("deactivated_by:", deactivated["deactivated_by"])

print("\n=== VERIFY ACTIVE LIST ===")
remaining = repo.list_active_profile_rule_exceptions(
    organization_id=ORG_ID,
    profile_id=PROFILE_ID,
    rule_id=RULE_ID,
    rule_version=RULE_VERSION,
)

print("active_count_after_deactivation:", len(remaining))

if any(
    row["exception_id"] == exception_id
    for row in remaining
):
    raise RuntimeError(
        "FAIL: deactivated exception remained ACTIVE."
    )

print("\nGREEN: governed profile rule exception persistence passed.")
