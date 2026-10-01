from __future__ import annotations

import json
from typing import Any, Dict

from app.api.schemas import MatchExplainRequest


def _require_organization_id(
    organization_id: str,
) -> str:
    normalized = str(organization_id or "").strip()

    if not normalized:
        raise ValueError(
            "organization_id is required for tenant-isolated prompt building."
        )

    if not normalized.startswith("org_"):
        raise ValueError(
            "organization_id must use the org_ identifier standard."
        )

    return normalized


def _assert_context_organization(
    *,
    organization_id: str,
    context: Dict[str, Any] | None,
    context_name: str,
) -> None:
    if not context:
        return

    context_organization_id = str(
        context.get("organization_id") or ""
    ).strip()

    if (
        context_organization_id
        and context_organization_id != organization_id
    ):
        raise ValueError(
            f"{context_name} does not belong to the authenticated organization."
        )


def _assert_signal_packet_organizations(
    *,
    organization_id: str,
    signal_packets: list[Any] | None,
) -> None:
    if not signal_packets:
        return

    for packet in signal_packets:
        if not isinstance(packet, dict):
            continue

        packet_organization_id = str(
            packet.get("organization_id") or ""
        ).strip()

        if (
            packet_organization_id
            and packet_organization_id != organization_id
        ):
            raise ValueError(
                "Signal evidence contains data from a different organization."
            )


def _strip_tenant_metadata(value: Any) -> Any:
    """Remove internal tenant identifiers before sending context to an LLM."""
    if isinstance(value, dict):
        return {
            key: _strip_tenant_metadata(item)
            for key, item in value.items()
            if key not in {
                "organization_id",
                "customer_id",
                "user_id",
            }
        }

    if isinstance(value, list):
        return [
            _strip_tenant_metadata(item)
            for item in value
        ]

    return value


def _fmt(v: Any) -> str:
    return str(v).strip() if v not in (None, "") else "N/A"


def _get_value(record: Any, key: str, default: str = "") -> str:
    if record is None:
        return default

    if isinstance(record, dict):
        value = record.get(key, default)
    else:
        value = getattr(record, key, default)

    if value is None:
        return default

    return str(value).strip()


def _resolve_entity_id(record: Any, domain: str) -> str:
    normalized_domain = (domain or "CUSTOMER").upper()

    fields_by_domain = {
        "CUSTOMER": ["member_id"],
        "PATIENT": ["patient_id", "member_id"],
        "PROVIDER": ["provider_id", "npi", "member_id"],
        "SUPPLIER": ["supplier_id", "vendor_id", "tax_id", "member_id"],
        "PRODUCT": ["product_id", "gtin", "sku", "upc", "member_id"],
        "BANKING": [
            "account_id",
            "banking_customer_id",
            "sap_business_partner_id",
        ],
        "LOCATION": [
            "location_id",
            "site_id",
            "location_code",
            "member_id",
        ],
        "ORGANIZATION": [
            "organization_entity_id",
            "organization_code",
            "member_id",
        ],
    }

    for field in fields_by_domain.get(normalized_domain, ["member_id"]):
        value = _get_value(record, field)
        if value:
            return value

    return ""


def build_governance_recommendation_prompt(
    governance_context: Dict[str, Any],
    *,
    organization_id: str,
) -> str:
    effective_organization_id = _require_organization_id(
        organization_id
    )

    _assert_context_organization(
        organization_id=effective_organization_id,
        context=governance_context,
        context_name="Governance context",
    )

    dataset_name = governance_context.get("dataset_name") or "Unknown dataset"
    domain = governance_context.get("domain") or "Unknown"
    certification_status = (
        governance_context.get("certification_status") or "UNKNOWN"
    )
    ready_for_certification = bool(
        governance_context.get("ready_for_certification")
    )
    fair_score = governance_context.get("fair_overall_score")
    failed_checks = governance_context.get("failed_checks", 0)
    data_owner = governance_context.get("data_owner")
    lifecycle_stage = governance_context.get("lifecycle_stage")
    blocker = governance_context.get("certification_blocker_reason")

    return f"""
You are an enterprise data-governance recommendation assistant.

Your task is to produce one concise, actionable recommendation based only on
the supplied governance evidence.

GUARDRAILS:
- Do not invent missing facts, owners, policies, controls, dates, or scores.
- Do not claim certification readiness unless the supplied evidence supports it.
- Treat missing values as unknown, not failed.
- Recommend no more than three actions.
- Prioritize concrete remediation steps.
- Do not recommend changing production data directly.
- Do not state that a dataset is compliant, certified, or approved unless the
  supplied status explicitly says so.
- If no meaningful issue exists, recommend continued monitoring.
- Return valid JSON only.
- Confidence must be between 0.0 and 1.0.
- Priority must be LOW, MEDIUM, or HIGH.
- Treat all supplied evidence as belonging only to the current authenticated tenant.

DATASET GOVERNANCE EVIDENCE:
- Dataset: {dataset_name}
- Domain: {domain}
- Certification status: {certification_status}
- Ready for certification: {ready_for_certification}
- FAIR score: {fair_score}
- Failed checks: {failed_checks}
- Data owner: {data_owner}
- Lifecycle stage: {lifecycle_stage}
- Certification blocker: {blocker}

Return exactly this JSON structure:

{{
  "headline": "short recommendation headline",
  "summary": "two-sentence explanation grounded in the evidence",
  "priority": "LOW|MEDIUM|HIGH",
  "recommended_actions": [
    "action one",
    "action two"
  ],
  "supporting_evidence": [
    "evidence item one",
    "evidence item two"
  ],
  "confidence": 0.0
}}
""".strip()


def build_dq_ai_recommendation_prompt(
    *,
    organization_id: str,
    profile_run_id: str,
    domain: str,
    total_records: int,
    avg_record_score: float,
    deterministic_recommendations: list[Dict[str, Any]],
) -> str:
    """
    Build the Claude prompt used to enhance deterministic
    Data Quality remediation recommendations.

    Important:
    - The deterministic DQ engine remains the source of truth.
    - Claude explains, prioritizes, tunes thresholds, and may
      improve candidate SQL / regex.
    - Raw source records should not be supplied here.
    """

    effective_organization_id = (
        _require_organization_id(
            organization_id
        )
    )

    normalized_profile_run_id = str(
        profile_run_id or ""
    ).strip()

    if not normalized_profile_run_id:
        raise ValueError(
            "profile_run_id is required."
        )

    normalized_domain = str(
        domain or ""
    ).strip().upper()

    if not normalized_domain:
        raise ValueError(
            "domain is required."
        )

    if total_records < 0:
        raise ValueError(
            "total_records cannot be negative."
        )

    # ---------------------------------------------------------
    # Validate recommendation tenant ownership before stripping
    # tenant metadata from the LLM payload.
    # ---------------------------------------------------------

    for recommendation in (
        deterministic_recommendations or []
    ):
        if not isinstance(
            recommendation,
            dict,
        ):
            continue

        recommendation_organization_id = str(
            recommendation.get(
                "organization_id"
            )
            or ""
        ).strip()

        if (
            recommendation_organization_id
            and
            recommendation_organization_id
            != effective_organization_id
        ):
            raise ValueError(
                "DQ recommendation evidence "
                "contains data from a different "
                "organization."
            )

        recommendation_profile_run_id = str(
            recommendation.get(
                "profile_run_id"
            )
            or ""
        ).strip()

        if (
            recommendation_profile_run_id
            and
            recommendation_profile_run_id
            != normalized_profile_run_id
        ):
            raise ValueError(
                "DQ recommendation evidence "
                "contains data from a different "
                "profile run."
            )

    # ---------------------------------------------------------
    # Do not send internal tenant identifiers to Claude.
    # Keep only aggregate DQ evidence and candidate fixes.
    # ---------------------------------------------------------

    safe_recommendations = (
    _strip_tenant_metadata(
        deterministic_recommendations
        or []
    )
)

