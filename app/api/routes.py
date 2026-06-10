from datetime import datetime, timezone
import traceback
import uuid
from difflib import SequenceMatcher
from typing import Optional
import logging

from app.services.record_search_service import RecordSearchService
from app.api.schemas import RecordSearchResponse
record_search_service = RecordSearchService()

from fastapi import APIRouter, Depends, HTTPException, Query, Request
router = APIRouter()

logger = logging.getLogger(__name__)
from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from google.cloud import bigquery
router = APIRouter()

import re

from app.workflow.orchestration.workflow_orchestrator import (
        WorkflowOrchestrator,
    )


from app.api.deps import get_current_user
from app.api.schemas import (
    MatchExplainRequest,
    MatchExplainResponse,
    MatchFeedbackRequest,
    MatchFeedbackResponse,
    MetricsOverviewResponse,
    DqDashboardResponse,
    PolicyConfigResponse,
    PolicyDraftRequest,
    PolicyDraftResponse,
    PolicyPublishRequest,
    PolicyPublishResponse,
    GovernanceOverviewResponse,
    GovernanceKPI,
    GovernanceDatasetStatus,
    GovernanceBlocker,
    GovernancePolicyActivity,
)
from app.services.address_intelligence_service import AddressIntelligenceService
from app.services.bq_logger import BigQueryLogger
from app.services.bq_metrics import BigQueryMetrics
from app.services.entity_resolution_engine import EntityResolutionEngine
from app.services.llm_service import LLMService
from app.services.policy_intelligence import PolicyIntelligenceEngine
from app.services.prompt_builder import build_match_explain_prompt

router = APIRouter()

policy_engine = PolicyIntelligenceEngine()
bq = BigQueryLogger()
metrics = BigQueryMetrics()

DEFAULT_PROMPT_VERSION = "match-explain-v1"
DEFAULT_FEATURE_SCHEMA_VERSION = "v1"
DEFAULT_LLM_PROVIDER = "claude"

VALID_DECISIONS = {
    "AUTO_MERGE",
    "APPROVE_MERGE",
    "REVIEW",
    "REVIEW_REQUIRED",
    "REJECT_MERGE",
    "BLOCK_MERGE",
}

VALID_RISK_FLAGS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_request_id(request_id: Optional[str]) -> str:
    return request_id or f"req_{uuid.uuid4().hex[:16]}"


def compute_address_similarity(a: Optional[str], b: Optional[str]) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.upper(), b.upper()).ratio()


def build_retry_prompt(prompt: str) -> str:
    return (
        prompt
        + "\n\nIMPORTANT: Return ONLY compact valid JSON."
        + "\nDo not include markdown, explanations, or code fences."
        + "\nKeep explanation_summary under 30 words."
        + "\nReturn at most 2 rule_analysis items."
        + "\nKeep each reason under 18 words."
        + "\nUse double quotes for all strings."
        + "\nDo not use trailing commas."
    )


def normalize_ai_payload(payload: dict) -> dict:
    confidence_raw = payload.get("confidence", 0.5)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.5

    confidence = max(0.0, min(1.0, confidence))

    risk_flag_raw = str(payload.get("risk_flag", "MEDIUM")).strip().upper()
    risk_flag = risk_flag_raw if risk_flag_raw in VALID_RISK_FLAGS else "MEDIUM"

    ai_decision_raw = str(payload.get("ai_decision", "REVIEW")).strip().upper()
    ai_decision = ai_decision_raw if ai_decision_raw in VALID_DECISIONS else "REVIEW"

    recommended_action_raw = str(
        payload.get("recommended_action", ai_decision)
    ).strip().upper()
    recommended_action = (
        recommended_action_raw
        if recommended_action_raw in VALID_DECISIONS
        else ai_decision
    )

    explanation_summary = str(
        payload.get("explanation_summary", "No explanation returned.")
    ).strip()
    if not explanation_summary:
        explanation_summary = "No explanation returned."

    rule_analysis_raw = payload.get("rule_analysis", [])
    rule_analysis: list[dict] = []

    if isinstance(rule_analysis_raw, list):
        for item in rule_analysis_raw[:3]:
            if not isinstance(item, dict):
                continue

            rule = str(item.get("rule", "UNKNOWN_RULE")).strip() or "UNKNOWN_RULE"

            impact_raw = str(item.get("impact", "MEDIUM")).strip().upper()
            impact = impact_raw if impact_raw in {"LOW", "MEDIUM", "HIGH"} else "MEDIUM"

            reason = str(item.get("reason", "No reason provided.")).strip()
            if not reason:
                reason = "No reason provided."

            rule_analysis.append(
                {
                    "rule": rule,
                    "impact": impact,
                    "reason": reason,
                }
            )

    return {
        "ai_decision": ai_decision,
        "confidence": confidence,
        "risk_flag": risk_flag,
        "recommended_action": recommended_action,
        "explanation_summary": explanation_summary,
        "rule_analysis": rule_analysis,
    }


