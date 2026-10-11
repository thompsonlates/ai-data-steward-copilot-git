from app.services.profile_rule_exception_service import (
    ProfileRuleExceptionResolver,
)

BASE = {
    "exception_id": "pre_test_exact_value_001",
    "rule_id": "PROFILE_F2C2E8F22A824492A0A0",
    "rule_version": 1,
    "field_name": "product_variant",
    "match_type": "EXACT_VALUE",
    "match_value_json": '"3/4  IN"',
    "reason": "Approved business formatting exception.",
    "status": "ACTIVE",
}


print("\n=== EXACT MATCH ===")
resolver = ProfileRuleExceptionResolver([BASE])

match = resolver.resolve_exact_value(
    rule_id="PROFILE_F2C2E8F22A824492A0A0",
    rule_version=1,
    field_name="product_variant",
    observed_value="3/4  IN",
)

assert match is not None
assert match.exception_id == "pre_test_exact_value_001"
assert match.match_value == "3/4  IN"

print("PASS:", match.exception_id)


print("\n=== DIFFERENT VALUE MUST NOT MATCH ===")
match = resolver.resolve_exact_value(
    rule_id="PROFILE_F2C2E8F22A824492A0A0",
    rule_version=1,
    field_name="product_variant",
    observed_value="3/4 IN",
)

assert match is None
print("PASS: normalized-looking value did not match")


print("\n=== WRONG RULE MUST NOT MATCH ===")
match = resolver.resolve_exact_value(
    rule_id="PROFILE_OTHER",
    rule_version=1,
    field_name="product_variant",
    observed_value="3/4  IN",
)

assert match is None
print("PASS: wrong rule did not match")


print("\n=== WRONG VERSION MUST NOT MATCH ===")
match = resolver.resolve_exact_value(
    rule_id="PROFILE_F2C2E8F22A824492A0A0",
    rule_version=2,
    field_name="product_variant",
    observed_value="3/4  IN",
)

assert match is None
print("PASS: wrong version did not match")


print("\n=== WRONG FIELD MUST NOT MATCH ===")
match = resolver.resolve_exact_value(
    rule_id="PROFILE_F2C2E8F22A824492A0A0",
    rule_version=1,
    field_name="product_name",
    observed_value="3/4  IN",
)

assert match is None
print("PASS: wrong field did not match")


print("\n=== INACTIVE EXCEPTION MUST BE IGNORED ===")
inactive = dict(BASE)
inactive["status"] = "INACTIVE"

inactive_resolver = ProfileRuleExceptionResolver([inactive])

match = inactive_resolver.resolve_exact_value(
    rule_id="PROFILE_F2C2E8F22A824492A0A0",
    rule_version=1,
    field_name="product_variant",
    observed_value="3/4  IN",
)

assert match is None
print("PASS: inactive exception ignored")


print("\n=== UNSUPPORTED ACTIVE TYPE MUST FAIL CLOSED ===")
unsupported = dict(BASE)
unsupported["exception_id"] = "pre_test_unsupported_001"
unsupported["match_type"] = "REGEX"

try:
    ProfileRuleExceptionResolver([unsupported])
except ValueError as exc:
    print("PASS:", str(exc))
else:
    raise RuntimeError(
        "FAIL: unsupported ACTIVE exception did not fail closed."
    )


print("\n=== MALFORMED JSON MUST FAIL CLOSED ===")
malformed = dict(BASE)
malformed["exception_id"] = "pre_test_malformed_001"
malformed["match_value_json"] = '"3/4  IN'

try:
    ProfileRuleExceptionResolver([malformed])
except ValueError as exc:
    print("PASS:", str(exc))
else:
    raise RuntimeError(
        "FAIL: malformed ACTIVE exception did not fail closed."
    )


print("\n=== DUPLICATE ACTIVE CONTRACT MUST FAIL CLOSED ===")
duplicate = dict(BASE)
duplicate["exception_id"] = "pre_test_exact_value_002"

try:
    ProfileRuleExceptionResolver([BASE, duplicate])
except ValueError as exc:
    print("PASS:", str(exc))
else:
    raise RuntimeError(
        "FAIL: duplicate ACTIVE exception contract "
        "did not fail closed."
    )


print(
    "\nGREEN: governed profile rule exception resolver passed."
)