# ---------------------------------------------------------
# Row-level finding evidence is for steward/UI visibility,
# not LLM analysis.
#
# Keep deterministic counts/rules/candidate remediation
# available to Claude while preventing observed/proposed
# source values and record identifiers from entering the
# LLM prompt.
# ---------------------------------------------------------

    safe_recommendations = [
    {
        key: value
        for key, value in recommendation.items()
        if key != "evidence_samples"
    }
    for recommendation in safe_recommendations
    if isinstance(recommendation, dict)
]
    # Keep the LLM request bounded. The repository/service
    # should already aggregate findings by rule.
    safe_recommendations = (
        safe_recommendations[:20]
    )

    recommendation_count = len(
        safe_recommendations
    )

    dq_payload = {
        "profile_run_id":
            normalized_profile_run_id,
        "domain":
            normalized_domain,
        "total_records":
            int(total_records),
        "avg_record_score":
            round(
                float(
                    avg_record_score
                    or 0.0
                ),
                2,
            ),
        "deterministic_recommendations":
            safe_recommendations,
    }

    return f"""
You are the Data Quality Remediation Advisor inside
AI Data Steward Copilot.

Your audience is a data steward, data-quality lead,
data governance lead, MDM architect, or data engineer.

You are given aggregated deterministic Data Quality
findings and candidate remediation logic generated by
AI Data Steward Copilot.

The deterministic DQ engine is the source of truth.

Your job is to ENHANCE the supplied recommendations,
not replace or contradict proven deterministic evidence.

CORE RESPONSIBILITIES:

1. Explain the business and downstream data impact.

2. Validate the deterministic remediation recommendation.

3. Review the proposed remediation logic. Only improve
   the artifact type selected by the recommendation when the
   supplied evidence supports it. Do not generate an alternate
   SQL artifact for a REGEX remediation, or an alternate REGEX
   artifact for a SQL remediation. Keep SQL short, diagnostic,
   and non-destructive when SQL is the selected implementation.

4. Recommend a practical passing threshold.

5. Recommend whether remediation can be automated.

6. Clearly identify when steward review is required.

7. Explain why the proposed remediation is appropriate.

8. Preserve the original rule_id, field_name,
   dimension, and severity.

9. Return concise remediation recommendations that
   a data steward or engineer can understand and
   implement safely.

STRICT DATA SAFETY RULES:

- Keep the entire JSON response concise.

- suggested_sql must be null unless implementation_type is SQL.

- When implementation_type is SQL, suggested_sql may contain a
  short diagnostic SELECT statement no longer than 500 characters.

- Do not generate multi-step SQL scripts, long CASE
  expressions, CTE chains, DDL, DML, or procedural SQL.

- Prefer recommended_remediation and reasoning_summary
  over long SQL examples.

- If remediation requires authoritative-source lookup,
  business interpretation, or steward judgment,
  suggested_sql must be null.

- suggested_regex must be concise and only returned
  when implementation_type is REGEX and regex is clearly appropriate.

- When implementation_type is REGEX, suggested_sql must be null.

- When implementation_type is SQL, suggested_regex must be null.
  Regex predicates needed by SQL belong inside suggested_sql rather
  than in a separate suggested_regex artifact.

- Return only the implementation artifact relevant to the selected
  implementation_type. Do not provide SQL and REGEX alternatives for
  the same recommendation.

- Keep business_impact, recommended_remediation,
  and reasoning_summary each concise.


- Prefer implementation_guidance over executable SQL
  when remediation requires business context,
  authoritative-source lookup, or steward review.

- Keep each recommendation concise enough that the
  entire JSON response remains well below the model
  output limit.

- Do not invent source values, record counts, rules,
  identifiers, systems, policies, or business context.

- Do not infer facts that are not present in the
  supplied DQ evidence.

- Never generate DELETE, DROP, TRUNCATE, MERGE,
  or destructive UPDATE statements.

- Never recommend modifying production data without
  steward approval.

- SQL should default to diagnostic SELECT statements
  or non-destructive transformation examples.

- If a safe automatic correction cannot be determined,
  use STEWARD_REVIEW_REQUIRED.

- Duplicate or identity-related issues should normally
  require steward review unless the supplied evidence
  proves the remediation is deterministic.

- Missing required values must not be fabricated.

- Completeness remediation may recommend enrichment
  only when an authoritative source is available.

- Standardization fixes may be AUTO_FIX_CANDIDATE when
  they are deterministic and reversible.

- Regex recommendations must be valid regex patterns.

- Suggested thresholds must be numbers from 0.0 to 100.0.

- Automation confidence must be between 0.0 and 1.0.

- Treat all supplied evidence as belonging exclusively
  to the currently authenticated tenant.

- Never expose or mention internal organization_id,
  customer_id, or user_id values.

PRODUCT VARIANT / UOM INTERPRETATION RULES:

- Apply these rules only when DOMAIN is PRODUCT and the supplied
  deterministic recommendation concerns product_variant or UOM.

- "3 OZ" is a canonical Product Variant example.

- "3   OZ" is noncanonical because repeated separator whitespace
  violates the one-space Product Variant format.

- Distinguish FORMAT VALIDITY from STANDARDIZATION:
  * STANDARDIZATION means the semantic quantity/UOM can be preserved
    while safely normalizing presentation such as trim, case, or
    repeated whitespace.
  * FORMAT VALIDITY means the value fails the governed structural
    quantity + UOM pattern and may require validation or steward review.

- Distinguish UOM POLICY from FORMAT:
  a value can be structurally valid while its UOM token is not in the
  currently approved governed vocabulary.

- Never claim an unapproved UOM is factually wrong unless the supplied
  evidence proves that. It may require tenant-specific policy approval.

- Never substitute one UOM for another or convert units unless the
  supplied evidence explicitly contains the authoritative business rule
  and conversion basis.

- Never change quantity merely to satisfy Product Variant validation.

- Safe deterministic Product Variant standardization may recommend
  trimming outer whitespace, collapsing repeated separator whitespace
  to one space, and uppercasing the UOM token when business meaning is
  unchanged.

- PRODUCT_VARIANT_UOM_ALLOWED_VALUE should normally require steward or
  configuration review rather than automatic source-data mutation.

DOMAIN:

{normalized_domain}

DOMAIN GUIDANCE:

{_dq_domain_guidance(normalized_domain)}

AGGREGATED DQ EVIDENCE AND DETERMINISTIC CANDIDATE FIXES:

{json.dumps(dq_payload, indent=2)}

RETURN ONLY VALID JSON USING EXACTLY THIS STRUCTURE:

{{
  "profile_run_id": "{normalized_profile_run_id}",
  "domain": "{normalized_domain}",
  "overall_analysis": {{
    "dq_health_summary":
      "concise explanation of overall DQ health",
    "highest_priority_issue":
      "rule id or short issue description",
    "recommended_next_action":
      "concise next action",
    "confidence": 0.0
  }},
  "recommendations": [
    {{
      "recommendation_id":
        "existing deterministic recommendation id",
      "rule_id":
        "existing rule id",
      "field_name":
        "existing field name or null",
      "dimension":
        "COMPLETENESS|VALIDITY|UNIQUENESS|STANDARDIZATION|CONSISTENCY",
      "severity":
        "CRITICAL|HIGH|MEDIUM|LOW",

      "recommendation_title":
        "short actionable title",

      "business_impact":
        "why this issue matters",

      "recommended_remediation":
        "specific concise steward-friendly remediation",

      "implementation_type":
        "SQL|REGEX|RULE_CONFIG|ENRICHMENT|MANUAL_REVIEW",

      "suggested_sql":
        "short diagnostic SELECT <= 500 characters or null",

      "suggested_regex":
        "regex pattern or null",

      "suggested_threshold": 0.0,

      "automation_recommendation":
        "AUTO_FIX_CANDIDATE|ENRICH_IF_AUTHORITATIVE_SOURCE_AVAILABLE|STEWARD_REVIEW_REQUIRED|REVIEW_REQUIRED",

      "automation_confidence": 0.0,

      "steward_approval_required": true,

      "reasoning_summary":
        "brief explanation grounded only in supplied evidence"
    }}
  ]
}}

CRITICAL RECOMMENDATION COVERAGE RULE:

    - The input contains exactly {recommendation_count}
    deterministic recommendation objects.
    - You MUST return exactly {recommendation_count}
    objects in the "recommendations" array.
    - Never return an empty recommendations array.
    - Never omit a supplied recommendation.
    - Never combine multiple deterministic recommendations
    into one recommendation.
    - Never create additional recommendations.
    - Copy every supplied recommendation_id exactly into
    its corresponding output object.
    - Every recommendation_id supplied in the input must
    appear exactly once in the output.
    


OUTPUT RULES:

- Return raw JSON only.
- Do not return markdown.
- Do not return code fences.
- Do not return commentary before or after JSON.
- Preserve recommendation_id when one is supplied.
- Preserve rule_id exactly.
- Preserve field_name exactly.
- Preserve dimension exactly.
- Preserve severity exactly.
- For deterministic formatting and canonicalization rules, prefer REGEX
  when the transformation can be expressed without changing business meaning.
- Use SQL as an implementation example, not as the canonical rule type,
  when the same remediation may apply to Google Sheets or other non-SQL sources.
- PRODUCT_VARIANT_STANDARDIZATION must use implementation_type REGEX.
- PRODUCT_VARIANT_STANDARDIZATION must set suggested_sql to null.
- PRODUCT_VARIANT_STANDARDIZATION must set steward_approval_required to true.
- FULL_NAME_STANDARDIZATION is a deterministic presentation-only
  standardization rule when the supplied deterministic evidence proves
  that business meaning is unchanged.

- REGEX may be used as a validation or normalization contract when the
  execution target is not SQL or when no complete executable SQL
  remediation artifact is available.

- For SQL-backed sources, FULL_NAME_STANDARDIZATION may be eligible for
  a governed SQL remediation when complete persisted observed-to-proposed
  evidence exists and the correction can be bounded exclusively to the
  affected values.

- Do not invent observed values or proposed values.

- AI analysis does not itself establish SQL write-back eligibility.
  Executable SQL eligibility must be verified server-side against the
  complete persisted finding evidence.
- AUTO_FIX_CANDIDATE means the remediation is eligible for governed
  automation AFTER explicit steward approval; it does not mean approval
  may be bypassed.
- Any remediation artifact capable of modifying source data must require
  steward approval before execution.
- Do not manufacture additional findings.
- suggested_sql must be null whenever implementation_type is not SQL.
- suggested_regex must be null whenever implementation_type is not REGEX.
- Never return both suggested_sql and suggested_regex as populated values
  for the same recommendation.
- SQL must be non-destructive.
- Do not use DELETE, DROP, TRUNCATE, or MERGE.
- Do not return UPDATE statements that modify source data.
- A duplicate-key recommendation must default to
  steward review.
- If evidence is insufficient, state that in the
  reasoning_summary and recommend review.
- suggested_sql must not exceed 500 characters.
- suggested_sql must be null when remediation requires
  authoritative-source lookup or steward judgment.
- Do not return multi-step SQL.
- Do not return SQL containing long CASE expressions.
- Keep each recommendation compact.
""".strip()


def _domain_guidance(domain: str | None) -> str:
    d = (domain or "").upper()

    if d == "PATIENT":
        return (
            "Use healthcare patient identity language. Treat exact patient/member ID, DOB, name, "
            "email, phone number, and address as identity evidence. Be cautious about overlays, "
            "shared addresses, shared phone numbers, and incomplete demographic evidence."
        )

    if d == "PROVIDER":
        return (
            "Use healthcare provider identity language. Treat Provider ID, Name, and NPI as deterministic provider identifiers."
            "Use phone number, provider name, specialty, practice address, provider email,"
            "and source-system trust as supporting evidence."
        )

    if d == "SUPPLIER":
        return (
            "Use supplier/vendor identity language. Treat supplier ID, vendor ID, tax ID, supplier name, "
            "address, contact email, and source-system trust as key evidence."
        )

    if d == "PRODUCT":
        return (
            "Use product/entity resolution language. Treat GTIN as the strongest "
            "deterministic product identifier. Treat SKU, Product ID, Product Name, "
            "Product Variant, Pack Size, Item Category, and Source System as "
            "product evidence. Do not use or mention DOB, email, address, NPI, "
            "patient/member identity, or provider identity for Product decisions."
        )

    if d == "BANKING":
        return (
            "Use banking customer and account identity language. "
            "Treat Account ID, Customer ID, and SAP Business Partner ID as strong "
            "identity evidence when exact and authoritative. "
            "Treat routing number as bank or institution evidence, not as a unique "
            "customer or account identifier. "
            "Treat account last four digits as supporting evidence only because "
            "they are not globally unique. "
            "Use customer name, account type, currency, institution name, "
            "email, address, phone number, and source-system trust as supporting evidence. "
            "A conflicting Account ID, Customer ID, or SAP Business Partner ID must "
            "materially reduce merge confidence. "
            "Never recommend AUTO_MERGE based only on routing number, account last four, "
            "name similarity, or other non-unique banking attributes."
        )

    if d == "LOCATION":
        return (
            "Use physical-site/location identity language. Treat Location ID, Site ID, and "
            "Location Code as strong deterministic location identifiers when exact and authoritative. "
            "Use Location Name, physical address, Parent Location ID, Organization Entity ID, "
            "Location Type, and source-system trust as supporting evidence. A matching physical "
            "address alone does not prove the same Location because multiple departments, suites, "
            "operational locations, or sites may share an address. Conflicting authoritative Location "
            "ID, Site ID, or Location Code values must materially reduce merge confidence. Parent "
            "Location ID represents hierarchy evidence; do not infer a child_location_id. Missing "
            "location identifiers are unavailable evidence, not conflicting evidence."
        )

    if d == "ORGANIZATION":
        return (
            "Parent Organization ID is hierarchy evidence and must not be treated as proof "
            "that two child organizations are the same entity. When one record's "
            "parent_organization_id exactly equals the other record's organization_entity_id, "
            "the records represent distinct parent-child hierarchy nodes, not duplicate "
            "organizations, and merging them would corrupt the organization hierarchy."
            "Use enterprise organization identity language. Treat Organization Entity ID and "
            "Organization Code as strong deterministic organization identifiers when exact and "
            "authoritative. Use Organization Name, Organization Type, Parent Organization ID, "
            "Organization Status, related location evidence, and source-system trust as supporting "
            "evidence. Never confuse the mastered Organization entity identifier with the internal "
            "tenant organization_id. Similar organization names alone do not prove entity identity. "
            "Conflicting authoritative Organization Entity ID or Organization Code values must "
            "materially reduce merge confidence. Parent Organization ID is hierarchy evidence and "
            "must not be treated as proof that two child organizations are the same entity."
        )

    if d == "CUSTOMER":
        return (
            "Use customer/member identity language. Treat member ID, full name, DOB, email, "
            "phone number, address, and source-system trust as identity evidence. "
            "Treat exact normalized phone matches as supporting evidence, not as a primary "
            "deterministic identifier."
        )


    return (
        "Use customer/member/entity resolution language. Treat identifiers, name, DOB/date, email, address, "
        "and source-system trust as key evidence."
    )