def get_model_metadata(provider: str) -> tuple[str, str]:
    provider_normalized = provider.lower()

    if provider_normalized == "claude":
        return "anthropic", "claude-sonnet-4-6"

    return "vertex", "gemini-2.5-flash"


def normalize_match_evidence_timeline(
    events: list[dict] | None,
) -> list[dict]:

    if not events:
        return []

    normalized: list[dict] = []

    for idx, event in enumerate(events):

        normalized.append(
            {
                "step": event.get("step", idx + 1),
                "stage": event.get("stage", "SIGNAL"),
                "title": event.get("title", "Evidence Evaluated"),
                "detail": event.get("detail", ""),
                "tone": event.get("tone", "neutral"),
                "signal_name": event.get("signal_name"),
                "signal_score": event.get("signal_score"),
                "policy_rule": event.get("policy_rule"),
                "impact": event.get("impact"),
            }
        )

    return normalized

def normalize_entity_resolution_signals(
    signals: list[dict] | None,
) -> list[dict]:

    if not signals:
        return []

    normalized: list[dict] = []

    for signal in signals:

        normalized.append(
            {
                "signal_name": signal.get("signal_name"),
                "signal_score": signal.get("signal_score"),
                "signal_weight": signal.get("signal_weight"),
                "weighted_score": signal.get("weighted_score"),
                "detail": signal.get("detail"),
                "tone": signal.get("tone"),
                "signal_band": signal.get("signal_band"),
                "signal_rank": signal.get("signal_rank"),
            }
        )

    return normalized

def contains_test_data(value: str) -> bool:

    if not value:
        return False

    test_patterns = {
        "test",
        "dummy",
        "fake",
        "unknown",
        "na",
        "n/a",
    }

    return value.strip().lower() in test_patterns


def is_underage_dob(dob: str) -> bool:

    try:
        birth_year = int(dob[:4])
        current_year = datetime.now().year

        age = current_year - birth_year

        return age < 18

    except Exception:
        return False
    
def compute_governance_risk(
    issues: list[str],
) -> int:

    risk = 0

    for issue in issues:

        if "Missing" in issue:
            risk += 10

        elif "Invalid" in issue:
            risk += 15

        elif "Restricted" in issue:
            risk += 30

        elif "Underage" in issue:
            risk += 40

        elif "dummy" in issue.lower():
            risk += 25

        else:
            risk += 10

    return min(risk, 100)


def derive_risk_band(score: int) -> str:

    if score <= 20:
        return "LOW"

    if score <= 50:
        return "MODERATE"

    if score <= 75:
        return "ELEVATED"

    return "SEVERE"


def compute_automation_readiness(
    risk_score: int,
) -> int:

    readiness = 100 - risk_score

    return max(readiness, 0)
   
def compute_source_trust(
    source_system: str,
) -> float:

    trusted_sources = {
        "Epic": 1.0,
        "Cerner": 0.95,
        "MDM": 0.98,
        "ERP": 0.90,
        "CRM": 0.80,
    }

    return trusted_sources.get(
        source_system,
        0.50,
    )


def compute_deterministic_strength(
    required: dict,
    domain: str,
) -> float:

    score = 0.0

    if domain == "PATIENT":

        if required.get("patient_id"):
            score += 0.50

        if required.get("dob"):
            score += 0.20

    if domain == "PROVIDER":

        if required.get("provider_id"):
            score += 0.40

        if required.get("npi"):
            score += 0.40

    return min(score, 1.0)


def derive_automation_decision(
    automation_readiness: int,
) -> str:

    if automation_readiness >= 90:
        return "AUTO_APPROVE"

    if automation_readiness >= 70:
        return "REVIEW_REQUIRED"

    return "MANUAL_STEWARD_REVIEW"

