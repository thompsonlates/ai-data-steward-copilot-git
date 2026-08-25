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
        "BANKING": [ "account_id","banking_customer_id", "sap_business_partner_id",
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
   SQL or regex when the supplied evidence supports it.
   Keep SQL short, diagnostic, and non-destructive.

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

- suggested_sql must be null or a short diagnostic
  SELECT statement no longer than 500 characters.

- Do not generate multi-step SQL scripts, long CASE
  expressions, CTE chains, DDL, DML, or procedural SQL.

- Prefer recommended_remediation and reasoning_summary
  over long SQL examples.

- If remediation requires authoritative-source lookup,
  business interpretation, or steward judgment,
  suggested_sql must be null.

- suggested_regex must be concise and only returned
  when regex is clearly appropriate.

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
- Do not manufacture additional findings.
- suggested_sql must be null when SQL is not appropriate.
- suggested_regex must be null when regex is not appropriate.
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
            "Do not treat duplicate Product IDs as "
            "automatically safe to merge. Duplicate "
            "identifiers require comparison of supporting "
            "product attributes before remediation."
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