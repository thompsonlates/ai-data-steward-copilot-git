from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List

from app.services.llm_service import LLMService
from app.services.policy_intelligence import PolicyIntelligenceEngine

from app.repositories.governance_ai_recommendation_repository import (
    GovernanceAiRecommendationRepository,
)


logger = logging.getLogger(__name__)


def _require_organization_id(
    organization_id: str,
) -> str:
    normalized = str(
        organization_id or ""
    ).strip()

    if not normalized:
        raise ValueError(
            "organization_id is required for "
            "tenant-isolated governance operations."
        )

    if not normalized.startswith("org_"):
        raise ValueError(
            "organization_id must use the org_ identifier standard."
        )

    return normalized


def _validate_context_organization(
    *,
    organization_id: str,
    context: Dict[str, Any],
    context_name: str,
) -> None:
    context_organization_id = str(
        context.get("organization_id")
        or ""
    ).strip()

    if (
        context_organization_id
        and context_organization_id != organization_id
    ):
        raise ValueError(
            f"{context_name} does not belong to the "
            "authenticated organization."
        )


def _strip_tenant_metadata(
    value: Any,
) -> Any:
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


def _extract_governance_json(
    raw: str,
) -> Dict[str, Any]:
    text = (raw or "").strip()

    text = re.sub(
        r"^```json\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"^```\s*",
        "",
        text,
    )
    text = re.sub(
        r"\s*```$",
        "",
        text,
    )

    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")

        if start < 0 or end <= start:
            raise ValueError(
                "Governance AI response did not "
                "contain valid JSON."
            )

        value = json.loads(
            text[start : end + 1]
        )

    if not isinstance(value, dict):
        raise ValueError(
            "Governance AI response was not "
            "a JSON object."
        )

    return value