@router.post("/stage-gate/validate")
async def validate_stage_gate(payload: dict):

    issues = []

    domain = payload.get("domain")
    source_system = payload.get("source_system")

    required = payload.get("required_fields", {})

    first_name = required.get("first_name", "").strip()
    last_name = required.get("last_name", "").strip()
    dob = required.get("dob", "").strip()
    email = required.get("email", "").strip()
    address = required.get("address", "").strip()

    # =========================
    # Required Field Checks
    # =========================

    if not first_name:
        issues.append("Missing first_name")

    if not last_name:
        issues.append("Missing last_name")

    if not dob:
        issues.append("Missing dob")

    if not address:
        issues.append("Missing address")

    # =========================
    # Format Validation
    # =========================

    if dob and not is_valid_date(dob):
        issues.append("Invalid dob format. Expected YYYY-MM-DD")

    if email and not is_valid_email(email):
        issues.append("Invalid email format")

    # =========================
    # Source Validation
    # =========================

    allowed_sources = {
        "ERP",
        "CRM",
        "MDM",
        "Epic",
        "Cerner",
    }

    if source_system not in allowed_sources:
        issues.append(
            f"Unauthorized source system: {source_system}"
        )

        # =========================
        # Business Policy Validation
        # =========================

    if contains_test_data(first_name):
        issues.append(
            "Test or dummy first_name detected"
        )

    if contains_test_data(last_name):
        issues.append(
            "Test or dummy last_name detected"
        )

    if dob and is_underage_dob(dob):
        issues.append(
            "Underage patient detected"
        )

    # Example governance source restriction

    restricted_sources = {
        "LegacyFlatFile",
        "UnknownVendor",
    }

    if source_system in restricted_sources:
        issues.append(
            f"Restricted source system: {source_system}"
        )

    # Example high-risk policy

    if (
        domain == "PATIENT"
        and not email
        and not address
    ):
        issues.append(
            "Insufficient patient contact attributes"
        )

    # =========================
    # Governance Decision
    # =========================

    if not issues:
        status = "PASS"
    elif len(issues) <= 2:
        status = "REVIEW"
    else:
        status = "FAIL"

    if status == "PASS":
        recommendation = "ALLOW_TO_MDM"

    elif status == "REVIEW":
        recommendation = "ROUTE_TO_STEWARD"

    else:
        recommendation = "BLOCK_FROM_MDM"

            # =========================
            # AI Governance Scoring
            # =========================

    governance_risk_score = compute_governance_risk(
        issues
    )

    risk_band = derive_risk_band(
        governance_risk_score
    )

    automation_readiness = (
        compute_automation_readiness(
            governance_risk_score
        )
    )

                # =========================
                # Steward Routing
                # =========================

    if governance_risk_score >= 75:

        steward_action = "ESCALATE_TO_GOVERNANCE"

    elif governance_risk_score >= 40:

        steward_action = "ROUTE_TO_STEWARD"

    else:

        steward_action = "AUTO_APPROVE"

            # =========================
            # Automation Readiness
            # =========================

    source_trust_score = compute_source_trust(
        source_system
    )

    deterministic_strength = (
        compute_deterministic_strength(
            required,
            domain,
        )
    )

    automation_readiness_score = round(
        (
            (100 - governance_risk_score) * 0.40 +
            source_trust_score * 100 * 0.20 +
            deterministic_strength * 100 * 0.40
        ),
        2,
    )

    automation_decision = (
        derive_automation_decision(
            automation_readiness_score
        )
    )

    return {
    "stage_gate_status": status,
    "domain": domain,
    "source_system": source_system,
    "issues": issues,
    "recommendation": recommendation,

    # AI Governance Intelligence
    "governance_risk_score": governance_risk_score,
    "risk_band": risk_band,
    "automation_readiness": automation_readiness,
    "steward_action": steward_action,

    # Automation Readiness
    "source_trust_score": source_trust_score,
    "deterministic_strength": deterministic_strength,

    "automation_readiness_score": (
        automation_readiness_score
    ),

    "automation_decision": (
        automation_decision
    ),
}

def is_valid_email(email: str) -> bool:
    return bool(
        re.match(
            r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$",
            email,
        )
    )


def is_valid_date(value: str) -> bool:
    return bool(
        re.match(
            r"^\d{4}-\d{2}-\d{2}$",
            value,
        )
    )


