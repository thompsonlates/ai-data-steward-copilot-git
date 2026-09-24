"""AI Data Quality Rule Suggestions service.

Place at: app/services/quality_intelligence_service.py
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from typing import Any, Dict, List, Optional
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)

DIMENSIONS = {
    "COMPLETENESS", "VALIDITY", "UNIQUENESS", "CONSISTENCY",
    "STANDARDIZATION", "TIMELINESS", "INTEGRITY", "ACCURACY",
}
LEVELS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


def _require_organization_id(
    organization_id: str,
) -> str:
    normalized = str(organization_id or "").strip()

    if not normalized:
        raise ValueError(
            "organization_id is required for tenant-isolated "
            "quality intelligence operations."
        )

    if not normalized.startswith("org_"):
        raise ValueError(
            "organization_id must use the org_ identifier standard."
        )

    return normalized


def _validate_tenant_context(
    *,
    organization_id: str,
    row: Dict[str, Any],
    steward_context: Optional[Dict[str, Any]],
) -> str:
    effective_organization_id = _require_organization_id(
        organization_id
    )

    row_organization_id = _text(
        row.get("organization_id")
    )

    if (
        row_organization_id
        and row_organization_id != effective_organization_id
    ):
        raise ValueError(
            "DQ dashboard row does not belong to the "
            "authenticated organization."
        )

    if steward_context:
        steward_organization_id = _text(
            steward_context.get("organization_id")
        )

        if (
            steward_organization_id
            and steward_organization_id
            != effective_organization_id
        ):
            raise ValueError(
                "Steward metrics do not belong to the "
                "authenticated organization."
            )

    return effective_organization_id


def _float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        number = float(value)
        return default if math.isnan(number) or math.isinf(number) else number
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    number = _float(value)
    return default if number is None else int(round(number))


def _score100(value: Any) -> Optional[float]:
    number = _float(value)
    if number is None:
        return None
    if 0 <= number <= 1:
        number *= 100
    return round(max(0.0, min(100.0, number)), 2)


def _confidence(value: Any, default: float = 0.80) -> float:
    number = _float(value)
    if number is None:
        return default
    if number > 1:
        number /= 100
    return round(max(0.0, min(1.0, number)), 4)


def _text(value: Any, default: str = "") -> str:
    return default if value is None else str(value).strip()


def _items(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    return [] if value is None else [value]


def _identifier(value: str, fallback: str = "source_table") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value or "").strip("_")
    return cleaned or fallback


def _triggered_rule_counts(
    value: Any,
) -> Dict[str, int]:
    """
    Normalize rule-level evidence into {RULE_ID: count}.

    Supports:
      - [{"rule_id": "...", "count": 12}]
      - [{"rule": "...", "finding_count": 12}]
      - {"RULE_A": 12, "RULE_B": 4}
      - ["RULE_A", "RULE_B"]
      - "RULE_A,RULE_B"

    Match/Explain evidence rules such as GTIN_MATCH or UOM_MATCH are
    retained as context, but they are not interpreted as DQ defects by
    _derive_opportunities().
    """

    counts: Dict[str, int] = {}

    def add_rule(
        raw_rule_id: Any,
        raw_count: Any = 1,
    ) -> None:
        rule_id = _text(
            raw_rule_id
        ).upper()

        if not rule_id:
            return

        count = max(
            0,
            _int(
                raw_count,
                1,
            ),
        )

        counts[rule_id] = (
            counts.get(rule_id, 0)
            + count
        )

    if value is None:
        return counts

    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, dict):
                add_rule(
                    item.get("rule_id")
                    or item.get("rule")
                    or item.get("name")
                    or key,
                    item.get("count")
                    or item.get("finding_count")
                    or item.get("trigger_count")
                    or item.get("affected_records")
                    or 1,
                )
            else:
                add_rule(
                    key,
                    item,
                )

        return counts

    if isinstance(value, str):
        for part in value.split(","):
            add_rule(part, 1)

        return counts

    for item in _items(value):
        if isinstance(item, dict):
            add_rule(
                item.get("rule_id")
                or item.get("rule")
                or item.get("name"),
                item.get("count")
                or item.get("finding_count")
                or item.get("trigger_count")
                or item.get("affected_records")
                or 1,
            )
        else:
            add_rule(
                item,
                1,
            )

    return counts


def build_quality_rule_context(
    row: Dict[str, Any],
    *,
    organization_id: str,
    steward_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Normalize tenant-scoped DQ dashboard metrics into a stable AI context."""
    effective_organization_id = _validate_tenant_context(
        organization_id=organization_id,
        row=row,
        steward_context=steward_context,
    )

    steward = steward_context or {}
    context = {
        "organization_id": effective_organization_id,
        "domain": _text(row.get("domain"), "GENERAL").upper(),
        "dataset_id": _text(row.get("dataset_id")),
        "dataset_name": _text(
            row.get("dataset_name") or row.get("table_name") or row.get("asset_name"),
            "Current governed dataset",
        ),
        # Preserve canonical -> physical source mappings established during profiling.
        "column_mappings": (
            row.get("column_mappings")
            if isinstance(row.get("column_mappings"), list)
            else []
),
        "metric_date": row.get("metric_date"),
        "dq_health_score": _score100(row.get("dq_health_score")),
        "dq_risk_score": _score100(row.get("dq_risk_score")),
        "automation_readiness_score": _score100(row.get("automation_readiness_score")),
        "total_records": _int(row.get("total_records")),
        "records_with_findings": _int(row.get("records_with_findings")),
        "impacted_record_rate": _float(row.get("impacted_record_rate")),
        "duplicate_record_count": _int(
            row.get("duplicate_record_count") or row.get("duplicate_records")
        ),
        "duplicate_rate": _float(row.get("duplicate_rate")),
        "records_below_threshold": _int(row.get("records_below_threshold")),
        "total_findings": _int(row.get("total_findings")),
        "open_findings_count": _int(
            row.get("open_findings_count") or row.get("open_findings")
        ),
        "critical_findings": _int(row.get("critical_findings")),
        "high_findings": _int(row.get("high_findings")),
        "medium_findings": _int(row.get("medium_findings")),
        "low_findings": _int(row.get("low_findings")),
        "configured_rule_count": _int(row.get("configured_rule_count")),
        "active_rule_count": _int(row.get("active_rule_count")),
        "rules_triggered": _int(row.get("rules_triggered")),
        "failed_rule_count": _int(row.get("failed_rule_count")),
        "avg_rule_weight": _float(row.get("avg_rule_weight")),
        "avg_completeness_score": _score100(row.get("avg_completeness_score")),
        "avg_validity_score": _score100(row.get("avg_validity_score")),
        "avg_standardization_score": _score100(row.get("avg_standardization_score")),
        "avg_consistency_score": _score100(row.get("avg_consistency_score")),
        "avg_uniqueness_score": _score100(row.get("avg_uniqueness_score")),
        "steward_actions_taken": _int(row.get("steward_actions_taken")),
        "ai_recommendations_generated": _int(row.get("ai_recommendations_generated")),
        "accepted_ai_recommendations": _int(row.get("accepted_ai_recommendations")),
        "implemented_ai_recommendations": _int(row.get("implemented_ai_recommendations")),
        "avg_ai_recommendation_confidence": _confidence(
            row.get("avg_ai_recommendation_confidence"), 0.0
        ),
        "ai_recommendation_implementation_rate": _float(
            row.get("ai_recommendation_implementation_rate")
        ),
        "recent_override_rate": _float(steward.get("override_rate")),
        "recent_false_positives": _int(steward.get("false_positives")),
        "recent_false_negatives": _int(steward.get("false_negatives")),
        "top_override_reasons": _items(steward.get("top_override_reasons")),
        "top_triggered_rules": _items(row.get("top_triggered_rules")),
        "triggered_rule_counts": _triggered_rule_counts(
            row.get("top_triggered_rules")
        ),
    }
    context["deterministic_opportunities"] = _derive_opportunities(context)
    context["evidence_summary"] = _evidence(context)
    return context


