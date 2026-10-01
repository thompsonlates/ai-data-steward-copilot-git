"""Shared dependencies and helper functions for API route modules.

Mechanical extraction from the former monolithic app.api.routes module.
Business logic is intentionally unchanged.
"""

from datetime import datetime, timezone

import time

from typing import Any, Optional

import traceback

import uuid

from difflib import SequenceMatcher

import logging

import os

from fastapi import APIRouter, Depends, Query, status

from fastapi.responses import RedirectResponse

from snowflake import connector

from app.api.auth import (
    AuthUser,
    get_current_user,
    get_current_tenant_user,
    require_active_product_access,
)

from app.repositories import dq_execution_repository

from app.services.quality_profile_config_factory import (
    build_quality_profile_config,
)

from app.services.dq_ai_recommendation_service import (
    DqAiRecommendationService,
)

from app.repositories.dq_execution_repository import (
    DqExecutionRepository,
)

from app.api.schemas import (
    DqApprovedRecommendationListResponse,
    DqApprovedRecommendationResponse,
    OneDriveOAuthStartRequest,
    OneDriveColumnMapping,
    OneDriveConnectionTestRequest,
    OneDriveConnectionTestResponse,
    OneDrivePreviewRequest,
    OneDrivePreviewResponse,
    OneDriveProfileRequest,
)

from app.api.schemas import (
    DqRemediationVersionListResponse,
    DqRemediationVersionResponse,
)

from app.services.dq_execution_service import (
    DqExecutionService,
)

from app.api.schemas import (
    DqExecutionValidationRequest,
    DqExecutionRequest,
    GoogleSheetsDqExecutionRequest,
    DqExecutionResponse,
    OneDriveDqExecutionRequest,
)

from app.api.schemas import (
    AiRecommendationFeedbackRequest,
    DqRemediationGenerateRequest,
    DqRemediationGenerateResponse,
    DqRemediationFeedbackRequest,
    DqRemediationFeedbackResponse,
)

from app.api.schemas import (
    ConnectionTestResponse,
    CustomConnectionRequestCreate,
    CustomConnectionRequestResponse,
    EnterpriseConnectionCreate,
    EnterpriseConnectionListResponse,
    EnterpriseConnectionResponse,
    GoogleOAuthStartRequest,
    OnboardingStatusResponse,
    OrganizationOnboardingRequest,
    OrganizationOnboardingResponse,
)

from app.repositories.onboarding_repository import (
    OnboardingRepository,
)

from app.services.google_oauth_service import (
    GoogleOAuthService,
)

from app.services.onedrive_oauth_service import (
    OneDriveOAuthService,
)

from app.services.onboarding_service import (
    OnboardingService,
)

from app.api.entitlements_schemas import (
    DomainEntitlementResponse,
)

from app.repositories.connection_repository import ( ConnectionRepository,)

from app.services.connection_service import ConnectionService

from app.services.secret_manager_service import SecretManagerService

from app.repositories.entitlement_repository import (
    EntitlementRepository,
)

from app.services.entitlement_service import (
    EntitlementService,
)

from app.api.customer_user_schemas import (
    CustomerUserInviteRequest,
    CustomerUserResponse,
)

from app.repositories.customer_user_repository import (
    CustomerUserRepository,
)

from app.services.email_service import EmailService

from app.services.customer_user_service import (
    CustomerUserService,
)

from app.repositories.custom_connection_repository import (
    CustomConnectionRepository,
)

from app.services.custom_connection_service import (
    CustomConnectionService,
)

from app.repositories.record_search_repository import (
    RecordSearchRepository,
)

from app.workflow.connectors.google_sheets_connector import (
    GoogleSheetsConnector,
)

from app.workflow.connectors.onedrive_connector import (
    OneDriveConnector,
)

from app.services.quality_profiler_service import (
    QualityFieldConfig,
    QualityProfileConfig,
    QualityProfilerService,
)