def _dq_domain_guidance(
    domain: str | None,
) -> str:
    d = str(
        domain or ""
    ).strip().upper()

    if d == "PRODUCT":
        return (
            "Use product data-quality language. "
            "Product ID, GTIN, SKU, Product Name, "
            "Product Variant, Item Category, and "
            "Source System may be relevant. "
            "Treat Product Variant as a governed quantity + UOM value "
            "when the deterministic evidence identifies it that way. "
            "Canonical Product Variant format is numeric quantity, "
            "exactly one separator space, and an uppercase UOM token; "
            "examples include '3 OZ', '3.3 OZ', '3.75 LB', and '12 IN'. "
            "Leading or trailing whitespace, repeated internal spaces, "
            "lowercase UOM text, or a missing separator are "
            "standardization/format defects when the deterministic "
            "engine reports them. For example, '3 OZ' is canonical "
            "while '3   OZ' is not. "
            "Never change the numeric quantity or substitute one UOM "
            "for another merely to make a Product Variant pass format "
            "validation. Safe formatting normalization may trim "
            "whitespace, collapse repeated separator whitespace to one "
            "space, and uppercase the UOM token when business meaning "
            "is preserved. "
            "A structurally valid UOM that is outside the effective "
            "approved UOM vocabulary is a governed-value issue, not "
            "proof that the UOM is factually wrong. Do not silently map "
            "or replace an unapproved UOM. Recommend steward/configuration "
            "review unless authoritative evidence supports a correction. "
            "Do not treat duplicate Product IDs as automatically safe "
            "to merge. Duplicate identifiers require comparison of "
            "supporting product attributes before remediation."
            "GTIN checksum failure proves only that the supplied GTIN is not "
            "checksum-valid; it does not prove which digit is incorrect or that "
            "recomputing the check digit would produce the authoritative GTIN. "
        )

    if d == "SUPPLIER":
        return (
            "Use supplier/vendor data-quality language. "
            "Supplier ID, supplier name, tax ID, email, "
            "address, and source system may be relevant. "
            "Tax ID and supplier identifier issues can "
            "have financial and procurement impact."
        )

    if d == "PROVIDER":
        return (
            "Use healthcare provider data-quality language. "
            "Provider ID, NPI, provider name, specialty, "
            "email, practice address, and source system "
            "may be relevant. NPI validity requires exactly "
            "10 numeric digits unless a configured policy "
            "states otherwise."
        )

    if d == "PATIENT":
        return (
            "Use healthcare patient data-quality language. "
            "Patient ID, name, DOB, email, phone number, address, and "
            "source system may be relevant. Identity and "
            "duplicate issues should favor steward review "
            "because incorrect remediation can join "
            "different patients."
        )

    if d == "BANKING":
        return (
            "Use banking customer and account data-quality language. "
            "Account ID, Customer ID, SAP Business Partner ID, routing number, "
            "account last four digits, account type, currency, institution name, "
            "customer name, email, address, phone number, and source system may be relevant. "
            "Account ID, Customer ID, and SAP Business Partner ID conflicts can represent "
            "significant identity or relationship risk. "
            "Routing number identifies a financial institution and must not be treated "
            "as a unique customer or account identifier. "
            "Account last four digits are not globally unique and must not independently "
            "justify duplicate remediation or account consolidation. "
            "Duplicate or conflicting banking identity records should default to steward "
            "review unless deterministic evidence proves the remediation is safe."
        )

    if d == "LOCATION":
        return (
            "Use physical-site/location data-quality language. "
            "Location ID, Site ID, Location Code, Location Name, Location Type, physical address, "
            "Parent Location ID, Organization Entity ID, and source system may be relevant. "
            "Do not treat a shared physical address as proof of a duplicate Location. "
            "Location hierarchy must be represented through parent_location_id; do not fabricate "
            "child identifiers or hierarchy relationships. Missing identifiers must not be invented. "
            "Address standardization may normalize presentation when deterministic, but changing the "
            "physical site, hierarchy, or organization relationship requires authoritative evidence "
            "or steward review."
        )

    if d == "ORGANIZATION":
        return (
            "Use enterprise organization data-quality language. "
            "Organization Entity ID, Organization Code, Organization Name, Organization Type, "
            "Parent Organization ID, Organization Status, related location evidence, and source "
            "system may be relevant. Never use internal tenant organization_id as mastered entity "
            "data. Do not merge organizations based on name similarity alone. Missing organization "
            "identifiers, parent relationships, types, or statuses must not be fabricated. Changes "
            "to hierarchy, status, or organization identity require authoritative evidence or steward "
            "review unless the supplied deterministic rule proves a presentation-only correction."
        )

    if d == "CUSTOMER":
        return (
            "Use customer/member data-quality language. "
            "Customer or member ID, full name, DOB, email, "
            "phone number, address, and source system may be relevant. "
            "Duplicate identity remediation should require "
            "review when identity evidence is ambiguous."
        )


    return (
        "Use generic enterprise data-quality language. "
        "Recommend deterministic, explainable, "
        "non-destructive remediation."
    )



GTIN_REMEDIATION_GUARDRAILS = """
GTIN REMEDIATION GUARDRAILS:

- Never invent, guess, synthesize, or fabricate a GTIN.
- Never replace an ambiguous GTIN with a value that is not explicitly supported
  by authoritative evidence.
- Never delete records as a GTIN remediation strategy.
- Do not generate destructive DML without explicit safety justification.
- Prefer a diagnostic SELECT preview and a proposed transformation explanation
  before any corrective UPDATE.
- Only generate UPDATE when the correction is deterministic, reversible, and
  limited to the affected rows with a WHERE clause.
- If the corrected GTIN cannot be derived safely from the supplied evidence,
  return MANUAL_REVIEW.
- If authoritative source confirmation is required, return MANUAL_REVIEW rather
  than fabricating a correction.
""".strip()


GTIN_CHECKSUM_REMEDIATION_GUARDRAILS = """
GTIN_CHECKSUM_VALIDITY SPECIAL RULES:

- A failed GTIN checksum means the supplied GTIN is not checksum-valid.
- Do NOT conclude that a specific digit is wrong unless authoritative
  evidence identifies the incorrect digit.
- Recalculating the final GTIN check digit does NOT prove that the
  preceding digits are correct.
- A mathematically valid replacement check digit can still produce a
  commercially incorrect GTIN when any preceding digit is wrong,
  missing, transposed, truncated, or otherwise corrupted.
- Do not auto-correct a checksum failure unless the base digits are
  explicitly known to be authoritative.
- When the base digits are not authoritative, require steward review
  and recommend validation against the source system, supplier master,
  internal MDM golden record, or GS1-authoritative reference before
  any production change.
- Never imply that checksum recomputation alone verifies commercial
  product identity.
- Never state that the GTIN "itself is wrong" when the evidence only
  proves checksum failure. State instead that the supplied GTIN is
  not checksum-valid and requires authoritative verification.
""".strip()

GOVERNED_PRODUCT_VARIANT_STANDARDIZATION_REGEX = (
    r"^\s*([0-9]+(?:\.[0-9]+)?)\s+([A-Za-z]+)\s*$"
)

GOVERNED_FULL_NAME_STANDARDIZATION_REGEX = (
    r"^\s*(\S+(?:\s+\S+)*)\s*$"
)


PRODUCT_VARIANT_REMEDIATION_GUARDRAILS = """
PRODUCT VARIANT / UOM REMEDIATION GUARDRAILS:
- Treat product_variant as a semantic quantity + UOM value when the persisted
  deterministic recommendation identifies that structure.
- Canonical examples include: 3 OZ, 3.3 OZ, 3.75 LB, and 12 IN.
- Canonical formatting requires:
  * a numeric integer or decimal quantity,
  * exactly one separator space,
  * an uppercase UOM token,
  * no leading whitespace,
  * no trailing whitespace.
- "3 OZ" is canonical.
- "3   OZ" is not canonical and may be safely normalized to "3 OZ" only when
  the supplied evidence establishes that the issue is formatting/standardization.
- Formatting-only fixes may trim leading/trailing whitespace, collapse repeated
  whitespace between quantity and UOM to one space, and uppercase the UOM token.
- Never change the numeric quantity merely to satisfy a format rule.
- Never convert units or substitute UOM semantics (for example LB -> KG, OZ -> G,
  IN -> MM) unless an authoritative conversion requirement and conversion logic
  are explicitly supplied in the persisted evidence.
- Never invent or guess a missing UOM.
- Never replace an unknown or unapproved UOM with a different allowed UOM merely
  to make the value pass validation.
- When a UOM is structurally valid but outside the approved governed vocabulary,
  prefer steward/configuration review. The correct action may be to approve a new
  tenant-specific UOM value rather than mutate source data.
- Never fabricate a missing quantity or UOM value.
- If the deterministic finding is PRODUCT_VARIANT_STANDARDIZATION
  and the canonical result can be derived without changing
  quantity or UOM meaning, prefer REGEX in AUTO mode.
- A bounded UPDATE may be generated only when SQL is explicitly
  requested as the artifact preference.
- If the deterministic finding is PRODUCT_VARIANT_FORMAT_VALIDITY and the
  corrected semantic value cannot be derived safely from formatting alone,
  return REGEX for validation or MANUAL_REVIEW rather than fabricating a value.
- If the deterministic finding is PRODUCT_VARIANT_UOM_ALLOWED_VALUE, do not
  auto-substitute another UOM. Return MANUAL_REVIEW unless authoritative evidence
  explicitly proves the required correction.
""".strip()