def _validate_governance_recommendation(
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(
            "Governance recommendation payload "
            "must be a dictionary."
        )

    headline = str(
        payload.get("headline")
        or "Review governance readiness"
    ).strip()

    summary = str(
        payload.get("summary")
        or (
            "Governance remediation is recommended "
            "based on the available dataset evidence."
        )
    ).strip()

    priority = str(
        payload.get("priority")
        or "MEDIUM"
    ).strip().upper()

    if priority not in {
        "LOW",
        "MEDIUM",
        "HIGH",
    }:
        priority = "MEDIUM"

    recommended_actions = payload.get(
        "recommended_actions"
    )

    if not isinstance(
        recommended_actions,
        list,
    ):
        recommended_actions = []

    recommended_actions = [
        str(item).strip()
        for item in recommended_actions
        if item is not None
        and str(item).strip()
    ][:3]

    if not recommended_actions:
        recommended_actions = [
            "Review the dataset's current governance posture."
        ]

    supporting_evidence = payload.get(
        "supporting_evidence"
    )

    if not isinstance(
        supporting_evidence,
        list,
    ):
        supporting_evidence = []

    supporting_evidence = [
        str(item).strip()
        for item in supporting_evidence
        if item is not None
        and str(item).strip()
    ][:5]

    if not supporting_evidence:
        supporting_evidence = [
            "Recommendation generated from available "
            "governance metrics."
        ]

    try:
        confidence = float(
            payload.get(
                "confidence",
                0.7,
            )
        )
    except (TypeError, ValueError):
        confidence = 0.7

    confidence = max(
        0.0,
        min(
            1.0,
            confidence,
        ),
    )

    return {
        "headline": headline,
        "summary": summary,
        "priority": priority,
        "recommended_actions": (
            recommended_actions
        ),
        "supporting_evidence": (
            supporting_evidence
        ),
        "confidence": confidence,
    }


def generate_structured_governance_recommendation(
    prompt: str,
    *,
    organization_id: str,
    provider: str | None = None,
) -> Dict[str, Any]:
    effective_organization_id = (
        _require_organization_id(
            organization_id
        )
    )

    provider_name = (
        provider
        or os.getenv(
            "GOVERNANCE_LLM_PROVIDER"
        )
        or "claude"
    )

    try:
        llm = LLMService(
            provider=provider_name
        )

        raw = llm.ask(
            prompt,
            organization_id=(
                effective_organization_id
            ),
        )

        payload = (
            _extract_governance_json(
                raw
            )
        )

        return (
            _validate_governance_recommendation(
                payload
            )
        )

    except Exception as exc:
        logger.exception(
            "Governance recommendation "
            "generation failed: %s",
            type(exc).__name__,
        )
        raise


def build_dq_policy_governance_context(
    *,
    organization_id: str,
    profile_run_id: str,
    domain: str,
    policy_id: str | None = None,
    policy_version: str | None = None,
) -> Dict[str, Any]:
    """
    Build deterministic Governance Intelligence evidence for Policy -> DQ Rule
    compliance. MET / AT_RISK / NOT_MET / NOT_EVALUATED is determined only by
    PolicyIntelligenceEngine from persisted DQ_RULE_EXECUTION evidence.
    """
    effective_organization_id = _require_organization_id(organization_id)
    effective_profile_run_id = str(profile_run_id or "").strip()
    effective_domain = str(domain or "").strip().upper()

    if not effective_profile_run_id:
        raise ValueError("profile_run_id is required.")
    if not effective_domain:
        raise ValueError("domain is required.")

    policy_engine = PolicyIntelligenceEngine()
    compliance = policy_engine.evaluate_profile_against_policy(
        organization_id=effective_organization_id,
        profile_run_id=effective_profile_run_id,
        domain=effective_domain,
        policy_id=(str(policy_id).strip() if policy_id else None),
        policy_version=(
            str(policy_version).strip() if policy_version else None
        ),
    )

    _validate_context_organization(
        organization_id=effective_organization_id,
        context=compliance,
        context_name="DQ policy compliance evidence",
    )

    rule_results: List[Dict[str, Any]] = []
    for raw_rule in compliance.get("rule_results") or []:
        if not isinstance(raw_rule, dict):
            continue
        rule_results.append({
            "dq_rule_id": raw_rule.get("dq_rule_id"),
            "authoritative": bool(
                raw_rule.get("catalog_authoritative", False)
            ),
            "required": bool(raw_rule.get("required_flag", False)),
            "severity": raw_rule.get("severity"),
            "compliance_status": raw_rule.get("compliance_status"),
            "required_compliance_rate": raw_rule.get(
                "required_compliance_rate"
            ),
            "measured_compliance_rate": raw_rule.get(
                "measured_compliance_rate"
            ),
            "execution_status": raw_rule.get("execution_status"),
            "evaluated_record_count": raw_rule.get(
                "evaluated_record_count", 0
            ),
            "passed_record_count": raw_rule.get("passed_record_count", 0),
            "failed_record_count": raw_rule.get("failed_record_count", 0),
            "skipped_record_count": raw_rule.get("skipped_record_count", 0),
            "governed_rule_description": raw_rule.get(
                "governed_rule_description"
            ),
            "diagnostic_rule_ids": raw_rule.get("diagnostic_rule_ids") or [],
            "compliance_evidence_id": raw_rule.get(
                "compliance_evidence_id"
            ),
            "remediation_approval_status": raw_rule.get(
                "remediation_approval_status"
            ),
            "remediation_recommendation_id": raw_rule.get(
                "remediation_recommendation_id"
            ),
            "remediation_version_id": raw_rule.get(
                "remediation_version_id"
            ),

            # Governed remediation rollup. These fields describe the
            # remediation lifecycle only; they do not determine MET /
            # AT_RISK / NOT_MET policy compliance.
            "remediation_status": raw_rule.get(
                "remediation_status"
            ),
            "authoritative_remediation_approval_status": raw_rule.get(
                "authoritative_remediation_approval_status"
            ),
            "authoritative_remediation_recommendation_id": raw_rule.get(
                "authoritative_remediation_recommendation_id"
            ),
            "authoritative_remediation_version_id": raw_rule.get(
                "authoritative_remediation_version_id"
            ),
            "diagnostic_rule_count": int(
                raw_rule.get("diagnostic_rule_count") or 0
            ),
            "diagnostic_recommendation_rule_count": int(
                raw_rule.get(
                    "diagnostic_recommendation_rule_count"
                )
                or 0
            ),
            "approved_diagnostic_rule_count": int(
                raw_rule.get(
                    "approved_diagnostic_rule_count"
                )
                or 0
            ),
            "approved_diagnostic_rule_ids": raw_rule.get(
                "approved_diagnostic_rule_ids"
            )
            or [],
            "diagnostic_remediation_states": raw_rule.get(
                "diagnostic_remediation_states"
            )
            or [],
            "reason": raw_rule.get("reason"),
        })

    status = str(
        compliance.get("policy_compliance_status") or "NOT_EVALUATED"
    ).strip().upper()

    return {
        "organization_id": effective_organization_id,
        "policy_id": compliance.get("policy_id"),
        "policy_version": compliance.get("policy_version"),
        "domain": effective_domain,
        "profile_run_id": effective_profile_run_id,
        "policy_compliance_status": status,
        "mapped_rule_count": int(compliance.get("mapped_rule_count") or 0),
        "required_rule_count": int(compliance.get("required_rule_count") or 0),
        "met_rule_count": int(compliance.get("met_rule_count") or 0),
        "at_risk_rule_count": int(compliance.get("at_risk_rule_count") or 0),
        "not_met_rule_count": int(compliance.get("not_met_rule_count") or 0),
        "not_evaluated_rule_count": int(
            compliance.get("not_evaluated_rule_count") or 0
        ),
        "evaluated_at": compliance.get("evaluated_at"),
        "rule_results": rule_results,
    }


def attach_dq_policy_governance_context(
    dataset_row: Dict[str, Any],
    *,
    organization_id: str,
    profile_run_id: str,
    domain: str,
    policy_id: str | None = None,
    policy_version: str | None = None,
) -> Dict[str, Any]:
    """Return a copy of a dataset governance row enriched with DQ policy evidence."""
    effective_organization_id = _require_organization_id(organization_id)
    _validate_context_organization(
        organization_id=effective_organization_id,
        context=dataset_row,
        context_name="Governance dataset row",
    )
    enriched = dict(dataset_row)
    enriched["organization_id"] = effective_organization_id
    enriched["dq_policy_compliance"] = build_dq_policy_governance_context(
        organization_id=effective_organization_id,
        profile_run_id=profile_run_id,
        domain=domain,
        policy_id=policy_id,
        policy_version=policy_version,
    )
    return enriched



def _format_policy_rate(value: Any) -> str:
    try:
        if value is None:
            return "not available"
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "not available"


def _apply_dq_policy_recommendation_guardrail(
    recommendation: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Enforce deterministic Policy -> DQ Rule evidence after AI generation.

    The LLM may explain the evidence, but it may never downgrade, contradict,
    or recalculate the deterministic policy compliance result.
    """
    normalized = _validate_governance_recommendation(recommendation)

    compliance = context.get("dq_policy_compliance")
    if not isinstance(compliance, dict):
        return normalized

    status = str(
        compliance.get("policy_compliance_status")
        or "NOT_EVALUATED"
    ).strip().upper()

    if status == "MET":
        return normalized

    rule_results = [
        item
        for item in (compliance.get("rule_results") or [])
        if isinstance(item, dict)
    ]

    required_rules = [
        item
        for item in rule_results
        if bool(item.get("required"))
    ]
    primary_rule = (
        required_rules[0]
        if required_rules
        else (rule_results[0] if rule_results else {})
    )

    policy_id = str(
        compliance.get("policy_id")
        or "the active governance policy"
    ).strip()
    dq_rule_id = str(
        primary_rule.get("dq_rule_id")
        or "the required DQ control"
    ).strip()

    measured = _format_policy_rate(
        primary_rule.get("measured_compliance_rate")
    )
    required = _format_policy_rate(
        primary_rule.get("required_compliance_rate")
    )

    remediation_approval_status = str(
        primary_rule.get("remediation_approval_status")
        or ""
    ).strip().upper()

    remediation_status = str(
        primary_rule.get("remediation_status")
        or ""
    ).strip().upper()

    approved_diagnostic_rule_count = int(
        primary_rule.get("approved_diagnostic_rule_count")
        or 0
    )
    diagnostic_rule_count = int(
        primary_rule.get("diagnostic_rule_count")
        or 0
    )
    approved_diagnostic_rule_ids = [
        str(item).strip().upper()
        for item in (
            primary_rule.get("approved_diagnostic_rule_ids")
            or []
        )
        if str(item).strip()
    ]

    evidence = list(
        normalized.get("supporting_evidence") or []
    )

    deterministic_evidence = (
        f"{policy_id}: {dq_rule_id} is {status}; "
        f"measured compliance {measured} versus "
        f"required {required}."
    )
    if deterministic_evidence not in evidence:
        evidence.insert(0, deterministic_evidence)

    if status == "NOT_MET":
        if remediation_status == "AUTHORITATIVE_APPROVED":
            action = (
                "Execute the approved authoritative remediation against the "
                "source data, then re-profile before policy compliance is "
                "considered verified."
            )
            remediation_summary = (
                "The authoritative governed control has an approved "
                "remediation artifact."
            )

        elif remediation_status == "ALL_DIAGNOSTICS_APPROVED":
            action = (
                "Execute the approved diagnostic remediations, then re-profile "
                "the data to verify the authoritative policy control."
            )
            remediation_summary = (
                "All supporting diagnostic remediation paths are approved, "
                "but policy verification still requires execution and re-profile."
            )

        elif remediation_status == "PARTIALLY_APPROVED":
            action = (
                "Complete review of the remaining supporting diagnostic "
                "remediation paths, execute approved changes, and re-profile "
                "the data."
            )

            approved_text = (
                ", ".join(approved_diagnostic_rule_ids)
                if approved_diagnostic_rule_ids
                else "one or more diagnostic controls"
            )

            remediation_summary = (
                f"Remediation is partially approved: "
                f"{approved_diagnostic_rule_count} of "
                f"{diagnostic_rule_count} supporting diagnostic controls "
                f"are approved ({approved_text})."
            )

        elif remediation_status == "PENDING_REVIEW":
            action = (
                "Review the available remediation artifacts for the failing "
                "governed control and supporting diagnostics, approve the "
                "appropriate changes, then execute and re-profile."
            )
            remediation_summary = (
                "Remediation artifacts exist but have not completed "
                "steward approval."
            )

        elif remediation_approval_status:
            # Backward-compatible fallback for older policy evidence rows.
            action = (
                "Execute the approved remediation against the source data, "
                "then re-profile before policy compliance is considered verified."
            )
            remediation_summary = (
                "An approved remediation artifact exists for this control."
            )

        else:
            action = (
                "Review and approve remediation for the failing required DQ "
                "control, execute the approved change, and re-profile the data."
            )
            remediation_summary = (
                "No approved remediation path is currently recorded for the "
                "governed control."
            )

        normalized["headline"] = "DQ policy remediation required"
        normalized["summary"] = (
            f"{policy_id} is NOT MET because {dq_rule_id} measured "
            f"{measured} compliance against a required threshold of "
            f"{required}. {remediation_summary} Certification readiness must "
            "remain blocked until deterministic profile evidence verifies "
            "compliance."
        )
        normalized["priority"] = "HIGH"
        normalized["recommended_actions"] = [
            action,
            (
                "Use the next profile run as the verification event for "
                "the governed policy threshold."
            ),
        ]
        normalized["supporting_evidence"] = evidence[:5]
        normalized["confidence"] = max(
            float(normalized.get("confidence") or 0.0),
            0.95,
        )
        return normalized

    if status == "AT_RISK":
        normalized["headline"] = "DQ policy threshold at risk"
        normalized["summary"] = (
            f"{policy_id} is AT RISK based on deterministic DQ policy "
            f"evidence for {dq_rule_id}. Measured compliance is {measured} "
            f"against a required threshold of {required}."
        )
        if normalized.get("priority") == "LOW":
            normalized["priority"] = "MEDIUM"
        normalized["recommended_actions"] = [
            (
                "Review the governed DQ control and current profile evidence "
                "before the next certification decision."
            ),
            (
                "Re-profile after remediation or source-data changes to "
                "confirm the policy posture."
            ),
        ]
        normalized["supporting_evidence"] = evidence[:5]
        normalized["confidence"] = max(
            float(normalized.get("confidence") or 0.0),
            0.90,
        )
        return normalized

    if status == "NOT_EVALUATED":
        normalized["headline"] = "DQ policy verification required"
        normalized["summary"] = (
            f"{policy_id} has not been deterministically evaluated for "
            f"{dq_rule_id}. Required policy controls must be executed and "
            "profile evidence captured before certification readiness can "
            "be confirmed."
        )
        if normalized.get("priority") == "LOW":
            normalized["priority"] = "MEDIUM"
        normalized["recommended_actions"] = [
            (
                "Run or verify the required DQ control and capture "
                "DQ_RULE_EXECUTION evidence."
            ),
            (
                "Re-evaluate the policy after deterministic profile "
                "evidence is available."
            ),
        ]
        normalized["supporting_evidence"] = evidence[:5]
        normalized["confidence"] = max(
            float(normalized.get("confidence") or 0.0),
            0.90,
        )
        return normalized

    return normalized

def build_governance_recommendation_context(
    row: Dict[str, Any],
    *,
    organization_id: str,
) -> Dict[str, Any]:
    effective_organization_id = (
        _require_organization_id(
            organization_id
        )
    )

    _validate_context_organization(
        organization_id=(
            effective_organization_id
        ),
        context=row,
        context_name="Governance dataset row",
    )

    blockers: List[str] = []

    data_owner = str(
        row.get("data_owner")
        or ""
    ).strip()

    fair_score = row.get(
        "fair_overall_score"
    )

    fair_assessed = bool(
        row.get(
            "fair_assessment_completed"
        )
    )

    fair_score_value: float | None = None

    try:
        if fair_score is not None:
            fair_score_value = float(
                fair_score
            )
    except (TypeError, ValueError):
        fair_score_value = None

    if (
        fair_assessed
        and fair_score_value is not None
        and fair_score_value < 0.8
    ):
        blockers.append(
            "FAIR_SCORE_BELOW_TARGET"
        )

    try:
        failed_checks = int(
            row.get("failed_checks")
            or 0
        )
    except (TypeError, ValueError):
        failed_checks = 0

    ready_value = row.get(
        "ready_for_certification"
    )

    if ready_value is not None:
        ready = bool(ready_value)
    else:
        try:
            readiness_score = float(
                row.get(
                    "certification_readiness_score"
                )
                or 0
            )
        except (TypeError, ValueError):
            readiness_score = 0.0

        ready = (
            bool(
                row.get(
                    "certified_for_use"
                )
            )
            and str(
                row.get(
                    "pass_fail_status"
                )
                or ""
            ).upper()
            == "PASS"
            and readiness_score >= 80
        )

    certification_status = str(
        row.get(
            "certification_status"
        )
        or "UNKNOWN"
    ).upper()

    if not data_owner:
        blockers.append(
            "DATA_OWNER_MISSING"
        )

    if (
        fair_score_value is not None
        and fair_score_value < 0.8
        and "FAIR_SCORE_BELOW_TARGET"
        not in blockers
    ):
        blockers.append(
            "FAIR_SCORE_BELOW_TARGET"
        )

    if failed_checks > 0:
        blockers.append(
            "CERTIFICATION_CHECKS_FAILED"
        )

    if not ready:
        blockers.append(
            "NOT_READY_FOR_CERTIFICATION"
        )

    dq_policy_compliance = row.get("dq_policy_compliance")
    if isinstance(dq_policy_compliance, dict):
        _validate_context_organization(
            organization_id=effective_organization_id,
            context=dq_policy_compliance,
            context_name="DQ policy compliance evidence",
        )
        dq_policy_status = str(
            dq_policy_compliance.get("policy_compliance_status")
            or "NOT_EVALUATED"
        ).strip().upper()
        dq_policy_blocker = {
            "NOT_MET": "DQ_POLICY_NOT_MET",
            "AT_RISK": "DQ_POLICY_AT_RISK",
            "NOT_EVALUATED": "DQ_POLICY_NOT_EVALUATED",
        }.get(dq_policy_status)
        if dq_policy_blocker and dq_policy_blocker not in blockers:
            blockers.append(dq_policy_blocker)

        # A required DQ policy failure or missing required policy evidence
        # blocks readiness even when legacy certification checks still pass.
        if dq_policy_status in {"NOT_MET", "NOT_EVALUATED"}:
            ready = False

            if (
                certification_status == "CERTIFIED"
                and "CERTIFICATION_POLICY_CONFLICT" not in blockers
            ):
                blockers.append(
                    "CERTIFICATION_POLICY_CONFLICT"
                )

    return {
        **row,
        "organization_id": (
            effective_organization_id
        ),
        "ready_for_certification": (
            ready
        ),
        "derived_governance_blockers": (
            blockers
        ),
        "recommendation_required": (
            bool(blockers)
        ),
        "certification_status": (
            certification_status
        ),
        "certification_policy_conflict": (
            "CERTIFICATION_POLICY_CONFLICT" in blockers
        ),
    }


def build_governance_recommendation_prompt(
    context: Dict[str, Any],
    *,
    organization_id: str,
) -> str:
    effective_organization_id = (
        _require_organization_id(
            organization_id
        )
    )

    _validate_context_organization(
        organization_id=(
            effective_organization_id
        ),
        context=context,
        context_name="Governance recommendation context",
    )

    prompt_context = (
        _strip_tenant_metadata(
            context
        )
    )

    return f"""
You are an Enterprise Data Governance Advisor.

Your task is to generate ONE concise governance recommendation based ONLY on
the evidence provided.

Rules:
- Do not invent facts.
- Use only the supplied governance metrics.
- Keep the response business friendly.
- Recommend only governance actions.
- Discuss data quality only when deterministic DQ policy compliance evidence is supplied.
- Never calculate or infer policy compliance; use the supplied deterministic status and rates exactly.
- Treat remediation_status independently from policy compliance: approval of remediation never means the policy is MET.
- When remediation_status is PARTIALLY_APPROVED, explicitly acknowledge partial approval and do not say that no remediation has been approved.
- When remediation_status is ALL_DIAGNOSTICS_APPROVED or AUTHORITATIVE_APPROVED, state that execution and re-profile are still required before policy verification.
- If a required DQ policy status is NOT_MET, the recommendation MUST NOT be LOW priority and MUST NOT say to merely continue monitoring.
- If a required DQ policy status is NOT_EVALUATED, do not claim certification readiness until deterministic evidence exists.
- A legacy CERTIFIED status does not override a current required DQ policy failure; call out the governance conflict when present.
- Do not recommend software products.
- Maximum 3 recommended actions.
- Confidence must reflect the available evidence.
- Return valid JSON only.
- Treat all evidence as belonging only to the current authenticated tenant.
- Never infer or mention internal organization, customer, or user identifiers.

Dataset Name:
{prompt_context.get("dataset_name")}

Certification Status:
{prompt_context.get("certification_status")}

Governance FAIR Score:
{prompt_context.get("fair_overall_score")}

Data Owner:
{prompt_context.get("data_owner")}

Lifecycle Stage:
{prompt_context.get("lifecycle_stage")}

Failed Checks:
{prompt_context.get("failed_checks")}

Certified For Use:
{prompt_context.get("certified_for_use")}

Pass/Fail Status:
{prompt_context.get("pass_fail_status")}

Certification Readiness Score:
{prompt_context.get("certification_readiness_score")}

Ready For Certification:
{prompt_context.get("ready_for_certification")}

Certification / Policy Conflict:
{prompt_context.get("certification_policy_conflict")}

Derived Governance Blockers:
{prompt_context.get("derived_governance_blockers")}

Recommendation Required:
{prompt_context.get("recommendation_required")}

DQ Policy Compliance Evidence:
{json.dumps(prompt_context.get("dq_policy_compliance"), default=str)}

Return EXACTLY this JSON format:

{{
  "headline": "",
  "summary": "",
  "priority": "LOW|MEDIUM|HIGH",
  "recommended_actions": [
    "",
    "",
    ""
  ],
  "supporting_evidence": [
    "",
    "",
    ""
  ],
  "confidence": 0.00
}}
""".strip()


def build_governance_ai_recommendation(
    dataset_row: Dict[str, Any],
    *,
    organization_id: str,
    provider: str | None = None,
    profile_run_id: str | None = None,
    domain: str | None = None,
    policy_id: str | None = None,
    policy_version: str | None = None,
) -> Dict[str, Any]:
    effective_organization_id = (
        _require_organization_id(
            organization_id
        )
    )

    recommendation_row = dict(dataset_row)

    effective_profile_run_id = str(
        profile_run_id or ""
    ).strip()
    requested_domain = str(
        domain
        or recommendation_row.get("domain")
        or ""
    ).strip().upper()
    row_domain = str(
        recommendation_row.get("domain")
        or ""
    ).strip().upper()

    if (
        not isinstance(
            recommendation_row.get("dq_policy_compliance"),
            dict,
        )
        and effective_profile_run_id
        and requested_domain
        and (
            not row_domain
            or row_domain == requested_domain
        )
    ):
        recommendation_row = attach_dq_policy_governance_context(
            recommendation_row,
            organization_id=effective_organization_id,
            profile_run_id=effective_profile_run_id,
            domain=requested_domain,
            policy_id=policy_id,
            policy_version=policy_version,
        )

    context = (
        build_governance_recommendation_context(
            recommendation_row,
            organization_id=(
                effective_organization_id
            ),
        )
    )

    # --------------------------------------------------
    # 1. Build the recommendation
    # --------------------------------------------------

    if not context.get(
        "recommendation_required"
    ):
        recommendation = {
            "headline": (
                "Continue governance monitoring"
            ),
            "summary": (
                "No material governance blocker is "
                "currently present in the available "
                "dataset evidence."
            ),
            "priority": "LOW",
            "recommended_actions": [
                (
                    "Continue scheduled certification "
                    "and FAIR assessments."
                )
            ],
            "supporting_evidence": [
                (
                    "No material deterministic governance "
                    "blocker detected."
                )
            ],
            "confidence": 0.90,
        }

    else:
        try:
            prompt = (
                build_governance_recommendation_prompt(
                    context,
                    organization_id=(
                        effective_organization_id
                    ),
                )
            )

            recommendation = (
                generate_structured_governance_recommendation(
                    prompt,
                    organization_id=(
                        effective_organization_id
                    ),
                    provider=provider,
                )
            )

        except Exception:
            recommendation = (
                fallback_governance_recommendation(
                    context,
                    organization_id=(
                        effective_organization_id
                    ),
                )
            )

    recommendation = _apply_dq_policy_recommendation_guardrail(
        recommendation,
        context,
    )

    # --------------------------------------------------
    # 2. Persist / reuse Governance recommendation
    # --------------------------------------------------

    repository = (
        GovernanceAiRecommendationRepository()
    )

    persisted = repository.get_or_create_recommendation(
        organization_id=effective_organization_id,
        dataset_id=str(
            recommendation_row.get("dataset_id")
            or ""
        ),
        dataset_name=str(
            recommendation_row.get("dataset_name")
            or ""
        ),
        domain=(
            str(recommendation_row.get("domain"))
            if recommendation_row.get("domain")
            is not None
            else None
        ),
        headline=str(
            recommendation.get("headline")
            or ""
        ),
        summary=str(
            recommendation.get("summary")
            or ""
        ),
        priority=str(
            recommendation.get("priority")
            or "MEDIUM"
        ),
        recommended_actions=(
            recommendation.get(
                "recommended_actions"
            )
            or []
        ),
        supporting_evidence=(
            recommendation.get(
                "supporting_evidence"
            )
            or []
        ),
        confidence=(
            float(
                recommendation.get(
                    "confidence"
                )
            )
            if recommendation.get(
                "confidence"
            )
            is not None
            else None
        ),
    )

    recommendation[
        "recommendation_id"
    ] = persisted["recommendation_id"]

    return recommendation

def fallback_governance_recommendation(
    context: Dict[str, Any],
    *,
    organization_id: str,
) -> Dict[str, Any]:
    effective_organization_id = (
        _require_organization_id(
            organization_id
        )
    )

    _validate_context_organization(
        organization_id=(
            effective_organization_id
        ),
        context=context,
        context_name="Fallback governance context",
    )

    blockers = (
        context.get(
            "derived_governance_blockers"
        )
        or []
    )

    actions: list[str] = []

    if "DQ_POLICY_NOT_MET" in blockers:
        dq_policy = context.get("dq_policy_compliance")
        primary_rule = {}

        if isinstance(dq_policy, dict):
            candidate_rules = [
                item
                for item in (dq_policy.get("rule_results") or [])
                if isinstance(item, dict)
                and bool(item.get("required"))
            ]
            if candidate_rules:
                primary_rule = candidate_rules[0]

        remediation_status = str(
            primary_rule.get("remediation_status")
            or ""
        ).strip().upper()

        if remediation_status == "PARTIALLY_APPROVED":
            actions.append(
                "Complete remaining diagnostic remediation approvals, execute approved changes, and re-profile the data."
            )
        elif remediation_status in {
            "ALL_DIAGNOSTICS_APPROVED",
            "AUTHORITATIVE_APPROVED",
        }:
            actions.append(
                "Execute approved remediation and re-profile the data to verify policy compliance."
            )
        elif remediation_status == "PENDING_REVIEW":
            actions.append(
                "Complete steward review of available remediation artifacts before execution and re-profile."
            )
        else:
            actions.append(
                "Resolve required DQ policy control failures, approve remediation, execute changes, and re-profile the data."
            )

    if "DQ_POLICY_NOT_EVALUATED" in blockers:
        actions.append(
            "Run or verify required DQ policy controls before certification readiness is confirmed."
        )

    if (
        "DATA_OWNER_MISSING"
        in blockers
    ):
        actions.append(
            "Assign an accountable data owner."
        )

    if (
        "FAIR_SCORE_BELOW_TARGET"
        in blockers
    ):
        actions.append(
            "Complete missing FAIR metadata and "
            "reassess the score."
        )

    if (
        "CERTIFICATION_CHECKS_FAILED"
        in blockers
    ):
        actions.append(
            "Resolve failed certification checks."
        )

    if "DQ_POLICY_AT_RISK" in blockers:
        actions.append(
            "Review DQ policy controls that are below or near the required threshold."
        )

    if (
        "NOT_READY_FOR_CERTIFICATION"
        in blockers
        and not actions
    ):
        actions.append(
            "Review the recorded certification blocker."
        )

    if not actions:
        actions.append(
            "Continue monitoring governance posture."
        )

    return {
        "headline": actions[0],
        "summary": (
            "The recommendation was generated from "
            "deterministic governance rules because "
            "an AI recommendation was unavailable."
        ),
        "priority": (
            "HIGH"
            if (
                "DQ_POLICY_NOT_MET" in blockers
                or len(blockers) >= 2
            )
            else "MEDIUM"
        ),
        "recommended_actions": actions[:3],
        "supporting_evidence": (
            blockers
            if blockers
            else [
                (
                    "No deterministic governance "
                    "blockers detected."
                )
            ]
        ),
        "confidence": 0.8,
    }