@router.get("/metrics/governance-overview", response_model=GovernanceOverviewResponse)
def get_governance_overview(days: int = Query(30, ge=1, le=365)):
    project_id = "api-project-503305938314"
    dataset_id = "ai_data_steward_mvp"

    client = bigquery.Client(project=project_id)

    kpi_sql = f"""
    SELECT *
    FROM `{project_id}.{dataset_id}.V_GOVERNANCE_INTELLIGENCE`
    """

    dataset_sql = f"""
    SELECT *
    FROM `{project_id}.{dataset_id}.V_GOVERNANCE_DATASET_DETAIL`
    ORDER BY
      COALESCE(certification_readiness_score, 0) ASC,
      failed_checks DESC,
      COALESCE(fair_overall_score, 0) ASC
    LIMIT 200
    """

    blockers_sql = f"""
    SELECT
      blocker_reason,
      blocker_count
    FROM `{project_id}.{dataset_id}.V_GOVERNANCE_TOP_BLOCKERS`
    LIMIT 10
    """

    policy_sql = f"""
    SELECT *
    FROM `{project_id}.{dataset_id}.V_GOVERNANCE_POLICY_ACTIVITY`
    WHERE DATE(changed_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL {days} DAY)
       OR changed_at IS NULL
    ORDER BY changed_at DESC
    LIMIT 20
    """

    try:
        kpi_rows = [dict(row) for row in client.query(kpi_sql).result()]
        dataset_rows = [dict(row) for row in client.query(dataset_sql).result()]
        blocker_rows = [dict(row) for row in client.query(blockers_sql).result()]
        policy_rows = [dict(row) for row in client.query(policy_sql).result()]

        if not kpi_rows:
            raise HTTPException(status_code=404, detail="No governance KPI data found")

        kpi_row = kpi_rows[0]

        kpis = GovernanceKPI(
            total_datasets=int(kpi_row.get("total_datasets") or 0),
            certified_datasets=int(kpi_row.get("certified_datasets") or 0),
            ready_for_certification=int(kpi_row.get("ready_for_certification") or 0),
            in_progress_certifications=int(
                kpi_row.get("in_progress_certifications") or 0
            ),
            avg_fair_score=float(kpi_row.get("avg_fair_score") or 0),
            open_governance_issues=int(kpi_row.get("open_governance_issues") or 0),
            total_checks=int(kpi_row.get("total_checks") or 0),
            passed_checks=int(kpi_row.get("passed_checks") or 0),
            failed_checks=int(kpi_row.get("failed_checks") or 0),
            check_pass_rate=float(kpi_row.get("check_pass_rate") or 0),
            active_policies=int(kpi_row.get("active_policies") or 0),
            recent_policy_changes_30d=int(
                kpi_row.get("recent_policy_changes_30d") or 0
            ),
        )

        dataset_statuses = [GovernanceDatasetStatus(**row) for row in dataset_rows]
        top_blockers = [GovernanceBlocker(**row) for row in blocker_rows]
        policy_activity = [GovernancePolicyActivity(**row) for row in policy_rows]

        return GovernanceOverviewResponse(
            kpis=kpis,
            dataset_statuses=dataset_statuses,
            top_blockers=top_blockers,
            policy_activity=policy_activity,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load governance overview: {str(e)}",
        )


@router.get("/metrics/dq-overview", response_model=DqDashboardResponse)
def get_dq_metrics_overview(
    days: int = Query(30, ge=1, le=365),
    domain: Optional[str] = Query(None),
):
    try:
        result = metrics.get_dq_dashboard_overview(days=days, domain=domain)
        return result
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load DQ overview: {str(e)}",
        )

@router.get("/records/search",
    response_model=RecordSearchResponse,
)
async def search_records(
    domain: str,
    q: str,
):

    rows = record_search_service.search_records(
        domain=domain,
        search_text=q,
    )

    return {
        "results": [
            {
                "record_id": row["record_id"],
                "mdm_id": row["mdm_id"],
                "domain": row["domain"],
                "display_name": row["display_name"],
                "source_system": row["source_system"],
                "golden_record_flag": row["golden_record_flag"],
                "record": {
                    "member_id": row.get("member_id"),
                    "patient_id": row.get("patient_id"),
                    "provider_id": row.get("provider_id"),
                    "supplier_id": row.get("supplier_id"),
                    "product_id": row.get("product_id"),
                    "first_name": row.get("first_name"),
                    "last_name": row.get("last_name"),
                    "email": row.get("email"),
                    "address": row.get("address"),
                    "dob": row.get("dob"),
                    "npi": row.get("npi"),
                    "specialty": row.get("specialty"),
                    "tax_id": row.get("tax_id"),
                    "gtin": row.get("gtin"),
                    "sku": row.get("sku"),
                    "product_name": row.get("product_name"),
                    "product_variant": row.get("product_variant"),
                    "effective_lot_date": row.get("effective_lot_date"),
                    "source_system": row.get("source_system"),
    },

            }
            for row in rows
        ]
    }


@router.get("/policy/config", response_model=PolicyConfigResponse)
def get_policy_config(
    domain: str = Query(...),
    policy_version: Optional[str] = Query(None),
):
    try:
        result = policy_engine.get_policy_config_bundle(
            domain=domain,
            policy_version=policy_version,
        )
        return PolicyConfigResponse(**result)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load policy config: {str(e)}",
        )