from app.repositories.quality_profiler_repository import (
    QualityProfilerRepository,
)

from app.api.schemas import (
    GoogleSheetsConnectionTestRequest,
    GoogleSheetsPreviewRequest,
    GoogleSheetsProfileRequest,
)

from app.services.quality_profile_config_factory import (
    build_quality_profile_config,
)

record_search_repository = RecordSearchRepository()

entitlement_repository = EntitlementRepository()

entitlement_service = EntitlementService(
    repository=entitlement_repository,
)

customer_user_repository = CustomerUserRepository()

customer_user_service = CustomerUserService(
    repository=customer_user_repository,
    entitlement_service=entitlement_service,
)

PROJECT_ID = os.getenv(
    "GOOGLE_CLOUD_PROJECT",
    "api-project-503305938314",
)

DATASET_ID = os.getenv(
    "BIGQUERY_DATASET",
    "ai_data_steward_mvp",
)

onboarding_repository = OnboardingRepository(
    project_id=PROJECT_ID,
    dataset_id=DATASET_ID,
)

connection_repository = ConnectionRepository(
    project_id=PROJECT_ID,
    dataset_id=DATASET_ID,
)

secret_manager_service = SecretManagerService(
    project_id=PROJECT_ID,
)

connection_service = ConnectionService(
    repository=connection_repository,
    secret_manager=secret_manager_service,
)

google_oauth_service = GoogleOAuthService(
    repository=connection_repository,
    secret_manager=secret_manager_service,

)

onedrive_oauth_service = OneDriveOAuthService(
    repository=connection_repository,
    secret_manager=secret_manager_service,
)

email_service = EmailService()

quality_profiler_repository = QualityProfilerRepository(
    project_id=PROJECT_ID,
    dataset=DATASET_ID,
)

dq_execution_repository = DqExecutionRepository(
    project_id=PROJECT_ID,
    dataset=DATASET_ID,
)

dq_execution_service = DqExecutionService(
    connection_repository=connection_repository,
    secret_manager=secret_manager_service,
    quality_repository=quality_profiler_repository,
    entitlement_service=entitlement_service,
    dq_execution_repository=dq_execution_repository,
)

custom_connection_repository = (
    CustomConnectionRepository(
        project_id=PROJECT_ID,
        dataset_id=DATASET_ID,
    )
)

custom_connection_service = (
    CustomConnectionService(
        repository=custom_connection_repository,
        email_service=email_service,
    )
)

customer_user_service = CustomerUserService(
    repository=customer_user_repository,
    entitlement_service=entitlement_service,
    email_service=email_service,
)

onboarding_service = OnboardingService(
    repository=onboarding_repository,
    entitlement_service=entitlement_service,
    email_service=email_service,
)

from app.services.record_search_service import RecordSearchService

from app.api.schemas import RecordSearchResponse

record_search_service = RecordSearchService()

from fastapi import APIRouter, Depends, HTTPException, Query, Request

logger = logging.getLogger(__name__)

from fastapi import Request

from fastapi.encoders import jsonable_encoder

from fastapi.responses import JSONResponse

from google.cloud import bigquery

import re

from app.workflow.orchestration.workflow_orchestrator import (
        WorkflowOrchestrator,
    )

from app.api.auth import AuthUser, get_current_user

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

from app.repositories.ai_recommendation_feedback_repository import (
    AiRecommendationFeedbackRepository,
)

from app.services.address_intelligence_service import AddressIntelligenceService

from app.services.bq_logger import BigQueryLogger

from app.services.bq_metrics import BigQueryMetrics

from app.services.entity_resolution_engine import EntityResolutionEngine

from app.services.llm_service import LLMService

from app.services.policy_intelligence import PolicyIntelligenceEngine

from app.services.risk_engine import evaluate_risk

from app.services.prompt_builder import build_match_explain_prompt