PRODUCT_VARIANT_FORMAT_REMEDIATION_GUARDRAILS = """
PRODUCT_VARIANT_FORMAT_VALIDITY SPECIAL RULES:

- The governed structural pattern is numeric quantity + one space + uppercase UOM.
- A concise validation regex may use the generic structure:
  ^[0-9]+(?:\\.[0-9]+)? [A-Z]+$
- The regex validates structure only. It does not prove that the UOM token is
  approved or semantically correct.
- Do not use the regex to infer missing quantity or UOM values.
- If a source value can be normalized deterministically by whitespace/case only,
  a bounded standardization UPDATE may be appropriate.
- If semantic correction is required, return MANUAL_REVIEW.
""".strip()


PRODUCT_VARIANT_UOM_REMEDIATION_GUARDRAILS = """
PRODUCT_VARIANT_UOM_ALLOWED_VALUE SPECIAL RULES:

- The UOM token has already passed structural parsing but is outside the effective
  governed UOM vocabulary for this profiling configuration.
- Do not assume the token is factually invalid solely because it is not currently
  approved.
- Do not auto-map the token to OZ, LB, KG, G, EA, CS, PK, IN, or any other UOM.
- The safe remediation is normally steward/configuration review so an authorized
  user can determine whether the UOM should be added to the tenant-specific policy
  or corrected from an authoritative source.
- Return MANUAL_REVIEW unless the persisted evidence explicitly contains the
  authoritative replacement value.
""".strip()

FULL_NAME_STANDARDIZATION_REMEDIATION_GUARDRAILS = """
FULL_NAME_STANDARDIZATION SPECIAL RULES:

- Treat FULL_NAME_STANDARDIZATION as deterministic presentation-only
  normalization when the persisted finding proves that business meaning
  is unchanged.

- NORMALIZE_FULL_NAME may trim leading/trailing whitespace, collapse
  repeated internal whitespace, and apply the governed casing convention.

- Never add, remove, reorder, infer, or semantically alter name components.

- REGEX may be used for validation or as a governed normalization contract,
  but REGEX alone does not constitute executable SQL source-data writeback.

- For SQL-backed sources, a corrective UPDATE is eligible only when the
  server-side governed remediation layer verifies complete persisted
  observed_value -> proposed_value evidence for every affected value.

- The AI model must never invent or reconstruct row-level name values.

- When complete server-side evidence is unavailable, do not generate a
  source-data UPDATE from aggregate evidence alone.

- If semantic name correction is required, return MANUAL_REVIEW.
""".strip()


PHONE_NUMBER_JUNK_VALUE_REMEDIATION_GUARDRAILS = """
PHONE_NUMBER_JUNK_VALUE SPECIAL RULES:

- Treat the persisted physical source_column as authoritative. Never substitute
  the logical field_name when generating SQL.
- A broad phone-format predicate does not prove that a value is junk and must
  never be used to null or overwrite every non-conforming phone number.
- In particular, do not generate corrective UPDATE SQL whose affected-row
  predicate uses NOT REGEXP_LIKE, NOT REGEXP_CONTAINS, NOT RLIKE, or an
  equivalent broad format-negation test.
- Default to MANUAL_REVIEW unless the persisted evidence contains every exact
  affected observed junk value and the evidence count agrees with the stated
  affected-record count.
- When complete exact junk-value evidence is present, SQL may only target those
  literal values through equality or an explicit IN allowlist on source_column.
- Never infer, fabricate, or normalize a replacement telephone number.
- Setting an exact, evidence-confirmed junk value to NULL is allowed only when
  the UPDATE is bounded to that exact persisted allowlist and requires steward
  approval.
- If source_column is missing, evidence is incomplete, or the affected values
  cannot be deterministically enumerated, return MANUAL_REVIEW.
""".strip()


def _dq_remediation_domain_guardrails(
    *,
    domain: str,
    rule_id: str | None,
    field_name: str | None,
) -> str:
    """Return generic domain/rule-specific remediation guardrails."""
    normalized_domain = str(domain or "").strip().upper()
    normalized_rule_id = str(rule_id or "").strip().upper()
    normalized_field_name = str(field_name or "").strip().lower()

    guardrails: list[str] = []

    if (
        normalized_rule_id == "FULL_NAME_STANDARDIZATION"
        and normalized_field_name == "full_name"
    ):
        guardrails.append(
            FULL_NAME_STANDARDIZATION_REMEDIATION_GUARDRAILS
        )

    if normalized_rule_id == "PHONE_NUMBER_JUNK_VALUE":
        guardrails.append(
            PHONE_NUMBER_JUNK_VALUE_REMEDIATION_GUARDRAILS
        )

    if (
        normalized_domain == "PRODUCT"
        and (
            normalized_field_name == "gtin"
            or normalized_rule_id.startswith("GTIN_")
        )
    ):
        guardrails.append(GTIN_REMEDIATION_GUARDRAILS)

    if normalized_rule_id == "GTIN_CHECKSUM_VALIDITY":
        guardrails.append(
            GTIN_CHECKSUM_REMEDIATION_GUARDRAILS
        )

    if (
        normalized_domain == "PRODUCT"
        and (
            normalized_field_name == "product_variant"
            or normalized_rule_id.startswith(
                "PRODUCT_VARIANT_"
            )
        )
    ):
        guardrails.append(
            PRODUCT_VARIANT_REMEDIATION_GUARDRAILS
        )
        

    if (
        normalized_rule_id
        == "PRODUCT_VARIANT_FORMAT_VALIDITY"
    ):
        guardrails.append(
            PRODUCT_VARIANT_FORMAT_REMEDIATION_GUARDRAILS
        )

    if (
        normalized_rule_id
        == "PRODUCT_VARIANT_UOM_ALLOWED_VALUE"
    ):
        guardrails.append(
            PRODUCT_VARIANT_UOM_REMEDIATION_GUARDRAILS
        )

    return "\n\n".join(guardrails).strip()