def _derive_opportunities(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    opportunities: List[Dict[str, Any]] = []

    def add(
        key: str,
        dimension: str,
        severity: str,
        reason: str,
        score: float,
        *,
        domain_priority: int = 0,
    ) -> None:
        opportunities.append({
            "key": key,
            "dimension": dimension,
            "severity": severity,
            "reason": reason,
            "opportunity_score": round(
                max(
                    0,
                    min(
                        100,
                        score,
                    ),
                ),
                1,
            ),
            "domain_priority": int(
                domain_priority
            ),
        })

    if context["configured_rule_count"] == 0:
        add("BASELINE_RULE_COVERAGE", "INTEGRITY", "HIGH", "No configured DQ rules were detected.", 96)
    elif context["active_rule_count"] == 0:
        add("INACTIVE_RULE_COVERAGE", "INTEGRITY", "HIGH", "DQ rules exist but none are active.", 92)

    if context["duplicate_record_count"] > 0:
        add(
            "DUPLICATE_PREVENTION", "UNIQUENESS", "HIGH",
            f"{context['duplicate_record_count']:,} duplicate records were detected.", 90,
        )
    if context["records_below_threshold"] > 0:
        add(
            "MINIMUM_QUALITY_GATE", "INTEGRITY", "HIGH",
            f"{context['records_below_threshold']:,} records are below the accepted DQ threshold.", 88,
        )

    # ---------------------------------------------------------
    # PRODUCT-specific deterministic rule recognition
    # ---------------------------------------------------------
    #
    # These are DQ findings, not Match Explain evidence rules.
    # Match rules such as GTIN_MATCH, SKU_MATCH,
    # PRODUCT_NAME_SIMILAR, DESCRIPTION_SIMILAR, and UOM_MATCH
    # remain useful context but are not defects by themselves.

    if context.get("domain") == "PRODUCT":
        rule_counts = (
            context.get(
                "triggered_rule_counts"
            )
            or {}
        )

        product_rule_specs = (
            (
                "GTIN_CHECKSUM_VALIDITY",
                "GTIN_CHECKSUM_CONTROL",
                "VALIDITY",
                "HIGH",
                (
                    "GTIN values fail GS1 check-digit "
                    "validation and require authoritative "
                    "source confirmation before correction."
                ),
                99,
                50,
            ),
            (
                "PRODUCT_VARIANT_FORMAT_VALIDITY",
                "PRODUCT_VARIANT_FORMAT_CONTROL",
                "VALIDITY",
                "HIGH",
                (
                    "product_variant values fail the governed "
                    "quantity + UOM structural format."
                ),
                97,
                45,
            ),
            (
                "PRODUCT_VARIANT_UOM_ALLOWED_VALUE",
                "PRODUCT_VARIANT_UOM_POLICY",
                "VALIDITY",
                "HIGH",
                (
                    "product_variant values use UOM tokens "
                    "outside the approved governed vocabulary."
                ),
                96,
                44,
            ),
            (
                "GTIN_FORMAT_VALIDITY",
                "GTIN_FORMAT_CONTROL",
                "VALIDITY",
                "HIGH",
                (
                    "GTIN values fail the governed numeric "
                    "length/format requirement."
                ),
                95,
                42,
            ),
            (
                "PRODUCT_VARIANT_STANDARDIZATION",
                "PRODUCT_VARIANT_STANDARDIZATION",
                "STANDARDIZATION",
                "MEDIUM",
                (
                    "product_variant values require safe "
                    "canonical formatting such as trimming, "
                    "uppercasing, or separator normalization."
                ),
                90,
                30,
            ),
        )

        for (
            rule_id,
            opportunity_key,
            dimension,
            severity,
            reason,
            score,
            domain_priority,
        ) in product_rule_specs:
            count = _int(
                rule_counts.get(
                    rule_id
                ),
                0,
            )

            if count <= 0:
                continue

            add(
                opportunity_key,
                dimension,
                severity,
                (
                    f"{count:,} finding"
                    f"{'' if count == 1 else 's'}: "
                    f"{reason}"
                ),
                score,
                domain_priority=(
                    domain_priority
                ),
            )

    score_rules = [
        ("avg_completeness_score", "COMPLETENESS", "REQUIRED_FIELD_COMPLETENESS"),
        ("avg_validity_score", "VALIDITY", "FORMAT_AND_DOMAIN_VALIDATION"),
        ("avg_standardization_score", "STANDARDIZATION", "STANDARDIZATION_ENFORCEMENT"),
        ("avg_consistency_score", "CONSISTENCY", "CROSS_FIELD_CONSISTENCY"),
        ("avg_uniqueness_score", "UNIQUENESS", "IDENTITY_UNIQUENESS"),
    ]
    for field, dimension, key in score_rules:
        score = context.get(field)
        if score is None or score >= 95:
            continue
        severity = "CRITICAL" if score < 80 else "HIGH" if score < 90 else "MEDIUM"
        add(key, dimension, severity, f"{dimension.title()} score is {score:.1f}/100.", 100 - score)

    severe = context["critical_findings"] + context["high_findings"]
    if severe > 0:
        add(
            "SEVERITY_BASED_BLOCKING", "INTEGRITY", "CRITICAL",
            f"{context['critical_findings']} critical and {context['high_findings']} high findings remain.", 97,
        )
    if context["open_findings_count"] > 0:
        add(
            "OPEN_FINDING_AGING", "TIMELINESS", "HIGH",
            f"{context['open_findings_count']:,} findings remain open.", 82,
        )

    override_rate = context.get("recent_override_rate")
    if override_rate is not None:
        pct = override_rate * 100 if override_rate <= 1 else override_rate
        if pct >= 20:
            add(
                "STEWARD_OVERRIDE_PATTERN", "ACCURACY",
                "HIGH" if pct >= 35 else "MEDIUM",
                f"Recent steward override rate is {pct:.1f}%.", min(100, 55 + pct),
            )

    if not opportunities and context["configured_rule_count"] < 5:
        add(
            "PREVENTIVE_RULE_EXPANSION", "INTEGRITY", "MEDIUM",
            "Current DQ posture is healthy, but preventive rule coverage is limited.", 58,
        )

    rank = {
        "CRITICAL": 4,
        "HIGH": 3,
        "MEDIUM": 2,
        "LOW": 1,
    }

    return sorted(
        opportunities,
        key=lambda x: (
            rank[
                x["severity"]
            ],
            int(
                x.get(
                    "domain_priority",
                    0,
                )
            ),
            x[
                "opportunity_score"
            ],
        ),
        reverse=True,
    )


def _evidence(context: Dict[str, Any]) -> List[str]:
    pairs = [
        ("DQ health score", context.get("dq_health_score"), "/100"),
        ("DQ risk score", context.get("dq_risk_score"), "/100"),
        ("Automation readiness", context.get("automation_readiness_score"), "/100"),
        ("Configured rules", context.get("configured_rule_count"), ""),
        ("Active rules", context.get("active_rule_count"), ""),
        ("Failed rules", context.get("failed_rule_count"), ""),
        ("Duplicate records", context.get("duplicate_record_count"), ""),
        ("Records below threshold", context.get("records_below_threshold"), ""),
        ("Open findings", context.get("open_findings_count"), ""),
        ("Critical findings", context.get("critical_findings"), ""),
        ("High findings", context.get("high_findings"), ""),
    ]
    evidence = [f"{label}: {value}{suffix}" for label, value, suffix in pairs if value is not None]
    for field, label in [
        ("avg_completeness_score", "Completeness"),
        ("avg_validity_score", "Validity"),
        ("avg_standardization_score", "Standardization"),
        ("avg_consistency_score", "Consistency"),
        ("avg_uniqueness_score", "Uniqueness"),
    ]:
        if context.get(field) is not None:
            evidence.append(
                f"{label}: "
                f"{context[field]:.1f}/100"
            )

    triggered_rule_counts = (
        context.get(
            "triggered_rule_counts"
        )
        or {}
    )

    if triggered_rule_counts:
        ranked_rules = sorted(
            triggered_rule_counts.items(),
            key=lambda item: (
                item[1],
                item[0],
            ),
            reverse=True,
        )[:10]

        evidence.append(
            "Triggered rule evidence: "
            + ", ".join(
                (
                    f"{rule_id}="
                    f"{count}"
                )
                for rule_id, count
                in ranked_rules
            )
        )

    return evidence


def build_quality_rule_prompt(context: Dict[str, Any]) -> str:
    prompt_context = dict(context)
    prompt_context.pop("organization_id", None)

    return f"""
You are the AI Data Quality Rule Engineering Assistant inside an enterprise
AI Data Steward Copilot platform.

Analyze the supplied DQ metrics and propose 1-5 implementation-ready data
quality rules. Recommendations must be evidence-based, explainable,
domain-aware, and safe for enterprise use.

RULES:
- Return valid JSON only; no Markdown fences.
- Never invent observed fields, record counts, source systems, or failures.
- Distinguish current defects from preventive controls.
- Zero means no observed issue; null means unavailable.
- SQL is illustrative and must use placeholders when field names are unknown.
- Confidence values must be 0-1.

Return exactly:
{{
  "headline": "string",
  "summary": "string",
  "priority": "LOW|MEDIUM|HIGH|CRITICAL",
  "rule_strategy": "PREVENTIVE|REMEDIATION|MIXED",
  "suggested_rules": [
    {{
      "rule_id": "UPPERCASE_IDENTIFIER",
      "rule_name": "string",
      "dimension": "COMPLETENESS|VALIDITY|UNIQUENESS|CONSISTENCY|STANDARDIZATION|TIMELINESS|INTEGRITY|ACCURACY",
      "severity": "LOW|MEDIUM|HIGH|CRITICAL",
      "rule_type": "SQL|REGEX|REFERENCE_LOOKUP|THRESHOLD|UNIQUENESS|CROSS_FIELD|REQUIRED_FIELD",
      "description": "string",
      "rationale": "string",
      "business_risk": "string",
      "rule_logic": "string",
      "sql_example": "string or null",
      "implementation_target": "BIGQUERY|SNOWFLAKE|DATABRICKS|INFORMATICA|COLLIBRA|GENERIC",
      "expected_impact": "string",
      "estimated_automation_gain_points": 0,
      "supporting_evidence": ["string"],
      "confidence": 0.0
    }}
  ],
  "recommended_sequence": ["rule_id"],
  "supporting_evidence": ["string"],
  "confidence": 0.0
}}

DQ CONTEXT:
{json.dumps(prompt_context, default=str, indent=2)}
""".strip()


def _extract_quality_json(
    raw: object,
) -> dict:
    """
    Parse structured DQ recommendation JSON from an LLM response.

    Tolerates:
      - already-parsed dicts
      - markdown ```json fences
      - explanatory text before/after JSON
      - trailing commas before } or ]
      - multiple candidate JSON objects

    Does NOT invent or auto-complete genuinely truncated JSON.
    """

    if isinstance(raw, dict):
        return raw

    if raw is None:
        raise ValueError(
            "LLM returned no content."
        )

    text = str(raw).strip()

    if not text:
        raise ValueError(
            "LLM returned empty content."
        )

    # ---------------------------------------------------------
    # 1. Remove common Markdown code fences
    # ---------------------------------------------------------

    text = re.sub(
        r"^\s*```(?:json)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\s*```\s*$",
        "",
        text,
    )

    text = text.strip()

    # ---------------------------------------------------------
    # 2. Try exact response first
    # ---------------------------------------------------------

    try:
        value = json.loads(text)

        if not isinstance(value, dict):
            raise ValueError(
                "LLM JSON root must be an object."
            )

        return value

    except json.JSONDecodeError:
        pass

    # ---------------------------------------------------------
    # 3. Conservative cleanup:
    #    trailing commas before closing braces/brackets
    # ---------------------------------------------------------

    cleaned_text = re.sub(
        r",\s*([}\]])",
        r"\1",
        text,
    )

    if cleaned_text != text:
        try:
            value = json.loads(
                cleaned_text
            )

            if isinstance(
                value,
                dict,
            ):
                return value

        except json.JSONDecodeError:
            pass

    # ---------------------------------------------------------
    # 4. Extract balanced JSON candidates.
    #
    #    Important:
    #    Do this string-aware so braces inside strings
    #    do not break parsing.
    # ---------------------------------------------------------

    def _balanced_candidates(
        source: str,
    ) -> list[str]:
        candidates: list[str] = []

        for start_index, char in enumerate(
            source
        ):
            if char not in "{[":
                continue

            opening = char
            closing = (
                "}"
                if opening == "{"
                else "]"
            )

            depth = 0
            in_string = False
            escaped = False

            for index in range(
                start_index,
                len(source),
            ):
                current = source[index]

                if in_string:
                    if escaped:
                        escaped = False
                        continue

                    if current == "\\":
                        escaped = True
                        continue

                    if current == '"':
                        in_string = False

                    continue

                if current == '"':
                    in_string = True
                    continue

                if current == opening:
                    depth += 1

                elif current == closing:
                    depth -= 1

                    if depth == 0:
                        candidates.append(
                            source[
                                start_index:
                                index + 1
                            ]
                        )
                        break

        return candidates

    candidates = (
        _balanced_candidates(
            cleaned_text
        )
    )

    # Prefer larger candidates first because the complete
    # response object will usually contain nested objects.
    candidates.sort(
        key=len,
        reverse=True,
    )

    last_error: Exception | None = None

    for candidate in candidates:
        candidate = re.sub(
            r",\s*([}\]])",
            r"\1",
            candidate,
        )

        try:
            value: Any = json.loads(
                candidate
            )

            if isinstance(
                value,
                dict,
            ):
                return value

        except json.JSONDecodeError as exc:
            last_error = exc

    # ---------------------------------------------------------
    # 5. Detect likely truncation explicitly.
    #
    #    We deliberately do not fabricate closing quotes,
    #    braces, fields, or recommendation content.
    # ---------------------------------------------------------

    open_braces = (
        cleaned_text.count("{")
        - cleaned_text.count("}")
    )

    open_brackets = (
        cleaned_text.count("[")
        - cleaned_text.count("]")
    )

    quote_count = len(
        re.findall(
            r'(?<!\\)"',
            cleaned_text,
        )
    )

    likely_truncated = (
        open_braces > 0
        or open_brackets > 0
        or quote_count % 2 != 0
    )

    preview = (
        cleaned_text[:500]
        .replace("\n", " ")
    )

    if likely_truncated:
        raise ValueError(
            "LLM returned truncated or incomplete "
            "JSON. Deterministic fallback should be "
            f"used. Response preview: {preview}"
        )

    if last_error:
        raise ValueError(
            "Unable to parse structured DQ JSON "
            f"from LLM response: {last_error}. "
            f"Response preview: {preview}"
        )

    raise ValueError(
        "No valid JSON object found in LLM "
        f"response. Response preview: {preview}"
    )


def _validate_quality_rule_suggestions(payload: Dict[str, Any]) -> Dict[str, Any]:
    priority = _text(payload.get("priority"), "MEDIUM").upper()
    priority = priority if priority in LEVELS else "MEDIUM"
    strategy = _text(payload.get("rule_strategy"), "MIXED").upper()
    strategy = strategy if strategy in {"PREVENTIVE", "REMEDIATION", "MIXED"} else "MIXED"
    rules: List[Dict[str, Any]] = []

    for index, raw in enumerate(_items(payload.get("suggested_rules"))):
        if not isinstance(raw, dict):
            continue
        dimension = _text(raw.get("dimension"), "INTEGRITY").upper()
        severity = _text(raw.get("severity"), "MEDIUM").upper()
        dimension = dimension if dimension in DIMENSIONS else "INTEGRITY"
        severity = severity if severity in LEVELS else "MEDIUM"
        rule_id = re.sub(
            r"[^A-Z0-9_]", "_",
            _text(raw.get("rule_id"), f"AI_DQ_RULE_{index + 1:02d}").upper(),
        )
        sql_example = raw.get("sql_example")
        rules.append({
            "rule_id": rule_id,
            "rule_name": _text(raw.get("rule_name"), f"Suggested DQ Rule {index + 1}"),
            "dimension": dimension,
            "severity": severity,
            "rule_type": _text(raw.get("rule_type"), "THRESHOLD").upper(),
            "description": _text(raw.get("description")),
            "rationale": _text(raw.get("rationale")),
            "business_risk": _text(raw.get("business_risk")),
            "rule_logic": _text(raw.get("rule_logic")),
            "sql_example": _text(sql_example) if sql_example else None,
            "implementation_target": _text(raw.get("implementation_target"), "GENERIC").upper(),
            "field_name": _text(raw.get("field_name")) or None,
            "source_column": _text(raw.get("source_column")) or None,
            "expected_impact": _text(raw.get("expected_impact")),
            "estimated_automation_gain_points": max(0, min(25, _int(raw.get("estimated_automation_gain_points")))),
            "supporting_evidence": [_text(x) for x in _items(raw.get("supporting_evidence")) if _text(x)],
            "confidence": _confidence(raw.get("confidence"), 0.75),
        })

    return {
        "headline": _text(payload.get("headline"), "AI data quality rule opportunities"),
        "summary": _text(payload.get("summary"), "AI identified opportunities to strengthen DQ controls."),
        "priority": priority,
        "rule_strategy": strategy,
        "suggested_rules": rules[:5],
        "recommended_sequence": [_text(x) for x in _items(payload.get("recommended_sequence")) if _text(x)],
        "supporting_evidence": [_text(x) for x in _items(payload.get("supporting_evidence")) if _text(x)],
        "confidence": _confidence(payload.get("confidence"), 0.80),
    }


def generate_structured_quality_rule_suggestions(
    prompt: str,
    *,
    organization_id: str,
    provider: Optional[str] = None,
) -> Dict[str, Any]:
    provider_name = (
        provider
        or os.getenv("QUALITY_INTELLIGENCE_LLM_PROVIDER")
        or "claude"
    )

    raw = LLMService(
        provider=provider_name,
    ).ask(
        prompt,
        organization_id=organization_id,
    )

    logger.debug(
        "Quality rule suggestion response received."
    )

    return _validate_quality_rule_suggestions(
        _extract_quality_json(raw)
    )


def _rule(
    *,
    rule_id: str,
    name: str,
    dimension: str,
    severity: str,
    rule_type: str,
    description: str,
    rationale: str,
    risk: str,
    logic: str,
    sql: Optional[str],
    impact: str,
    gain: int,
    evidence: str,
    confidence: float,
    field_name: Optional[str] = None,
    source_column: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "rule_id": rule_id,
        "rule_name": name,
        "dimension": dimension,
        "severity": severity,
        "rule_type": rule_type,
        "description": description,
        "rationale": rationale,
        "business_risk": risk,
        "rule_logic": logic,
        "sql_example": sql,
        "implementation_target": "GENERIC",
        "field_name": field_name,
        "source_column": source_column,
        "expected_impact": impact,
        "estimated_automation_gain_points": gain,
        "supporting_evidence": [evidence],
        "confidence": confidence,
    }


def _baseline_rules(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    table = _identifier(context["dataset_name"])
    return [
        _rule(
            rule_id="REQUIRED_BUSINESS_KEY",
            name="Required Business Key",
            dimension="COMPLETENESS",
            severity="HIGH",
            rule_type="REQUIRED_FIELD",
            description="Require a primary business identifier for every active record.",
            rationale="A stable key is foundational for matching, lineage, and deduplication.",
            risk="Missing identifiers create duplicate entities and orphaned relationships.",
            logic="business_key IS NOT NULL AND TRIM(business_key) <> ''",
            sql=f"SELECT * FROM `{table}` WHERE business_key IS NULL OR TRIM(business_key) = ''",
            impact="Prevents unidentified records from entering trusted workflows.",
            gain=3,
            evidence="No configured DQ rules were detected.",
            confidence=0.90,
        ),
        _rule(
            rule_id="ACTIVE_RECORD_UNIQUENESS",
            name="Active Record Business-Key Uniqueness",
            dimension="UNIQUENESS",
            severity="HIGH",
            rule_type="UNIQUENESS",
            description="Require one active record per business key.",
            rationale="Preventive uniqueness controls reduce duplicate creation and steward review.",
            risk="Duplicate active records create conflicting golden records.",
            logic="COUNT(active records grouped by business_key) <= 1",
            sql=f"SELECT business_key, COUNT(*) c FROM `{table}` WHERE active_flag = TRUE GROUP BY business_key HAVING c > 1",
            impact="Reduces duplicate identity pressure and reconciliation.",
            gain=4,
            evidence="Preventive uniqueness coverage is recommended.",
            confidence=0.86,
        ),
        _rule(
            rule_id="SOURCE_TIMESTAMP_VALIDITY",
            name="Source Timestamp Validity",
            dimension="TIMELINESS",
            severity="MEDIUM",
            rule_type="CROSS_FIELD",
            description="Require source and update timestamps to be present and logically ordered.",
            rationale="Reliable timestamps support recency, lineage, and auditability.",
            risk="Invalid timestamps create stale data and broken audit trails.",
            logic="source_created_at IS NOT NULL AND updated_at >= source_created_at",
            sql=f"SELECT * FROM `{table}` WHERE source_created_at IS NULL OR updated_at < source_created_at",
            impact="Improves auditability and prevents stale sequencing.",
            gain=2,
            evidence="Foundational timeliness coverage is not established.",
            confidence=0.80,
        ),
    ]

def _source_column_for_target(
    column_mappings: Any,
    target_field: str,
) -> Optional[str]:
    normalized_target = _text(
        target_field
    ).lower()

    for mapping in _items(column_mappings):
        if not isinstance(mapping, dict):
            continue

        mapped_target = _text(
            mapping.get("target_field")
        ).lower()

        if mapped_target != normalized_target:
            continue

        source_column = _text(
            mapping.get("source_column")
        )

        return source_column or None

    return None

def fallback_quality_rule_suggestions(
    context: Dict[str, Any],
    *,
    error: Optional[Exception] = None,
) -> Dict[str, Any]:
    """Deterministic fallback so the feature still works if the LLM fails."""
    if error:
        logger.warning("Using deterministic DQ-rule fallback: %s", error)

    table = _identifier(context["dataset_name"])

    column_mappings = (
        context.get("column_mappings")
        if isinstance(context.get("column_mappings"), list)
        else []
    )
    product_variant_source_column = _source_column_for_target(
        column_mappings, "product_variant"
    ) or None

    rules: List[Dict[str, Any]] = []
    for opportunity in context["deterministic_opportunities"][:5]:
        key, evidence = opportunity["key"], opportunity["reason"]
        confidence = min(0.97, 0.65 + opportunity["opportunity_score"] / 333)

        if key in {"BASELINE_RULE_COVERAGE", "PREVENTIVE_RULE_EXPANSION"}:
            rules.extend(_baseline_rules(context))
            break
        if key in {"DUPLICATE_PREVENTION", "IDENTITY_UNIQUENESS"}:
            rules.append(_rule(
                rule_id="ACTIVE_BUSINESS_KEY_UNIQUENESS",
                name="Active Business-Key Uniqueness",
                dimension="UNIQUENESS",
                severity=opportunity["severity"],
                rule_type="UNIQUENESS",
                description="Require every active business key to resolve to one governed record.",
                rationale=evidence,
                risk="Duplicates increase over-merge, under-merge, and reporting risk.",
                logic="COUNT(active records grouped by business_key) <= 1",
                sql=f"SELECT business_key, COUNT(*) c FROM `{table}` WHERE active_flag = TRUE GROUP BY business_key HAVING c > 1",
                impact="Reduces duplicate creation and steward review pressure.",
                gain=5,
                evidence=evidence,
                confidence=confidence,
            ))
        elif key == "REQUIRED_FIELD_COMPLETENESS":
            rules.append(_rule(
                rule_id="CRITICAL_ATTRIBUTE_COMPLETENESS",
                name="Critical Attribute Completeness",
                dimension="COMPLETENESS",
                severity=opportunity["severity"],
                rule_type="REQUIRED_FIELD",
                description="Require all domain-critical identity and operational fields before certification.",
                rationale=evidence,
                risk="Missing attributes reduce match quality and block automation.",
                logic="critical_field_1 IS NOT NULL AND critical_field_2 IS NOT NULL",
                sql=f"SELECT * FROM `{table}` WHERE critical_field_1 IS NULL OR critical_field_2 IS NULL",
                impact="Improves certification readiness and reduces incomplete-record review.",
                gain=4,
                evidence=evidence,
                confidence=confidence,
            ))
        elif key == "GTIN_CHECKSUM_CONTROL":
            rules.append(_rule(
                rule_id="GTIN_CHECKSUM_VALIDITY",
                name="GTIN GS1 Check-Digit Validation",
                dimension="VALIDITY",
                severity="HIGH",
                rule_type="REFERENCE_LOOKUP",
                description=(
                    "Require structurally valid GTINs to pass "
                    "GS1 modulo-10 check-digit validation."
                ),
                rationale=evidence,
                risk=(
                    "Invalid GTIN check digits can break barcode "
                    "verification, product syndication, trading-partner "
                    "integrations, and global product identification."
                ),
                logic=(
                    "GTIN length/format must be valid and the GS1 "
                    "check digit must match the deterministic modulo-10 "
                    "calculation."
                ),
                sql=None,
                impact=(
                    "Routes corrupted GTINs to authoritative source "
                    "verification before downstream use."
                ),
                gain=4,
                evidence=evidence,
                confidence=confidence,
            ))

        elif key == "PRODUCT_VARIANT_FORMAT_CONTROL":
            rules.append(_rule(
                rule_id="PRODUCT_VARIANT_FORMAT_VALIDITY",
                name="Product Variant Quantity/UOM Format Validation",
                dimension="VALIDITY",
                severity="HIGH",
                rule_type="REGEX",
                description=(
                    "Require product_variant to contain a numeric "
                    "quantity, one separator space, and an uppercase "
                    "UOM token."
                ),
                rationale=evidence,
                risk=(
                    "Malformed product variants weaken catalog "
                    "matching, pack-size interpretation, ordering, "
                    "inventory, and downstream product analytics."
                ),
                logic=(
                    "product_variant matches the governed structural "
                    "pattern <quantity><single-space><UOM>."
                ),
                sql=(
                    f"SELECT * FROM `{table}` "
                    "WHERE product_variant IS NOT NULL "
                    "AND NOT REGEXP_CONTAINS("
                    "CAST(product_variant AS STRING), "
                    "r'^[0-9]+(?:\\.[0-9]+)? [A-Z]+$')"
                ),
                impact=(
                    "Separates structurally invalid Product Variant "
                    "values from safe standardization candidates."
                ),
                gain=5,
                evidence=evidence,
                confidence=confidence,
            ))

        elif key == "PRODUCT_VARIANT_UOM_POLICY":
            rules.append(_rule(
                rule_id="PRODUCT_VARIANT_UOM_ALLOWED_VALUE",
                name="Product Variant Governed UOM Validation",
                dimension="VALIDITY",
                severity="HIGH",
                rule_type="REFERENCE_LOOKUP",
                description=(
                    "Validate the UOM token embedded in product_variant "
                    "against the organization-approved Product UOM "
                    "vocabulary."
                ),
                rationale=evidence,
                risk=(
                    "Unapproved or ambiguous UOM values can change "
                    "pack-size meaning and create unsafe ordering, "
                    "pricing, or inventory interpretation."
                ),
                logic=(
                    "parsed product_variant UOM exists in the effective "
                    "tenant-governed Product UOM reference set."
                ),
                sql=None,
                impact=(
                    "Prevents unknown UOM semantics from entering "
                    "trusted product workflows."
                ),
                gain=4,
                evidence=evidence,
                confidence=confidence,
            ))

        elif key == "GTIN_FORMAT_CONTROL":
            rules.append(_rule(
                rule_id="GTIN_FORMAT_VALIDITY",
                name="GTIN Numeric Length Validation",
                dimension="VALIDITY",
                severity="HIGH",
                rule_type="REGEX",
                description=(
                    "Require GTIN values to contain only digits and use "
                    "a supported GTIN length."
                ),
                rationale=evidence,
                risk=(
                    "Malformed GTINs break product identification, "
                    "barcode workflows, and trading-partner exchange."
                ),
                logic=(
                    "GTIN contains only digits and uses an approved "
                    "GTIN-8, GTIN-12, GTIN-13, or GTIN-14 length."
                ),
                sql=(
                    f"SELECT * FROM `{table}` "
                    "WHERE gtin IS NOT NULL "
                    "AND NOT REGEXP_CONTAINS("
                    "CAST(gtin AS STRING), "
                    "r'^(?:\\d{8}|\\d{12}|\\d{13}|\\d{14})$')"
                ),
                impact=(
                    "Identifies malformed GTINs before checksum "
                    "validation and authoritative remediation."
                ),
                gain=4,
                evidence=evidence,
                confidence=confidence,
            ))

        elif key == "PRODUCT_VARIANT_STANDARDIZATION":
            rules.append(_rule(
                rule_id="PRODUCT_VARIANT_STANDARDIZATION",
                name="Product Variant Canonical Standardization",
                dimension="STANDARDIZATION",
                severity="MEDIUM",
                rule_type="REGEX",
                description=(
                    "Normalize safe Product Variant presentation issues "
                    "without changing quantity or UOM business meaning."
                ),
                rationale=evidence,
                risk=(
                    "Whitespace and casing differences create false "
                    "non-matches and inconsistent product reporting."
                ),
                logic=(
                    "Normalize product_variant using the governed Product Variant "
                    "pattern: preserve numeric quantity, normalize whitespace to one "
                    "separator space, and uppercase the UOM token. Never invent or "
                    "replace quantity or UOM business meaning."
                ),
                sql=(
                    f"SELECT product_variant, "
                    "UPPER(TRIM(CAST(product_variant AS STRING))) "
                    f"AS product_variant_standardized FROM `{table}` "
                    "WHERE product_variant IS NOT NULL"
                ),
                impact=(
                    "Improves product matching and catalog consistency "
                    "without fabricating business data."
                ),
                gain=5,
                evidence=evidence,
                confidence=confidence,
                field_name="product_variant",
                source_column=product_variant_source_column,
            ))

        elif key == "FORMAT_AND_DOMAIN_VALIDATION":
            rules.append(_rule(
                rule_id="DOMAIN_FORMAT_VALIDATION",
                name="Domain Format and Allowed-Value Validation",
                dimension="VALIDITY",
                severity=opportunity["severity"],
                rule_type="REGEX",
                description="Validate governed attributes against approved formats and reference values.",
                rationale=evidence,
                risk="Invalid values weaken matching, integrations, and reporting.",
                logic="attribute matches approved regex and allowed-value domain",
                sql=f"SELECT * FROM `{table}` WHERE NOT REGEXP_CONTAINS(attribute, r'<approved_pattern>')",
                impact="Stops invalid values before stewardship and analytics.",
                gain=3,
                evidence=evidence,
                confidence=confidence,
            ))
        elif key == "STANDARDIZATION_ENFORCEMENT":
            rules.append(_rule(
                rule_id="CANONICAL_VALUE_STANDARDIZATION",
                name="Canonical Value Standardization",
                dimension="STANDARDIZATION",
                severity=opportunity["severity"],
                rule_type="REFERENCE_LOOKUP",
                description="Map variant values to approved canonical representations.",
                rationale=evidence,
                risk="Unstandardized values increase false non-matches.",
                logic="normalized_value equals approved canonical reference value",
                sql=f"SELECT s.* FROM `{table}` s LEFT JOIN `reference_mapping` r ON UPPER(TRIM(s.raw_value)) = r.source_value WHERE r.canonical_value IS NULL",
                impact="Improves match consistency and reporting alignment.",
                gain=3,
                evidence=evidence,
                confidence=confidence,
            ))
        elif key == "CROSS_FIELD_CONSISTENCY":
            rules.append(_rule(
                rule_id="CROSS_FIELD_CONSISTENCY",
                name="Cross-Field Consistency Validation",
                dimension="CONSISTENCY",
                severity=opportunity["severity"],
                rule_type="CROSS_FIELD",
                description="Validate logically dependent fields against each other.",
                rationale=evidence,
                risk="Contradictory attributes cause unsafe decisions and failed integrations.",
                logic="dependent fields satisfy the approved business relationship",
                sql=f"SELECT * FROM `{table}` WHERE <cross_field_condition_is_invalid>",
                impact="Reduces conflicting attributes in trusted records.",
                gain=3,
                evidence=evidence,
                confidence=confidence,
            ))
        elif key == "MINIMUM_QUALITY_GATE":
            rules.append(_rule(
                rule_id="MINIMUM_RECORD_QUALITY_GATE",
                name="Minimum Record Quality Gate",
                dimension="INTEGRITY",
                severity=opportunity["severity"],
                rule_type="THRESHOLD",
                description="Prevent records below the approved DQ score from certified workflows.",
                rationale=evidence,
                risk="Low-quality records drive incorrect matches and downstream failures.",
                logic="record_dq_score >= approved_minimum_threshold",
                sql=f"SELECT * FROM `{table}` WHERE record_dq_score < <approved_threshold>",
                impact="Routes low-quality records to remediation before automation.",
                gain=5,
                evidence=evidence,
                confidence=confidence,
            ))
        elif key == "SEVERITY_BASED_BLOCKING":
            rules.append(_rule(
                rule_id="CRITICAL_FINDING_AUTOMATION_BLOCK",
                name="Critical Finding Automation Block",
                dimension="INTEGRITY",
                severity="CRITICAL",
                rule_type="THRESHOLD",
                description="Block certification when critical or high findings remain unresolved.",
                rationale=evidence,
                risk="Automating unresolved severe issues creates material operational risk.",
                logic="critical_findings = 0 AND high_findings = 0 before automation",
                sql=None,
                impact="Prevents unsafe automation until material issues are remediated.",
                gain=6,
                evidence=evidence,
                confidence=confidence,
            ))
        elif key == "OPEN_FINDING_AGING":
            rules.append(_rule(
                rule_id="DQ_FINDING_AGING_SLA",
                name="DQ Finding Aging SLA",
                dimension="TIMELINESS",
                severity=opportunity["severity"],
                rule_type="THRESHOLD",
                description="Require findings to be resolved within severity-based SLAs.",
                rationale=evidence,
                risk="Aging findings create persistent quality debt.",
                logic="open_age_days <= severity_sla_days",
                sql="SELECT * FROM `DQ_FINDINGS` WHERE status = 'OPEN' AND DATE_DIFF(CURRENT_DATE(), DATE(created_at), DAY) > severity_sla_days",
                impact="Accelerates remediation and certification readiness.",
                gain=2,
                evidence=evidence,
                confidence=confidence,
            ))
        elif key == "STEWARD_OVERRIDE_PATTERN":
            rules.append(_rule(
                rule_id="STEWARD_OVERRIDE_DRIFT_ALERT",
                name="Steward Override Pattern Alert",
                dimension="ACCURACY",
                severity=opportunity["severity"],
                rule_type="THRESHOLD",
                description="Trigger policy review when steward overrides exceed the approved threshold.",
                rationale=evidence,
                risk="Persistent overrides indicate weak data or misaligned rule thresholds.",
                logic="rolling_override_rate <= approved_override_threshold",
                sql=None,
                impact="Creates a closed loop between stewardship and rule engineering.",
                gain=3,
                evidence=evidence,
                confidence=confidence,
            ))

    if not rules:
        rules = _baseline_rules(context)

    seen = set()
    rules = [r for r in rules if not (r["rule_id"] in seen or seen.add(r["rule_id"]))][:5]
    rank = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    priority = max(rules, key=lambda r: rank[r["severity"]])["severity"]
    current_issues = sum(context[k] for k in [
        "duplicate_record_count", "records_below_threshold",
        "critical_findings", "high_findings", "open_findings_count",
    ])

    if current_issues == 0:
        priority = "MEDIUM"
        headline = "Establish foundational data quality controls"
        strategy = "PREVENTIVE"
    else:
        headline = (
            "Prioritize AI-generated data quality controls"
            if priority in {"HIGH", "CRITICAL"}
            else "Strengthen data quality controls"
        )
        strategy = "REMEDIATION"
    return {
        "headline": headline,
        "summary": "AI identified implementation-ready rules from current DQ posture, rule coverage, steward pressure, and automation-readiness signals.",
        "priority": priority,
        "rule_strategy": strategy,
        "suggested_rules": rules,
        "recommended_sequence": [r["rule_id"] for r in rules],
        "supporting_evidence": context["evidence_summary"],
        "confidence": round(sum(r["confidence"] for r in rules) / len(rules), 4),
    }


# Governed executable recommendations are deterministic controls whose observed
# findings have already been established by Profile Intelligence. The LLM may
# explain, prioritize, or supplement them, but it must not suppress them.
#
# Keep this allowlist intentionally narrow. Add a rule only after its detection,
# remediation contract, and governed execution path are deterministic.
GOVERNED_EXECUTABLE_RULE_IDS = {
    "PRODUCT_VARIANT_STANDARDIZATION",
}


def _merge_governed_executable_suggestions(
    *,
    context: Dict[str, Any],
    ai_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Preserve evidenced governed executable rules alongside AI suggestions.

    Architecture boundary:
      Profile Intelligence / Universal Intelligence -> deterministic evidence
      -> governed executable recommendation -> AI explanation/prioritization
      -> steward approval -> deterministic execution.

    A governed executable rule is merged only when its exact rule_id has a
    positive deterministic trigger count. This prevents the recommendation
    layer from inventing standards or promoting merely preventive controls into
    executable remediation.
    """
    rule_counts = context.get("triggered_rule_counts") or {}

    evidenced_rule_ids = {
        rule_id
        for rule_id in GOVERNED_EXECUTABLE_RULE_IDS
        if _int(rule_counts.get(rule_id), 0) > 0
    }

    if not evidenced_rule_ids:
        return ai_result

    deterministic_result = fallback_quality_rule_suggestions(context)
    deterministic_rules = deterministic_result.get("suggested_rules") or []

    ai_rules = [
        rule
        for rule in (ai_result.get("suggested_rules") or [])
        if isinstance(rule, dict)
    ]
    ai_rule_ids = {
        _text(rule.get("rule_id")).upper()
        for rule in ai_rules
        if _text(rule.get("rule_id"))
    }

    merged_rule_ids: List[str] = []

    for rule in deterministic_rules:
        if not isinstance(rule, dict):
            continue

        rule_id = _text(rule.get("rule_id")).upper()
        if (
            rule_id not in evidenced_rule_ids
            or rule_id in ai_rule_ids
        ):
            continue

        # Do not truncate the governed deterministic rule merely because the
        # LLM already returned five advisory suggestions. Executable evidence
        # has precedence over advisory recommendation count limits.
        ai_rules.append(rule)
        ai_rule_ids.add(rule_id)
        merged_rule_ids.append(rule_id)

    ai_result["suggested_rules"] = ai_rules

    sequence = [
        _text(rule_id)
        for rule_id in _items(ai_result.get("recommended_sequence"))
        if _text(rule_id)
    ]
    sequence_seen = {rule_id.upper() for rule_id in sequence}

    for rule_id in merged_rule_ids:
        if rule_id not in sequence_seen:
            sequence.append(rule_id)
            sequence_seen.add(rule_id)

    ai_result["recommended_sequence"] = sequence

    if merged_rule_ids:
        logger.info(
            "Preserved governed executable DQ recommendations from "
            "deterministic profile evidence. rules=%s",
            ",".join(merged_rule_ids),
        )

    return ai_result


def build_quality_rule_suggestions(
    row: Dict[str, Any],
    *,
    organization_id: str,
    steward_context: Optional[Dict[str, Any]] = None,
    provider: Optional[str] = None,
    use_llm: bool = True,
) -> Dict[str, Any]:
    """Build DQ rule suggestions for the authenticated organization only."""
    context = build_quality_rule_context(
        row,
        organization_id=organization_id,
        steward_context=steward_context,
    )
    if not use_llm:
        return fallback_quality_rule_suggestions(context)
    try:
        result = generate_structured_quality_rule_suggestions(
            build_quality_rule_prompt(context),
            organization_id=organization_id,
            provider=provider,
        )
        if not result.get("suggested_rules"):
            return fallback_quality_rule_suggestions(context)
        if not result.get("supporting_evidence"):
            result["supporting_evidence"] = context["evidence_summary"]

        # The AI recommendation set is advisory. Preserve any ACTIVE/governed
        # executable recommendation whose exact deterministic rule fired during
        # profiling. This is the bridge from legacy Profile Intelligence into
        # the Universal Intelligence architecture: deterministic evidence owns
        # whether an executable standard exists; the LLM cannot suppress it.
        result = _merge_governed_executable_suggestions(
            context=context,
            ai_result=result,
        )

        column_mappings = context.get("column_mappings") or []

        for rule in result.get("suggested_rules") or []:
            if not isinstance(rule, dict):
                continue

            if rule.get("rule_id") == "PRODUCT_VARIANT_STANDARDIZATION":
                rule["field_name"] = "product_variant"
                rule["source_column"] = _source_column_for_target(
                    column_mappings,
                    "product_variant",
                )

        observed_issue_count = (
            context["duplicate_record_count"]
            + context["records_below_threshold"]
            + context["critical_findings"]
            + context["high_findings"]
            + context["open_findings_count"]
        )

        if observed_issue_count == 0:
            result["headline"] = "Establish foundational data quality controls"
            result["priority"] = "MEDIUM"
            result["rule_strategy"] = "PREVENTIVE"

        return result
    except Exception as exc:
        logger.exception("AI DQ rule suggestion generation failed.")
        return fallback_quality_rule_suggestions(context, error=exc)
