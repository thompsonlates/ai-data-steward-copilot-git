from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List

from app.services.llm_service import LLMService

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
- Do not discuss data quality unless explicitly mentioned.
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

Derived Governance Blockers:
{prompt_context.get("derived_governance_blockers")}

Recommendation Required:
{prompt_context.get("recommendation_required")}

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
) -> Dict[str, Any]:
    effective_organization_id = (
        _require_organization_id(
            organization_id
        )
    )

    context = (
        build_governance_recommendation_context(
            dataset_row,
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

    # --------------------------------------------------
    # 2. Persist / reuse Governance recommendation
    # --------------------------------------------------

    repository = (
        GovernanceAiRecommendationRepository()
    )

    persisted = repository.get_or_create_recommendation(
        organization_id=effective_organization_id,
        dataset_id=str(
            dataset_row.get("dataset_id")
            or ""
        ),
        dataset_name=str(
            dataset_row.get("dataset_name")
            or ""
        ),
        domain=(
            str(dataset_row.get("domain"))
            if dataset_row.get("domain")
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
            if len(blockers) >= 2
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