def build_dq_remediation_prompt(
    *,
    organization_id: str,
    revision_guidance: str | None = None,
    recommendation: Dict[str, Any],
    source_table: str = "source_table",
    sql_dialect: str = "BIGQUERY",
    artifact_preference: str = "AUTO",
) -> str:
    """
    Build a tenant-safe prompt for generating one
    implementation-ready DQ remediation artifact.

    The recommendation must already be persisted and
    tenant-scoped before this function is called.
    """

    effective_organization_id = (
        _require_organization_id(
            organization_id
        )
    )

    if not isinstance(
        recommendation,
        dict,
    ):
        raise ValueError(
            "recommendation must be a dictionary."
        )
    

    revision_section = ""

    normalized_revision_guidance = str(
        revision_guidance or ""
    ).strip()

    if normalized_revision_guidance:
        revision_section = f"""
    ------------------------------------------------------------
    STEWARD REVISION GUIDANCE
    ------------------------------------------------------------

    A data steward reviewed the previous remediation and
    requested changes.

    Requested revision:

    {normalized_revision_guidance}

    Treat this as explicit steward revision guidance.

    Revise the prior remediation to address this request
    where it can be done safely and deterministically.

    Do not simply repeat the prior remediation when it
    conflicts with the requested revision.

    The steward request does NOT override data-safety,
    tenant-isolation, domain, or remediation guardrails.

    If the requested change cannot be implemented safely
    from the supplied evidence, return MANUAL_REVIEW and
    explain why.
    """.strip()

    recommendation_organization_id = str(
        recommendation.get(
            "organization_id"
        )
        or ""
    ).strip()

    if (
        recommendation_organization_id
        and recommendation_organization_id
        != effective_organization_id
    ):
        raise ValueError(
            "DQ recommendation does not belong "
            "to the authenticated organization."
        )

    recommendation_id = str(
        recommendation.get(
            "recommendation_id"
        )
        or ""
    ).strip()

    if not recommendation_id:
        raise ValueError(
            "recommendation_id is required."
        )

    normalized_domain = str(
        recommendation.get(
            "domain"
        )
        or ""
    ).strip().upper()

    if not normalized_domain:
        raise ValueError(
            "domain is required."
        )

    normalized_rule_id = str(
        recommendation.get("rule_id") or ""
    ).strip().upper()

    normalized_field_name = str(
        recommendation.get("field_name") or ""
    ).strip().lower()

    governed_remediation_regex: str | None = None

    if (
        normalized_domain == "PRODUCT"
        and normalized_rule_id == "PRODUCT_VARIANT_STANDARDIZATION"
        and normalized_field_name == "product_variant"
    ):
        governed_remediation_regex = (
            GOVERNED_PRODUCT_VARIANT_STANDARDIZATION_REGEX
        )

    elif (
            normalized_rule_id == "FULL_NAME_STANDARDIZATION"
            and normalized_field_name == "full_name"
        ):
            governed_remediation_regex = (
                GOVERNED_FULL_NAME_STANDARDIZATION_REGEX
            )

    remediation_domain_guardrails = (
        _dq_remediation_domain_guardrails(
            domain=normalized_domain,
            rule_id=normalized_rule_id,
            field_name=normalized_field_name,
        )
    )

    normalized_source_table = str(
        source_table
        or "source_table"
    ).strip()

    if not normalized_source_table:
        raise ValueError(
            "source_table is required."
        )

    normalized_sql_dialect = str(
        sql_dialect
        or "BIGQUERY"
    ).strip().upper()

    if normalized_sql_dialect not in {
        "BIGQUERY",
        "SNOWFLAKE",
        "DATABRICKS",
        "GENERIC",
        "AZURE_SQL",
    }:
        raise ValueError(
            "Unsupported SQL dialect."
        )

    normalized_artifact_preference = str(
        artifact_preference or "AUTO"
    ).strip().upper()

    if normalized_artifact_preference not in {
        "AUTO",
        "SQL",
        "REGEX",
    }:
        raise ValueError(
            "Unsupported artifact_preference."
        )

    # ---------------------------------------------------------
    # Validate tenant ownership first, then remove internal
    # tenant metadata before sending context to the LLM.
    # ---------------------------------------------------------

    safe_recommendation = (
        _strip_tenant_metadata(
            recommendation
        )
    )

    remediation_payload = {
        "recommendation_id":
            recommendation_id,

        "domain":
            normalized_domain,

        "rule_id":
            safe_recommendation.get(
                "rule_id"
            ),

        "field_name":
            safe_recommendation.get(
                "field_name"
            ),

        "source_column":
            safe_recommendation.get(
                "source_column"
            ),

        "dimension":
            safe_recommendation.get(
                "dimension"
            ),

        "severity":
            safe_recommendation.get(
                "severity"
            ),

        "finding_count":
            safe_recommendation.get(
                "finding_count"
            ),

        "affected_record_count":
            safe_recommendation.get(
                "affected_record_count"
            ),

        "affected_percent":
            safe_recommendation.get(
                "affected_percent"
            ),

        # Row-level observed phone values may contain personal data and
        # are intentionally not sent to the LLM. The server-side safety
        # validator can inspect persisted evidence without disclosing it.
        "evidence_sample_count": len(
            safe_recommendation.get("evidence_samples") or []
        ),

        "exact_evidence_values_provided_to_model": False,

        "recommendation_title":
            safe_recommendation.get(
                "recommendation_title"
            ),

        "recommendation_summary":
            safe_recommendation.get(
                "recommendation_summary"
            ),

        "suggested_action":
            safe_recommendation.get(
                "suggested_action"
            ),

        "suggested_rule_type":
            safe_recommendation.get(
                "suggested_rule_type"
            ),

        "existing_suggested_sql":
            safe_recommendation.get(
                "suggested_sql"
            ),

        "existing_suggested_regex":
            safe_recommendation.get(
                "suggested_regex"
            ),

        "governed_remediation_regex":
            governed_remediation_regex,

        "automation_recommendation":
            safe_recommendation.get(
                "automation_recommendation"
            ),

        "automation_confidence":
            safe_recommendation.get(
                "automation_confidence"
            ),

        "status":
            safe_recommendation.get(
                "status"
            ),

        "source_table":
            normalized_source_table,

        "sql_dialect":
            normalized_sql_dialect,

        "artifact_preference":
            normalized_artifact_preference,
    }

    if normalized_artifact_preference == "SQL":
        artifact_preference_section = """
    ------------------------------------------------------------
    STEWARD ARTIFACT PREFERENCE
    ------------------------------------------------------------

    The steward explicitly requested a SQL remediation artifact.

    - Prefer artifact_type SQL when a safe deterministic corrective
      UPDATE can be generated from the persisted evidence.

    - A regex may be used INSIDE the SQL WHERE clause as a predicate
      to identify the affected rows.

    - When regex is embedded inside SQL, artifact_type must remain SQL
      and remediation_regex must be null.

    - Do NOT return a standalone REGEX artifact for this request.

    - The SQL must perform the deterministic correction; do not merely
      return the existing diagnostic SELECT.

    - If the exact corrective value or transformation cannot be derived
      safely and deterministically from the supplied evidence, return
      MANUAL_REVIEW.

    - The steward's SQL preference never overrides domain guardrails,
      authoritative-source requirements, GTIN protections, tenant
      isolation, or SQL safety rules.
        """.strip()
    elif normalized_artifact_preference == "REGEX":
        artifact_preference_section = """
    ------------------------------------------------------------
    STEWARD ARTIFACT PREFERENCE
    ------------------------------------------------------------

    The steward explicitly requested a REGEX remediation artifact.

    - Return artifact_type REGEX when the persisted evidence
      supports a safe deterministic governed normalization.

    - remediation_regex must contain the governed regex pattern.

    - The REGEX identifies values eligible for the governed
      transformation. The approved deterministic execution
      operation performs the actual normalization.

    - A REGEX artifact does not need to perform casing,
      whitespace collapsing, or other transformation by regex
      replacement syntax itself.

    - remediation_sql must be null.

    - Do not convert the requested REGEX artifact into SQL.

    - When governed_remediation_regex is non-null, copy it
      EXACTLY into remediation_regex.

    - Do not claim SQL is required merely because the approved
      execution operation performs trim, whitespace normalization,
      or casing changes.

    - If the governed execution operation would require changing
      business meaning, return MANUAL_REVIEW.

    - REGEX preference never overrides domain guardrails,
      authoritative-source requirements, or tenant isolation.
    """.strip()
    else:
        artifact_preference_section = """
    ------------------------------------------------------------
    STEWARD ARTIFACT PREFERENCE
    ------------------------------------------------------------

    Artifact preference is AUTO.

    Preserve the existing artifact-selection behavior:
    choose SQL, REGEX, or MANUAL_REVIEW according to the evidence,
    domain guardrails, and safety rules.
        """.strip()

    return f"""
    You are the Data Quality Remediation Engineer inside
    AI Data Steward Copilot.

    Your task is to generate ONE implementation-ready
    remediation artifact for ONE already-approved AI
    recommendation candidate.

    The deterministic DQ engine and persisted recommendation
    are the source of truth.

    You must not invent source data, business rules,
    authoritative values, identifiers, policies, systems,
    or record-level facts that are not present in the
    supplied recommendation evidence.

    Your output is for STEWARD REVIEW ONLY.
    Never claim that generated SQL or regex has been executed.

    ------------------------------------------------------------
    PRIMARY GOAL
    ------------------------------------------------------------

    Generate exactly one of the following:

    1. SQL
    - A controlled remediation UPDATE statement.
    - Must include a WHERE clause.
    - Must be limited to rows affected by the stated issue.
    - Must not update unrelated rows.

    2. REGEX
    - A concise validation or standardization regex.
    - Only use REGEX when regex is clearly appropriate.

    3. MANUAL_REVIEW
    - Use when deterministic remediation cannot be safely
    generated from the supplied evidence.

    ------------------------------------------------------------
    STRICT SQL SAFETY RULES
    ------------------------------------------------------------

    - SQL remediation may use UPDATE only.

    - Every UPDATE must contain a WHERE clause.

    - Never generate:
    DELETE
    DROP
    TRUNCATE
    MERGE
    ALTER
    CREATE
    INSERT
    GRANT
    REVOKE

    - Never generate multi-statement SQL.

    - Never generate procedural SQL.

    - Never generate stored procedures.

    - Never generate dynamic SQL.

    - Never generate a script that executes multiple changes.

    - Never update all rows in a table.

    - Never fabricate missing values.

    - Never replace missing required values with guessed,
    default, synthetic, or placeholder values.

    - Never overwrite a field using an authoritative value
    unless that authoritative value is explicitly present
    in the supplied evidence.

    - Never perform duplicate deletion or automatic merge
    through remediation SQL.

    - Duplicate or identity-related remediation should
    normally return MANUAL_REVIEW.

    - Completeness issues requiring enrichment should
    normally return MANUAL_REVIEW unless the remediation
    is purely deterministic formatting/standardization.

    - Standardization fixes may return SQL when they are
    deterministic, reversible, and limited by a WHERE clause.

    - Validity fixes may return SQL only when the correction
    rule is deterministic and does not invent data.

    - Prefer a SELECT preview plus a proposed transformation
    explanation before any corrective UPDATE when there is
    uncertainty about the resulting value.

    - Only generate UPDATE when the correction is deterministic,
    reversible, evidence-supported, and limited by a WHERE clause.

    - SQL must target this source table only:

    {normalized_source_table}

    ------------------------------------------------------------
    SQL DIALECT
    ------------------------------------------------------------

    Requested dialect:

    {normalized_sql_dialect}

    Use syntax compatible with the requested dialect.

    Dialect guidance:

    BIGQUERY:
    - Use BigQuery Standard SQL.
    - CAST(... AS STRING) is acceptable.
    - REGEXP_CONTAINS may be used when appropriate.

    SNOWFLAKE:
    - Use Snowflake SQL syntax.
    - Prefer REGEXP_LIKE when regex predicates are needed.

    DATABRICKS:
    - Use Databricks SQL syntax.
    - Prefer RLIKE when regex predicates are needed.
    - IMPORTANT: When a regex pattern contains backslash escapes such as
    \\s, \\d, \\., \\w, or similar regex metacharacters, use a
    Databricks raw string literal with the r'...' syntax.
    - Use raw regex literals consistently in RLIKE, REGEXP_EXTRACT,
    REGEXP_REPLACE, REGEXP_LIKE, and other Databricks regex functions.
    - Example:
    product_variant RLIKE r'^\\s*([0-9]+(?:\\.[0-9]+)?)\\s+([A-Za-z]+)\\s*$'
    - Example:
    REGEXP_EXTRACT(
        TRIM(product_variant),
        r'^([0-9]+(?:\\.[0-9]+)?)\\s+([A-Za-z]+)\\s*$',
        1
    )
    - Do not place a backslash-containing regex in an ordinary Databricks
    SQL string literal such as '^\\s*...$' because SQL string parsing
    may consume the backslash before the regex engine evaluates it.
    - The SQL artifact shown to the steward must already contain the
    correct Databricks raw regex literal. Do not rely on the execution
    layer to rewrite or escape approved SQL.

    AZURE_SQL:
    - Use Microsoft Azure SQL / SQL Server T-SQL syntax.
    - Use schema-qualified table names such as dbo.Customer_Profile_Test.
    - Use square brackets for identifiers when quoting is required.
    - Use TOP rather than LIMIT.
    - Do not use REGEXP_LIKE, REGEXP_CONTAINS, RLIKE, or BigQuery-style regex functions.
    - Do not use BigQuery CAST(... AS STRING); use T-SQL-compatible data types.
    - For deterministic string normalization, use T-SQL functions such as LTRIM, RTRIM, TRIM, UPPER, LOWER, REPLACE, and LIKE only when appropriate to the governed rule.
    - Generated remediation must remain one bounded UPDATE with a restrictive WHERE clause.

    GENERIC:
    - Use broadly portable ANSI-style SQL where possible.

    ------------------------------------------------------------
    REGEX SAFETY RULES
    ------------------------------------------------------------

    - Return a regex only when the rule clearly requires one.

    - Regex must be syntactically valid.

    - Regex should validate or standardize format only.

    - Do not use regex to infer or fabricate missing data.

    - Keep regex concise.

    - When governed_remediation_regex is non-null, it is an
      authoritative executable remediation contract.

    - When governed_remediation_regex is non-null and artifact_type
      is REGEX, copy governed_remediation_regex EXACTLY into
      remediation_regex.

    - Do not simplify, rewrite, broaden, narrow, optimize, or replace
      a supplied governed_remediation_regex.

    - Do not substitute a validation-only regex when an exact governed
      remediation regex has been supplied.

    ------------------------------------------------------------
    DOMAIN
    ------------------------------------------------------------

    {normalized_domain}

    DOMAIN GUIDANCE:

    {_dq_domain_guidance(normalized_domain)}

    ------------------------------------------------------------
    DOMAIN / RULE-SPECIFIC REMEDIATION GUARDRAILS
    ------------------------------------------------------------

    {remediation_domain_guardrails or "No additional domain-specific remediation guardrails."}

    ------------------------------------------------------------
    PERSISTED DQ RECOMMENDATION
    ------------------------------------------------------------

    {json.dumps(remediation_payload, indent=2)}

    {revision_section}

    {artifact_preference_section}

    ------------------------------------------------------------
    DECISION LOGIC
    ------------------------------------------------------------

    Choose artifact_type using these rules:

    - When artifact_preference is SQL:
      return SQL when a deterministic corrective UPDATE can be
      generated safely. Regex predicates may be embedded inside
      that UPDATE's WHERE clause. Otherwise return MANUAL_REVIEW.
      Do not return standalone REGEX.

    - When artifact_preference is AUTO:

      SQL:
      Use when a deterministic corrective UPDATE can be
      generated safely.

      REGEX:
      Use when the issue is primarily a format-validation
      or pattern-standardization problem.

    - MANUAL_REVIEW:
    Use when remediation requires:
    authoritative source lookup,
    steward interpretation,
    record survivorship,
    deduplication decisions,
    identity resolution,
    missing-value invention,
    business context,
    or non-deterministic correction.

    PRODUCT VARIANT DECISION RULES:

    - PRODUCT_VARIANT_STANDARDIZATION:

      In AUTO mode, return artifact_type REGEX when the correction is
      purely deterministic formatting and preserves both numeric quantity
      and UOM meaning.

      When governed_remediation_regex is supplied, remediation_regex
      MUST equal governed_remediation_regex exactly.

      Do not generate a different validation regex.

      The governed regex captures the numeric quantity and UOM token so
      the approved execution contract can preserve quantity, normalize
      separator whitespace, and uppercase the UOM without changing
      business meaning.

      When artifact_preference is SQL, a bounded corrective UPDATE may
      be generated instead, provided it preserves quantity and UOM
      meaning and includes a WHERE clause.

    - PRODUCT_VARIANT_FORMAT_VALIDITY:
      In AUTO mode, prefer REGEX when the requested artifact is
      validation of the quantity + UOM structure. When the steward
      explicitly requests SQL, use SQL only when the exact corrected
      value can be deterministically derived from formatting alone;
      the structural regex may be embedded in the SQL WHERE predicate.
      Otherwise use MANUAL_REVIEW.

    - PRODUCT_VARIANT_UOM_ALLOWED_VALUE:
      Prefer MANUAL_REVIEW. Do not replace an unapproved UOM with
      another UOM. The appropriate remediation may be an authorized
      tenant-specific configuration change rather than source-data
      correction.

    If the supplied recommendation already contains
    diagnostic SQL, do NOT merely return the same diagnostic
    SELECT.

    The remediation SQL must perform the safe correction,
    not just identify bad records.

    ------------------------------------------------------------
    EXPECTED EXAMPLES
    ------------------------------------------------------------

    Safe standardization SQL example:

    UPDATE source_table
    SET customer_name = UPPER(TRIM(customer_name))
    WHERE customer_name IS NOT NULL
    AND customer_name != UPPER(TRIM(customer_name));

    Safe SQL-with-regex example for BIGQUERY when the correction
    itself is deterministic:

    UPDATE source_table
    SET currency = UPPER(TRIM(CAST(currency AS STRING)))
    WHERE currency IS NOT NULL
    AND NOT REGEXP_CONTAINS(
      UPPER(TRIM(CAST(currency AS STRING))),
      r'^[A-Z]{3}$'
    );

    A regex in the WHERE clause does not make the artifact a REGEX
    artifact. It remains artifact_type SQL.

    Unsafe examples that must never be returned:

    DELETE FROM source_table;

    UPDATE source_table
    SET customer_name = NULL;

    UPDATE source_table
    SET customer_name = 'UNKNOWN';

    UPDATE source_table
    SET customer_name = UPPER(TRIM(customer_name));

    The last example is unsafe because it has no WHERE clause.

    ------------------------------------------------------------
    RETURN ONLY VALID JSON
    ------------------------------------------------------------

    Return exactly this structure:

    {{
    "recommendation_id":
    "{recommendation_id}",

    "artifact_type":
    "SQL|REGEX|MANUAL_REVIEW",

    "remediation_sql":
    "single controlled UPDATE statement or null",

    "remediation_regex":
    "regex pattern or null",

    "reasoning_summary":
    "brief explanation grounded only in the persisted DQ recommendation"
    }}

    ------------------------------------------------------------
    OUTPUT RULES
    ------------------------------------------------------------

    - Return raw JSON only.

    - Do not return markdown.

    - Do not return code fences.

    - Do not return commentary before or after JSON.

    - Preserve recommendation_id exactly.

    - Return only one artifact.

    - If artifact_type is SQL:
    remediation_sql must be non-null.
    remediation_regex must be null.
    Regex logic may appear inside remediation_sql as a WHERE predicate
    using syntax appropriate for the requested SQL dialect.

    - If artifact_type is REGEX:
    remediation_regex must be non-null.
    remediation_sql must be null.
    - If governed_remediation_regex is non-null and artifact_type is REGEX:
      remediation_regex must exactly equal governed_remediation_regex.

    - Never generate an alternative regex for an allow-listed governed
      remediation rule.

    - If artifact_type is MANUAL_REVIEW:
    remediation_sql must be null.
    remediation_regex must be null.

    - Never expose or mention internal organization_id,
    customer_id, or user_id values.

    - Do not claim execution occurred.

    - Steward approval is always required before any
    generated remediation is executed.
    """.strip()