@router.post("/policy/config/draft", response_model=PolicyDraftResponse)
def save_policy_config_draft(payload: PolicyDraftRequest):
    try:
        result = policy_engine.save_policy_draft(payload.model_dump())
        return PolicyDraftResponse(**result)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save policy draft: {str(e)}",
        )


@router.post("/policy/config/publish", response_model=PolicyPublishResponse)
def publish_policy_config(payload: PolicyPublishRequest):
    try:
        result = policy_engine.publish_policy_version(
            domain=payload.domain,
            policy_version=payload.policy_version,
            published_by=payload.published_by or "system",
        )
        return PolicyPublishResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to publish policy config: {str(e)}",
        )


def build_ai_insight_prompt(
    domain: str,
    ai_decision: str,
    recommended_action: str,
    confidence: float,
    risk_flag: str,
    triggered_rules: list[str],
    primary_signal: str | None,
    composite_risk_score: float | None,
    signal_contributions: list[dict] | None = None,
) -> str:
    return f"""
You are AI Data Steward Copilot.

Write one concise steward-facing explanation for an MDM match decision.

Domain: {domain}
AI Decision: {ai_decision}
Recommended Action: {recommended_action}
Confidence: {confidence}
Risk Flag: {risk_flag}
Triggered Rules: {triggered_rules}
Primary Signal: {primary_signal}
Composite Risk Score: {composite_risk_score}
Signal Contributions: {signal_contributions or []}

Requirements:
- 1 to 2 sentences only.
- Plain English for a data steward.
- Explain why the steward should trust, review, or block the decision.
- Do not mention internal model names.
- If critical evidence is missing, call it out.
- Do not return JSON, markdown, bullets, or code fences.
""".strip()


def generate_text_insight(llm: LLMService, prompt: str) -> str | None:
    """Generate a plain-text insight with whichever LLM method is available."""
    try:
        if hasattr(llm, "ask"):
            value = llm.ask(prompt)
        elif hasattr(llm, "generate_text"):
            value = llm.generate_text(prompt)
        else:
            value = llm.generate_explanation(prompt)

        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned or None

        if isinstance(value, dict):
            for key in (
                "ai_insight",
                "insight",
                "explanation_summary",
                "summary",
                "text",
                "content",
            ):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()

        return None
    except Exception as e:
        print(f"AI insight generation failed: {e}")
        return None