from app.services.governance_service_engine import (
    build_dq_policy_governance_context,
    build_governance_ai_recommendation,
)

from app.api.schemas import GovernanceDqPolicyComplianceResponse

from app.services.quality_intelligence_service import (build_quality_rule_suggestions,)

from app.api.schemas import (
    MatchExplainRequest,
    MatchExplainResponse,
    MatchFeedbackRequest,
    MatchFeedbackResponse,
    MetricsOverviewResponse,
    DqDashboardResponse,
    DqRuleSuggestionsResponse,
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

import json

from fastapi import File, Form, UploadFile

from app.api.schemas import (
    CsvPreviewResponse,
    CsvProfileResponse,
)

from app.services.csv_ingestion_service import (
    CsvIngestionError,
    CsvIngestionService,
)

from app.services.quality_profiler_service import (
    QualityFieldConfig,
    QualityProfileConfig,
    QualityProfilerService,
)

from app.repositories.quality_profiler_repository import (
    QualityProfilerRepository,
)

policy_engine = PolicyIntelligenceEngine()

bq = BigQueryLogger()

metrics = BigQueryMetrics()

csv_ingestion_service = CsvIngestionService()

csv_quality_profiler_service = (
    QualityProfilerService()
)

csv_quality_profiler_repository = (
    QualityProfilerRepository()
)

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

def require_current_organization_id(current_user: AuthUser) -> str:
    """Return the authenticated tenant ID or fail closed.

    Never coerce a missing organization_id with str(), because str(None)
    becomes "None" and can leak invalid tenant context downstream.
    """
    organization_id = (current_user.organization_id or "").strip()

    if not organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Authenticated organization context is required.",
        )

    if not organization_id.startswith("org_"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Authenticated organization context is invalid.",
        )

    return organization_id

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

_DQ_RULE_SUGGESTION_CACHE: dict[
    str,
    dict[str, Any],
] = {}

_DQ_RULE_SUGGESTION_TTL_SECONDS = 600

def _get_cached_dq_rule_suggestions(
    cache_key: str,
) -> Any | None:
    cached = _DQ_RULE_SUGGESTION_CACHE.get(
        cache_key
    )

    if cached is None:
        return None

    expires_at = float(
        cached.get("expires_at", 0)
    )

    if time.time() >= expires_at:
        _DQ_RULE_SUGGESTION_CACHE.pop(
            cache_key,
            None,
        )
        return None

    return cached.get("data")

def _set_cached_dq_rule_suggestions(
    cache_key: str,
    data: Any,
) -> None:
    _DQ_RULE_SUGGESTION_CACHE[cache_key] = {
        "data": data,
        "expires_at": (
            time.time()
            + _DQ_RULE_SUGGESTION_TTL_SECONDS
        ),
    }

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

def _get_onedrive_connector(
    *,
    connection_id: str,
    organization_id: str,
) -> OneDriveConnector:
    microsoft_credentials = (
        connection_service.get_microsoft_oauth_credentials(
            connection_id=connection_id,
            organization_id=organization_id,
        )
    )

    return OneDriveConnector(
        credentials=microsoft_credentials
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

def generate_text_insight(
    llm: LLMService,
    prompt: str,
    *,
    organization_id: str,
) -> str | None:
    """
    Generate a tenant-scoped plain-text insight
    with whichever LLM method is available.
    """
    try:
        if hasattr(llm, "ask"):
            value = llm.ask(
                prompt,
                organization_id=organization_id,
            )

        elif hasattr(llm, "generate_text"):
            value = llm.generate_text(
                prompt,
                organization_id=organization_id,
            )

        else:
            value = llm.generate_explanation(
                prompt,
                organization_id=organization_id,
            )

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

                if (
                    isinstance(candidate, str)
                    and candidate.strip()
                ):
                    return candidate.strip()

        return None

    except Exception as e:
        print(
            f"AI insight generation failed: {e}"
        )
        return None