def build_match_explain_prompt(
    req: MatchExplainRequest,
    *,
    organization_id: str,
    learning_context: str | None = None,
    policy_context: str | None = None,
    policy_recommendation: dict[str, Any] | None = None,
    signal_packets: list[Any] | None = None,
) -> str:
    effective_organization_id = _require_organization_id(
        organization_id
    )

    _assert_context_organization(
        organization_id=effective_organization_id,
        context=policy_recommendation,
        context_name="Policy recommendation",
    )

    _assert_signal_packet_organizations(
        organization_id=effective_organization_id,
        signal_packets=signal_packets,
    )

    recommendation_text = "N/A"
    recommendation_reason = "N/A"
    risk_band = "N/A"
    highest_risk_level = "N/A"
    recommended_actions_seen = "N/A"
    signal_packets = signal_packets or []
    prompt_signal_packets = _strip_tenant_metadata(
        signal_packets
    )

    domain = (req.domain or "CUSTOMER").upper()
    record_a = req.record_a
    record_b = req.record_b

    entity_id_a = _resolve_entity_id(record_a, domain)
    entity_id_b = _resolve_entity_id(record_b, domain)

    source_system_a = _get_value(record_a, "source_system")
    source_system_b = _get_value(record_b, "source_system")

    first_name_a = _get_value(record_a, "first_name")
    first_name_b = _get_value(record_b, "first_name")

    last_name_a = _get_value(record_a, "last_name")
    last_name_b = _get_value(record_b, "last_name")

    dob_a = _get_value(record_a, "dob")
    dob_b = _get_value(record_b, "dob")

    email_a = _get_value(record_a, "email")
    email_b = _get_value(record_b, "email")

    address_a = _get_value(record_a, "address")
    address_b = _get_value(record_b, "address")

    if domain == "PROVIDER":
        first_name_a = _get_value(record_a, "provider_first_name") or first_name_a
        first_name_b = _get_value(record_b, "provider_first_name") or first_name_b

        last_name_a = _get_value(record_a, "provider_last_name") or last_name_a
        last_name_b = _get_value(record_b, "provider_last_name") or last_name_b

        email_a = _get_value(record_a, "provider_email") or email_a
        email_b = _get_value(record_b, "provider_email") or email_b

        specialty_a = _get_value(record_a, "specialty")
        specialty_b = _get_value(record_b, "specialty")

        npi_a = _get_value(record_a, "npi")
        npi_b = _get_value(record_b, "npi")

        provider_id_a = _get_value(record_a, "provider_id") or entity_id_a
        provider_id_b = _get_value(record_b, "provider_id") or entity_id_b


    if policy_recommendation:
        recommendation_text = policy_recommendation.get("recommendation", "N/A")
        recommendation_reason = policy_recommendation.get(
            "recommendation_reason",
            "N/A",
        )
        risk_band = policy_recommendation.get("risk_band", "N/A")
        highest_risk_level = policy_recommendation.get(
            "highest_risk_level",
            "N/A",
        )

        actions_seen = policy_recommendation.get("recommended_actions_seen", [])
        recommended_actions_seen = ", ".join(actions_seen) if actions_seen else "None"

    if domain == "PROVIDER":
        record_evidence = f"""
    Record A Provider Evidence:
    - provider_id: {_fmt(_get_value(record_a, "provider_id"))}
    - npi: {_fmt(_get_value(record_a, "npi"))}
    - phone_number: {_fmt(_get_value(record_a, "phone_number"))}
    - provider_first_name: {_fmt(_get_value(record_a, "provider_first_name") or _get_value(record_a, "first_name"))}
    - provider_last_name: {_fmt(_get_value(record_a, "provider_last_name") or _get_value(record_a, "last_name"))}
    - provider_email: {_fmt(_get_value(record_a, "provider_email") or _get_value(record_a, "email"))}
    - specialty: {_fmt(_get_value(record_a, "specialty"))}
    - practice_address: {_fmt(_get_value(record_a, "provider_address") or _get_value(record_a, "address"))}
    - source_system: {_fmt(_get_value(record_a, "source_system"))}

    Record B Provider Evidence:
    - provider_id: {_fmt(_get_value(record_b, "provider_id"))}
    - npi: {_fmt(_get_value(record_b, "npi"))}
    - phone_number: {_fmt(_get_value(record_b, "phone_number"))}
    - provider_first_name: {_fmt(_get_value(record_b, "provider_first_name") or _get_value(record_b, "first_name"))}
    - provider_last_name: {_fmt(_get_value(record_b, "provider_last_name") or _get_value(record_b, "last_name"))}
    - provider_email: {_fmt(_get_value(record_b, "provider_email") or _get_value(record_b, "email"))}
    - specialty: {_fmt(_get_value(record_b, "specialty"))}
    - practice_address: {_fmt(_get_value(record_b, "provider_address") or _get_value(record_b, "address"))}
    - source_system: {_fmt(_get_value(record_b, "source_system"))}
    """

    elif domain == "PRODUCT":
        record_evidence = f"""
    Record A:
    - Product Name: {getattr(record_a, "product_name", None)}
    - Product Variant / Pack Size: {getattr(record_a, "product_variant", None)}
    - Item Category: {getattr(record_a, "item_category", None)}
    - GTIN: {getattr(record_a, "gtin", None)}
    - SKU: {getattr(record_a, "sku", None)}

    Record B:
    - Product Name: {getattr(record_b, "product_name", None)}
    - Product Variant / Pack Size: {getattr(record_b, "product_variant", None)}
    - Item Category: {getattr(record_b, "item_category", None)}
    - GTIN: {getattr(record_b, "gtin", None)}
    - SKU: {getattr(record_b, "sku", None)}
"""

    elif domain == "SUPPLIER":
        record_evidence = f"""
    Record A Supplier Evidence:
    - supplier_id: {_fmt(_get_value(record_a, "supplier_id") or entity_id_a)}
    - tax_id: {_fmt(_get_value(record_a, "tax_id"))}
    - supplier_name: {_fmt(_get_value(record_a, "supplier_name") or _get_value(record_a, "first_name"))}
    - contact_email: {_fmt(_get_value(record_a, "contact_email") or _get_value(record_a, "email"))}
    - supplier_address: {_fmt(_get_value(record_a, "supplier_address") or _get_value(record_a, "address"))}
    - source_system: {_fmt(_get_value(record_a, "source_system"))}

    Record B Supplier Evidence:
    - supplier_id: {_fmt(_get_value(record_b, "supplier_id") or entity_id_b)}
    - tax_id: {_fmt(_get_value(record_b, "tax_id"))}
    - supplier_name: {_fmt(_get_value(record_b, "supplier_name") or _get_value(record_b, "first_name"))}
    - contact_email: {_fmt(_get_value(record_b, "contact_email") or _get_value(record_b, "email"))}
    - supplier_address: {_fmt(_get_value(record_b, "supplier_address") or _get_value(record_b, "address"))}
    - source_system: {_fmt(_get_value(record_b, "source_system"))}
    """

    elif domain == "LOCATION":
        record_evidence = f"""
    Record A Location Evidence:
    - location_id: {_fmt(_get_value(record_a, "location_id") or entity_id_a)}
    - site_id: {_fmt(_get_value(record_a, "site_id"))}
    - location_code: {_fmt(_get_value(record_a, "location_code"))}
    - location_name: {_fmt(_get_value(record_a, "location_name"))}
    - location_type: {_fmt(_get_value(record_a, "location_type"))}
    - physical_address: {_fmt(_get_value(record_a, "location_address") or _get_value(record_a, "address"))}
    - parent_location_id: {_fmt(_get_value(record_a, "parent_location_id"))}
    - organization_entity_id: {_fmt(_get_value(record_a, "organization_entity_id"))}
    - source_system: {_fmt(_get_value(record_a, "source_system"))}

    Record B Location Evidence:
    - location_id: {_fmt(_get_value(record_b, "location_id") or entity_id_b)}
    - site_id: {_fmt(_get_value(record_b, "site_id"))}
    - location_code: {_fmt(_get_value(record_b, "location_code"))}
    - location_name: {_fmt(_get_value(record_b, "location_name"))}
    - location_type: {_fmt(_get_value(record_b, "location_type"))}
    - physical_address: {_fmt(_get_value(record_b, "location_address") or _get_value(record_b, "address"))}
    - parent_location_id: {_fmt(_get_value(record_b, "parent_location_id"))}
    - organization_entity_id: {_fmt(_get_value(record_b, "organization_entity_id"))}
    - source_system: {_fmt(_get_value(record_b, "source_system"))}
    """

    elif domain == "ORGANIZATION":
        record_evidence = f"""
    Record A Organization Evidence:
    - organization_entity_id: {_fmt(_get_value(record_a, "organization_entity_id") or entity_id_a)}
    - organization_code: {_fmt(_get_value(record_a, "organization_code"))}
    - organization_name: {_fmt(_get_value(record_a, "organization_name"))}
    - organization_type: {_fmt(_get_value(record_a, "organization_type"))}
    - parent_organization_id: {_fmt(_get_value(record_a, "parent_organization_id"))}
    - organization_status: {_fmt(_get_value(record_a, "organization_status"))}
    - location_id: {_fmt(_get_value(record_a, "location_id"))}
    - source_system: {_fmt(_get_value(record_a, "source_system"))}

    Record B Organization Evidence:
    - organization_entity_id: {_fmt(_get_value(record_b, "organization_entity_id") or entity_id_b)}
    - organization_code: {_fmt(_get_value(record_b, "organization_code"))}
    - organization_name: {_fmt(_get_value(record_b, "organization_name"))}
    - organization_type: {_fmt(_get_value(record_b, "organization_type"))}
    - parent_organization_id: {_fmt(_get_value(record_b, "parent_organization_id"))}
    - organization_status: {_fmt(_get_value(record_b, "organization_status"))}
    - location_id: {_fmt(_get_value(record_b, "location_id"))}
    - source_system: {_fmt(_get_value(record_b, "source_system"))}
    """

    elif domain == "BANKING":
        record_evidence = f"""
        Record A Banking Evidence:
        - account_id: {_fmt(_get_value(record_a, "account_id"))}
        - banking_customer_id: {_fmt(_get_value(record_a, "banking_customer_id"))}
        - sap_business_partner_id: {_fmt(_get_value(record_a, "sap_business_partner_id"))}
        - customer_name: {_fmt(_get_value(record_a, "customer_name") or _get_value(record_a, "full_name"))}
        - routing_number: {_fmt(_get_value(record_a, "routing_number"))}
        - account_last_four: {_fmt(_get_value(record_a, "account_last_four"))}
        - account_type: {_fmt(_get_value(record_a, "account_type"))}
        - currency: {_fmt(_get_value(record_a, "currency"))}
        - institution_name: {_fmt(_get_value(record_a, "institution_name"))}
        - email: {_fmt(_get_value(record_a, "email"))}
        - phone_number: {_fmt(_get_value(record_a, "phone_number"))}
        - address: {_fmt(_get_value(record_a, "address"))}
        - source_system: {_fmt(_get_value(record_a, "source_system"))}

        Record B Banking Evidence:
        - account_id: {_fmt(_get_value(record_b, "account_id"))}
        - banking_customer_id: {_fmt(_get_value(record_b, "banking_customer_id"))}
        - sap_business_partner_id: {_fmt(_get_value(record_b, "sap_business_partner_id"))}
        - customer_name: {_fmt(_get_value(record_b, "customer_name") or _get_value(record_b, "full_name"))}
        - routing_number: {_fmt(_get_value(record_b, "routing_number"))}
        - account_last_four: {_fmt(_get_value(record_b, "account_last_four"))}
        - account_type: {_fmt(_get_value(record_b, "account_type"))}
        - currency: {_fmt(_get_value(record_b, "currency"))}
        - institution_name: {_fmt(_get_value(record_b, "institution_name"))}
        - email: {_fmt(_get_value(record_b, "email"))}
        - phone_number: {_fmt(_get_value(record_b, "phone_number"))}
        - address: {_fmt(_get_value(record_b, "address"))}
        - source_system: {_fmt(_get_value(record_b, "source_system"))}
        """

    elif domain == "CUSTOMER":
        customer_full_name_a = (
            _get_value(record_a, "full_name")
            or " ".join(
                filter(
                    None,
                    [
                        _get_value(record_a, "first_name"),
                        _get_value(record_a, "last_name"),
                    ],
                )
            )
        )

        customer_full_name_b = (
            _get_value(record_b, "full_name")
            or " ".join(
                filter(
                    None,
                    [
                        _get_value(record_b, "first_name"),
                        _get_value(record_b, "last_name"),
                    ],
                )
            )
        )

        record_evidence = f"""
        Record A Customer Evidence:
        - member_id: {_fmt(_get_value(record_a, "member_id") or entity_id_a)}
        - full_name: {_fmt(customer_full_name_a)}
        - dob: {_fmt(_get_value(record_a, "dob"))}
        - email: {_fmt(_get_value(record_a, "email"))}
        - phone_number: {_fmt(_get_value(record_a, "phone_number"))}
        - address: {_fmt(_get_value(record_a, "address"))}
        - source_system: {_fmt(_get_value(record_a, "source_system"))}

        Record B Customer Evidence:
        - member_id: {_fmt(_get_value(record_b, "member_id") or entity_id_b)}
        - full_name: {_fmt(customer_full_name_b)}
        - dob: {_fmt(_get_value(record_b, "dob"))}
        - email: {_fmt(_get_value(record_b, "email"))}
        - phone_number: {_fmt(_get_value(record_b, "phone_number"))}
        - address: {_fmt(_get_value(record_b, "address"))}
        - source_system: {_fmt(_get_value(record_b, "source_system"))}
        """

    elif domain == "PATIENT":
        record_evidence = f"""
        Record A Patient Evidence:
        - patient_id: {_fmt(_get_value(record_a, "patient_id"))}
        - first_name: {_fmt(_get_value(record_a, "patient_first_name") or _get_value(record_a, "first_name"))}
        - last_name: {_fmt(_get_value(record_a, "patient_last_name") or _get_value(record_a, "last_name"))}
        - dob: {_fmt(_get_value(record_a, "patient_dob") or _get_value(record_a, "dob"))}
        - email: {_fmt(_get_value(record_a, "patient_email") or _get_value(record_a, "email"))}
        - phone_number: {_fmt(_get_value(record_a, "phone_number"))}
        - address: {_fmt(_get_value(record_a, "patient_address") or _get_value(record_a, "address"))}
        - source_system: {_fmt(_get_value(record_a, "source_system"))}

        Record B Patient Evidence:
        - patient_id: {_fmt(_get_value(record_b, "patient_id"))}
        - first_name: {_fmt(_get_value(record_b, "patient_first_name") or _get_value(record_b, "first_name"))}
        - last_name: {_fmt(_get_value(record_b, "patient_last_name") or _get_value(record_b, "last_name"))}
        - dob: {_fmt(_get_value(record_b, "patient_dob") or _get_value(record_b, "dob"))}
        - email: {_fmt(_get_value(record_b, "patient_email") or _get_value(record_b, "email"))}
        - phone_number: {_fmt(_get_value(record_b, "phone_number"))}
        - address: {_fmt(_get_value(record_b, "patient_address") or _get_value(record_b, "address"))}
        - source_system: {_fmt(_get_value(record_b, "source_system"))}
        """

    else:
        record_evidence = f"""
        Record A Evidence:
        - entity_id: {_fmt(entity_id_a)}
        - first_name: {_fmt(first_name_a)}
        - last_name: {_fmt(last_name_a)}
        - dob: {_fmt(dob_a)}
        - email: {_fmt(email_a)}
        - address: {_fmt(address_a)}
        - source_system: {_fmt(source_system_a) }

        Record B Evidence:
        - entity_id: {_fmt(entity_id_b)}
        - first_name: {_fmt(first_name_b)}
        - last_name: {_fmt(last_name_b)}
        - dob: {_fmt(dob_b)}
        - email: {_fmt(email_b)}
        - address: {_fmt(address_b)}
        - source_system: {_fmt(source_system_b) }
        """
    prompt = f"""
You are AI Data Steward Copilot, an explainable AI assistant for MDM match, merge, survivorship, and governance decisions.

Your audience is a data steward, data governance lead, or MDM architect. Your response must be concise, practical, and audit-friendly.

Decision objective:
Evaluate whether the two records should be auto-merged, approved for merge, sent to manual review, or blocked from merge.

Domain-specific guidance:
{_domain_guidance(domain)}

Core instructions:
Critical alignment rules:
- Treat all supplied records, signals, policy context, and steward learning as belonging only to the authenticated tenant.
- Never infer or mention internal organization, customer, or user identifiers.
- The AI explanation must align with the structured signal evidence, policy recommendation, risk band, recommended action, and final confidence.
- Treat the user-provided match_score as a preliminary upstream score only. It may be stale, generic, or overridden by domain-specific signal evidence.
- If match_score conflicts with signal evidence, use the signal evidence and policy recommendation as the source of truth.
- Never say evidence strongly favors merge when deterministic identifiers, critical domain attributes, or weighted signals are materially conflicting.
- If final confidence is low, automation readiness is low, or recommended_action is REVIEW_REQUIRED or BLOCK_MERGE, the explanation_summary must describe mixed, weak, or conflicting evidence.
- If signal evidence shows low contribution from deterministic identifiers, do not describe the records as strong merge candidates even if match_score is high.
- Do not say signal evidence is empty when supplier_id, tax_id, supplier_name,contact_email, supplier_address, or source_system values are present.
- If those values align, describe them as deterministic and supporting evidence.
- If policy still requires review, say governance policy requires steward confirmation, not that evidence is empty.
- Matching address corroborates supplier identity; governance policy still requires steward confirmation before automation.

1. Use the record evidence, structured signal evidence, policy context, policy recommendation, and steward learning context. Treat match_score as secondary context only.
2. Explain the decision in steward-friendly language.
3. Identify the strongest positive evidence and the most important risk or weak evidence.
4. Recommend exactly one action: AUTO_MERGE, APPROVE_MERGE, REVIEW_REQUIRED, or BLOCK_MERGE.
5. Keep ai_decision and recommended_action aligned unless policy or evidence clearly requires separation.
6. Follow policy recommendation by default unless record-level evidence strongly contradicts it.
7. Prefer REVIEW_REQUIRED when evidence is mixed, incomplete, or policy-sensitive.
8. Prefer BLOCK_MERGE when evidence conflicts on critical identity attributes or risk is high.
9. Prefer AUTO_MERGE only when evidence is consistently strong, low risk, and policy allows automation.
10. Return ONLY valid JSON.
11. Missing evidence should be treated as unavailable or incomplete evidence, not as conflicting evidence.
12. Do not treat absent identifiers as mismatches unless conflicting values are present.
13. For PRODUCT, only treat GTIN_MATCH as positive evidence when Record A gtin and Record B gtin are identical.
14. If PRODUCT gtin values differ, do not cite GTIN_MATCH as a valid matching rule even if it appears in triggered_rules.
15. If PRODUCT gtin differs but SKU, product name, or product ID are similar, describe those as supporting but non-deterministic evidence.
16. For PATIENT, use patient_id, patient_first_name, patient_last_name, patient_dob, patient_email, phone_number, patient_address, and source_system as evidence.
17. For PATIENT, do not say DOB, name, email, or address are missing when patient_* fields are present.
18. For PATIENT, if patient_id base value matches but suffix differs, describe it as strong but not exact identifier evidence.
19. For SUPPLIER, use supplier_id, tax_id, supplier_name, contact_email, supplier_address, and source_system as evidence.
20. For SUPPLIER, do not say email, name, or address are missing when contact_email, supplier_name, or supplier_address are present.
21. For SUPPLIER, treat same-domain contact emails as supporting but non-deterministic evidence unless the full email matches exactly.
22. For PROVIDER, conflicting NPI values are high-risk deterministic evidence and must not be minimized.
23. For PROVIDER, conflicting provider_id values reduce merge confidence unless there is a clear same-root identifier pattern supported by other strong evidence.
24. For PROVIDER, conflicting specialty or practice address should be described as cautionary evidence.
25. For PROVIDER, same email domain or same last name alone is not enough to call the records strong merge candidates.
26. For PROVIDER, if provider_id and npi are both missing, treat the records as weak evidence for merge even if other attributes align.
27. For PROVIDER, treat an exact normalized phone-number match as supporting evidence, not a primary deterministic identifier.
28. For PROVIDER, a missing phone number is unavailable evidence, not a conflict.
29. For PROVIDER, a phone-number mismatch should reduce confidence modestly but must not outweigh matching NPI or Provider ID by itself.
30. For PATIENT, treat an exact normalized phone-number match as supporting evidence, not a primary deterministic identifier.
31. For PATIENT, a missing phone number is unavailable evidence, not a conflict.
32. For CUSTOMER, use member_id, full_name, dob, email, phone_number, address, and source_system as evidence.
33. For CUSTOMER, treat an exact normalized phone-number match as supporting evidence, not a primary deterministic identifier.
34. For CUSTOMER, a missing phone number is unavailable evidence, not a conflict.
35. For BANKING, use account_id, banking_customer_id, sap_business_partner_id, routing_number, account_last_four, account_type, currency, institution_name, customer_name, email, phone_number, address, and source_system as evidence.
36. For BANKING, conflicting account_id, customer_id, or sap_business_partner_id values are significant identity evidence and must not be minimized.
37. For BANKING, routing_number identifies the financial institution and is not a unique account or customer identifier.
38. For BANKING, account_last_four is supporting evidence only and must never independently justify AUTO_MERGE.
39. For BANKING, matching routing_number and account_last_four together may strengthen evidence but are not sufficient by themselves to prove entity identity.
40. For BANKING, account_type, currency, institution_name, email, phone_number, address, and name are supporting evidence and must not override conflicting authoritative identifiers.
41. For BANKING, missing banking identifiers are unavailable evidence, not conflicting evidence.
42. For BANKING, prefer REVIEW_REQUIRED when authoritative banking identifiers are incomplete and identity depends primarily on non-unique attributes.
43. For LOCATION, use location_id, site_id, location_code, location_name, location_type, physical address, parent_location_id, organization_entity_id, and source_system as evidence.
44. For LOCATION, conflicting authoritative location_id, site_id, or location_code values are significant identity evidence and must not be minimized.
45. For LOCATION, an exact physical-address match is supporting evidence only; multiple departments, suites, operational locations, or sites may share the same address.
46. For LOCATION, parent_location_id is hierarchy evidence. Do not infer, invent, or require a child_location_id; child relationships are derived from records that reference the parent.
47. For LOCATION, matching parent_location_id or organization_entity_id strengthens structural context but does not independently prove two Locations are the same entity.
48. For LOCATION, missing location identifiers or hierarchy values are unavailable evidence, not conflicting evidence.
49. For LOCATION, prefer REVIEW_REQUIRED when identity depends mainly on name/address similarity without an aligned authoritative location identifier.
50. For ORGANIZATION, use organization_entity_id, organization_code, organization_name, organization_type, parent_organization_id, organization_status, related location evidence, and source_system as evidence.
51. For ORGANIZATION, organization_entity_id is mastered entity evidence and is distinct from the internal tenant organization_id; never expose, compare, or infer the tenant identifier as business entity evidence.
52. For ORGANIZATION, conflicting authoritative organization_entity_id or organization_code values are significant identity evidence and must not be minimized.
53. For ORGANIZATION, organization-name similarity alone is supporting evidence and must never independently justify AUTO_MERGE.
54. For ORGANIZATION, parent_organization_id is hierarchy evidence and must not be treated as proof that two child organizations are the same entity.
55. For ORGANIZATION, organization_type and organization_status are supporting governance evidence; conflicts should increase caution but must not override aligned authoritative identifiers by themselves.
56. For ORGANIZATION, missing organization identifiers or hierarchy values are unavailable evidence, not conflicting evidence.
57. For ORGANIZATION, prefer REVIEW_REQUIRED when authoritative organization identifiers are incomplete and identity depends primarily on name, type, hierarchy, or location similarity.
58. For ORGANIZATION, if Record A parent_organization_id exactly equals Record B organization_entity_id, or Record B parent_organization_id exactly equals Record A organization_entity_id, treat this as deterministic parent-child hierarchy evidence.

59. For ORGANIZATION, when that parent-child relationship is established, the records represent distinct organization hierarchy nodes and must not be described as possible duplicates requiring review to determine whether they are the same organization.

60. For ORGANIZATION, a confirmed parent-child relationship must support BLOCK_MERGE because merging the records would collapse the organization hierarchy and corrupt mastered entity identity.

61. For ORGANIZATION, when a parent-child relationship is established, steward review may validate the governance action, but must not be described as necessary to determine whether the records are distinct entities.
Decision calibration:
- AUTO_MERGE: deterministic identifiers or highly aligned evidence with low risk and no conflicting identity attributes.
- APPROVE_MERGE: strong evidence, but steward approval is still appropriate.
- REVIEW_REQUIRED: mixed evidence, missing evidence, elevated risk, or governance sensitivity.
- BLOCK_MERGE: conflicting critical evidence, high risk, or policy explicitly blocks merge.

Rule analysis selection:
Select up to 3 rule_analysis items using this priority:
1. The most decision-critical deterministic identifier or identity rule.
2. The strongest positive evidence rule.
3. The highest-risk or cautionary rule.
4. Address, source-system, provenance, or stewardship learning rule if materially relevant.

Avoid redundant rule_analysis items. Each reason should explain why the rule matters to the merge decision.

Domain:
{_fmt(domain)}

{record_evidence}
Preliminary upstream match score secondary context only:
{req.match_score}

Triggered rules:
{", ".join(req.triggered_rules) if req.triggered_rules else "None"}

Policy recommendation:
- recommendation: {recommendation_text}
- recommendation_reason: {recommendation_reason}
- risk_band: {risk_band}
- highest_risk_level: {highest_risk_level}
- recommended_actions_seen: {recommended_actions_seen}

Signal evidence:
{json.dumps(prompt_signal_packets, indent=2)}
""".strip()

    if policy_context:
        prompt += f"""


Effective policy context:
{policy_context}
"""

    if learning_context:
        prompt += f"""

Historical steward learning context:
{learning_context}

How to use steward learning:
- Treat historical steward behavior as a supporting signal, not a hard rule.
- If similar cases were often overridden, reduce automation confidence.
- If similar cases were commonly reviewed, bias toward REVIEW_REQUIRED.
- If current evidence is stronger than historical override patterns, explain that clearly.
- Current policy guidance takes priority over historical behavior.
"""

    prompt += """

Return ONLY valid JSON in exactly this structure:
{
  "ai_decision": "AUTO_MERGE",
  "confidence": 0.0,
  "risk_flag": "LOW",
  "recommended_action": "AUTO_MERGE",
  "explanation_summary": "short steward-friendly explanation",
  "rule_analysis": [
    {
      "rule": "RULE_NAME",
      "impact": "HIGH",
      "reason": "brief explanation"
    }
  ]
}

Output rules:
- ai_decision must be one of AUTO_MERGE, APPROVE_MERGE, REVIEW_REQUIRED, BLOCK_MERGE
- recommended_action must be one of AUTO_MERGE, APPROVE_MERGE, REVIEW_REQUIRED, BLOCK_MERGE
- confidence must be a number between 0 and 1
- risk_flag must be LOW, MEDIUM, or HIGH
- explanation_summary must be under 45 words
- rule_analysis must contain 1 to 3 items
- each rule_analysis item must include rule, impact, and reason
- impact must be HIGH, MEDIUM, or LOW
- each reason must be under 18 words
- do not include apostrophes inside JSON string values if avoidable
- do not include line breaks inside string values
- do not include markdown
- do not include code fences
- do not include any keys other than the keys shown above
- output raw JSON only
""".strip()

    return prompt