@router.post("/match/explain")
async def match_explain(
    request: Request,
    req: MatchExplainRequest,
    provider: str = Query(DEFAULT_LLM_PROVIDER, pattern="^(gemini|claude)$"),
):

    try:  
        user_agent = request.headers.get("User-Agent")
        integration_source = request.headers.get("X-Integration-Source")

        logger.info(
            f"[INTEGRATION] "
            f"UserAgent={user_agent} "
            f"Source={integration_source} "
            f"Domain={req.domain} "
            f"PolicyVersion={req.policy_version} ")
        
        explanation_id = f"exp_{uuid.uuid4().hex[:12]}"

        model_provider, model_version = get_model_metadata(provider)
        llm = LLMService(provider=provider)

        decision_ctx = policy_engine.build_decision_context(req)

        policy_rec = decision_ctx.get("policy_recommendation", {}) or {}
        policy_cfg = decision_ctx.get("policy_config", {}) or {}
        policy_thresholds = decision_ctx.get("policy_thresholds", {}) or {}
        policy_risk_rules = decision_ctx.get("policy_risk_rules", []) or []

        domain = decision_ctx.get("domain") or req.domain
        policy_version = decision_ctx.get("policy_version") or req.policy_version

        if not domain:
            raise HTTPException(status_code=400, detail="domain is required")

        if not policy_version:
            raise HTTPException(status_code=400, detail="policy_version is required")
        
        request_id = ensure_request_id(decision_ctx.get("request_id") or req.request_id)
        audit_packet_id = decision_ctx.get("audit_packet_id")

        domain = (domain or "CUSTOMER").upper()
        req.domain = domain
        req.policy_version = policy_version
        req.request_id = request_id
      
        record_a_id = getattr(req.record_a, "member_id", None) or ""
        record_b_id = getattr(req.record_b, "member_id", None) or ""

        record_a_source_system = getattr(req.record_a, "source_system", None) or ""
        record_b_source_system = getattr(req.record_b, "source_system", None) or ""

        address_service = AddressIntelligenceService()

        record_a_address_intelligence = address_service.validate(req.record_a.address)
        record_b_address_intelligence = address_service.validate(req.record_b.address)

        address_match_insight = address_service.compare(
            record_a_address_intelligence,
            record_b_address_intelligence,
        )

        address_similarity_score = compute_address_similarity(
            record_a_address_intelligence.standardized_address,
            record_b_address_intelligence.standardized_address,
        )
        entity_policy_config = {
            "policy_config": policy_cfg,
            "policy_thresholds": policy_thresholds,
            "policy_risk_rules": policy_risk_rules,
            "signal_weights": policy_rec.get("signal_weights"),
            "weights": policy_rec.get("weights"),
            "source_trust_map": policy_rec.get("source_trust_map"),
            "automation_thresholds": policy_rec.get("automation_thresholds"),
            "signal_tone_thresholds": policy_rec.get("signal_tone_thresholds"),
            "readiness_label_thresholds": policy_rec.get("readiness_label_thresholds"),
        }
        address_similarity_score = locals().get("address_similarity_score", 0.0)
        address_match_insight = locals().get(
        "address_match_insight",
        "No address similarity evaluated for this domain."
    )
        policy_recommended_action = str(
        policy_rec.get("recommendation") or ""
        ).strip().upper()

        final_recommended_action = (
        policy_recommended_action
        if policy_recommended_action in VALID_DECISIONS
        else "REVIEW_REQUIRED"
    )

        policy_risk_flag = str(
        policy_rec.get("highest_risk_level") or ""
    ).strip().upper()

        response_risk_flag = (
        policy_risk_flag
        if policy_risk_flag in VALID_RISK_FLAGS
        else "MEDIUM"
    )


        entity_engine = EntityResolutionEngine()
        entity_resolution = entity_engine.score(
            req=req,
            address_similarity_score=address_similarity_score,
            override_rate_estimate=policy_rec.get("override_rate_estimate"),
            composite_risk_score=policy_rec.get("composite_risk_score"),
            risk_flag=(
            str(policy_rec.get("highest_risk_level") or "").strip().upper()
            if str(policy_rec.get("highest_risk_level") or "").strip().upper() in VALID_RISK_FLAGS
            else "MEDIUM"
        ),
        recommended_action=(
            str(policy_rec.get("recommendation") or "").strip().upper()
            if str(policy_rec.get("recommendation") or "").strip().upper() in VALID_DECISIONS
            else "REVIEW_REQUIRED"
        ),
            address_match_insight=address_match_insight,
            composite_risk_band=policy_rec.get("composite_risk_band"),
            primary_risk_driver=policy_rec.get("primary_risk_driver"),
            policy_config=entity_policy_config,
        )

        prompt = build_match_explain_prompt(
            req=req,
            learning_context=decision_ctx.get("learning_context"),
            policy_context=decision_ctx.get("policy_context"),
            policy_recommendation=decision_ctx.get("policy_recommendation"),
            signal_packets=decision_ctx.get("signal_packets"),
        )
        try:
            ai_payload = llm.generate_explanation(prompt)
        except Exception as e:
            print("LLM PARSE FAILURE:")
            print(str(e))

            retry_prompt = build_retry_prompt(prompt)
            ai_payload = llm.generate_explanation(retry_prompt)

        ai_payload = normalize_ai_payload(ai_payload)

            # ---------------------------------------------------
            # Governance Workflow Orchestration
            # ---------------------------------------------------


        workflow_result = None

        try:
            orchestrator = WorkflowOrchestrator()

            workflow_payload = {
                **ai_payload,
                "explanation_id": explanation_id,
                "request_id": request_id,
                "domain": domain,
                "policy_version": policy_version,
                "record_a": req.record_a.model_dump(),
                "record_b": req.record_b.model_dump(),
                "primary_risk_driver": policy_rec.get("primary_risk_driver"),
                "composite_risk_score": policy_rec.get("composite_risk_score"),
                "composite_risk_band": policy_rec.get("composite_risk_band"),
            }

            workflow_result = orchestrator.evaluate_match_explanation(
                workflow_payload
            )

            print(
                f"Governance Workflow Result: "
                f"{workflow_result}"
            )

        except Exception as workflow_error:
            print(
                "Governance workflow orchestration failed:"
            )
            print(str(workflow_error))

        # Auto-add deterministic Member ID rule
        record_a_member_id = getattr(req.record_a, "member_id", None)
        record_b_member_id = getattr(req.record_b, "member_id", None)

        if (
            record_a_member_id
            and record_b_member_id
            and str(record_a_member_id).strip().lower()
            == str(record_b_member_id).strip().lower()
            ):

                if req.triggered_rules is None:
                    req.triggered_rules = []

                if "MEMBER_ID_EXACT" not in req.triggered_rules:
                    req.triggered_rules.append("MEMBER_ID_EXACT")

                existing_rules = {
                    item.get("rule")
                    for item in ai_payload.get("rule_analysis", [])
                    if isinstance(item, dict)
                }

                if "MEMBER_ID_EXACT" not in existing_rules:
                    ai_payload["rule_analysis"].insert(
                        0,
                            {
                                "rule": "MEMBER_ID_EXACT",
                                "impact": "HIGH",
                                "reason": (
                                    "Exact Member ID match strongly indicates the same member."
                            ),
                        },
                    )
        
        policy_recommended_action = str(
            policy_rec.get("recommendation") or ""
        ).strip().upper()
        final_recommended_action = (
            policy_recommended_action
            if policy_recommended_action in VALID_DECISIONS
            else ai_payload["recommended_action"]
        )

        policy_risk_flag = str(
            policy_rec.get("highest_risk_level") or ""
        ).strip().upper()
        response_risk_flag = (
            policy_risk_flag
            if policy_risk_flag in VALID_RISK_FLAGS
            else ai_payload["risk_flag"]
        )


        insight_prompt = build_ai_insight_prompt(
            domain=domain,
            ai_decision=ai_payload["ai_decision"],
            recommended_action=final_recommended_action,
            confidence=ai_payload["confidence"],
            risk_flag=response_risk_flag,
            triggered_rules=req.triggered_rules or [],
            primary_signal=entity_resolution.get("primary_signal"),
            composite_risk_score=(
                entity_resolution.get("composite_risk_score")
                or policy_rec.get("composite_risk_score")
            ),
            signal_contributions=entity_resolution.get("signal_contributions"),
        )
        ai_insight = generate_text_insight(llm, insight_prompt)


        print("===== FINAL SCORE DEBUG =====")
        print("match_score:", entity_resolution.get("match_score"))
        print(
            "decision_confidence_score:",
            entity_resolution.get("decision_confidence_score"),
        )
        print(
            "automation_readiness_score:",
            entity_resolution.get("automation_readiness_score"),
        )
        print(
            "automation_policy_status:",
            entity_resolution.get("automation_policy_status"),
        )
        print("=============================")
        response_obj = MatchExplainResponse(
            explanation_id=explanation_id,
            ai_decision=ai_payload["ai_decision"],
            confidence=ai_payload["confidence"],
            risk_flag=response_risk_flag,
            match_score=entity_resolution.get("match_score"),
            explanation_summary=ai_payload["explanation_summary"],
            rule_analysis=ai_payload["rule_analysis"],
            recommended_action=final_recommended_action,
            final_recommended_action=entity_resolution.get("final_recommended_action"),
            model_version=model_version,
            model_provider=model_provider,
            prompt_version=DEFAULT_PROMPT_VERSION,
            feature_schema_version=DEFAULT_FEATURE_SCHEMA_VERSION,
            domain=domain,
            policy_version=policy_version,
            policy_hash=None,
            request_id=request_id,
            trace_id=request_id,
            audit_packet_id=audit_packet_id,
            composite_risk_score=policy_rec.get("composite_risk_score"),
            composite_risk_band=policy_rec.get("composite_risk_band"),
            primary_risk_driver=policy_rec.get("primary_risk_driver"),
            record_a_address_intelligence=record_a_address_intelligence,
            record_b_address_intelligence=record_b_address_intelligence,
            address_match_insight=address_match_insight,
            address_similarity_score=address_similarity_score,
            decision_confidence_score=entity_resolution.get("decision_confidence_score"),
            automation_tier=entity_resolution.get("automation_tier"),
            automation_readiness_score=entity_resolution.get("automation_readiness_score"),
            automation_readiness_label=entity_resolution.get("automation_readiness_label"),
            automation_policy_status=entity_resolution.get("automation_policy_status"),
            estimated_false_positive_risk=entity_resolution.get(
                "estimated_false_positive_risk"
            ),
            entity_similarity_score=entity_resolution.get(
            "entity_similarity_score",
            entity_resolution.get("match_score", 0.0),
            ),
            primary_signal=entity_resolution.get("primary_signal"),
            entity_resolution_signals=normalize_entity_resolution_signals(
            entity_resolution.get("signals")
            ),
            entity_resolution_summary=entity_resolution.get("entity_resolution_summary"),
            match_evidence_timeline=normalize_match_evidence_timeline(
            entity_resolution.get("match_evidence_timeline")
            ),
            timeline_events=normalize_match_evidence_timeline(
            entity_resolution.get("timeline_events")
            ),
            ai_insight=ai_insight,
            signal_weights=entity_resolution.get("signal_weights"),
            signal_contributions=entity_resolution.get("signal_contributions"),
            timeline_version="v2",
            workflow_ticket_created=(
            workflow_result.created
            if workflow_result
            else False
        ),

            workflow_ticket_key=(
            workflow_result.jira_key
            if workflow_result
            else None
        ),

            workflow_ticket_url=(
            workflow_result.jira_url
            if workflow_result
            else None
        ),
        )

        log_row = {
            "explanation_id": explanation_id,
            "request_id": request_id,
            "audit_packet_id": audit_packet_id,
            "domain": domain,
            "policy_version": policy_version,
            "policy_hash": None,
            "record_a_id": record_a_id,
            "record_b_id": record_b_id,
            "record_a_source_system": record_a_source_system,
            "record_b_source_system": record_b_source_system,
            "match_score": entity_resolution.get("match_score"),
            "triggered_rules": req.triggered_rules,
            "requested_by": req.requested_by,
            "context_id": req.context_id,
            "ai_decision": response_obj.ai_decision,
            "ai_confidence": response_obj.confidence,
            "risk_flag": response_obj.risk_flag,
            "recommended_action": response_obj.recommended_action,
            "final_recommended_action": response_obj.final_recommended_action,
            "automation_readiness_score": response_obj.automation_readiness_score,
            "automation_readiness_label": response_obj.automation_readiness_label,
            "automation_policy_status": response_obj.automation_policy_status,
            "estimated_false_positive_risk": response_obj.estimated_false_positive_risk,
            "composite_risk_score": response_obj.composite_risk_score,
            "composite_risk_band": response_obj.composite_risk_band,
            "primary_risk_driver": response_obj.primary_risk_driver,
            "decision_confidence_score": response_obj.decision_confidence_score,
            "automation_tier": response_obj.automation_tier,
            "primary_signal": response_obj.primary_signal,
            "steward_decision": None,
            "steward_override_reason": None,
            "steward_user": None,
            "feedback_at": None,
            "steward_override_flag": None,
            "model_provider": model_provider,
            "model_version": model_version,
            "prompt_version": DEFAULT_PROMPT_VERSION,
            "feature_schema_version": DEFAULT_FEATURE_SCHEMA_VERSION,
            "created_at": utc_now_iso(),
        }

        bq.log_explanation(log_row)

        return JSONResponse(content=jsonable_encoder(response_obj))

    except HTTPException:
        raise
    except Exception as e:
            print("========= FULL TRACEBACK =========")
            traceback.print_exc()
            print("==================================")

            raise HTTPException(
                status_code=500,
                detail=str(e)
    )

@router.post("/match/feedback", dependencies=[Depends(get_current_user)])
def match_feedback(req: MatchFeedbackRequest):
    try:
        if not req.domain:
            raise HTTPException(status_code=400, detail="domain is required")

        if not req.policy_version:
            raise HTTPException(status_code=400, detail="policy_version is required")

        request_id = ensure_request_id(req.request_id)
        decision_id = f"dec_{uuid.uuid4().hex[:12]}"
        submitted_at = utc_now_iso()

        recommended_action = metrics.get_recommended_action(req.explanation_id)

        if recommended_action is None:
            raise HTTPException(status_code=404, detail="explanation_id not found")

        override_flag = "Y" if req.steward_decision != recommended_action else "N"

        row = bq.log_feedback_event(
            explanation_id=req.explanation_id,
            steward_decision=req.steward_decision,
            steward_user=req.steward_user,
            steward_override_flag=override_flag,
            request_id=request_id,
            decision_id=decision_id,
            domain=req.domain,
            policy_version=req.policy_version,
            override_reason_code=req.override_reason_code,
            override_reason_note=req.override_reason_note,
            submitted_at=submitted_at,
        )

        payload = MatchFeedbackResponse(
            decision_id=decision_id,
            explanation_id=req.explanation_id,
            status="RECORDED",
            override_flag=override_flag,
            feedback_event_id=row.get("feedback_id"),
            feedback_at=row.get("feedback_at"),
            recommended_action=recommended_action,
            audit_packet_id=row.get("audit_packet_id"),
            request_id=request_id,
            submitted_at=submitted_at,
        )

        return JSONResponse(content=jsonable_encoder(payload))

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/metrics/overview", response_model=MetricsOverviewResponse)
def metrics_overview(days: int = Query(7, ge=1, le=365)):
    try:
        data = metrics.overview(days)

        payload = {
            "days": days,
            "generated_at": utc_now_iso(),
            **data,
        }

        return JSONResponse(content=jsonable_encoder(payload))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))