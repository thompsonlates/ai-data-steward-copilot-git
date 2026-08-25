from datetime import datetime, timezone

import time
from typing import Any

from http import client
import traceback
import uuid
from difflib import SequenceMatcher
from typing import Optional
import logging
import os

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import RedirectResponse

from app.api.auth import AuthUser, get_current_user, get_current_tenant_user

from app.services.dq_ai_recommendation_service import (
    DqAiRecommendationService,
)

from app.api.schemas import (
    AiRecommendationFeedbackRequest,
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
email_service = EmailService()


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
router = APIRouter()

router = APIRouter(prefix="/v1", tags=["Enterprise Connections"])

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
from app.services.governance_service_engine import (build_governance_ai_recommendation,)
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

router = APIRouter()

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

# ============================================================
# DQ Rule Suggestions Cache
# ============================================================

_DQ_RULE_SUGGESTION_CACHE: dict[
    str,
    dict[str, Any],
] = {}

_DQ_RULE_SUGGESTION_TTL_SECONDS = 600  # 10 minutes


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

@router.post(
    "/organization/users/invite",
    response_model=CustomerUserResponse,
)
def invite_customer_user(
    payload: CustomerUserInviteRequest,
    current_user: AuthUser = Depends(
        get_current_tenant_user
    ),
) -> CustomerUserResponse:
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    result = customer_user_service.invite_user(
        organization_id=organization_id,
        email=str(payload.email),
        display_name=payload.display_name,
        organization_role=payload.organization_role,
        billing_role=payload.billing_role,
        invited_by=str(current_user.email),
    )

    return CustomerUserResponse(
        **result
    )

@router.post(
    "/connections/custom-request",
    response_model=CustomConnectionRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_custom_connection_request(
    request: CustomConnectionRequestCreate,
    current_user: AuthUser = Depends(
        get_current_tenant_user
    ),
) -> CustomConnectionRequestResponse:

    organization_id = (
        require_current_organization_id(current_user)
    )

    if not current_user.customer_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "A valid customer membership is required "
                "to request a custom connection."
            ),
        )

    return custom_connection_service.create_request(
        request=request,
        organization_id=organization_id,
        customer_id=current_user.customer_id,
        requested_by=str(current_user.email),
        contact_email=str(current_user.email),
    )

@router.post(
    "/connections/google-sheets/test",
)
def test_google_sheets_connection(
    req: GoogleSheetsConnectionTestRequest,
    current_user: AuthUser = Depends(
        get_current_tenant_user
    ),
):
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    connector = GoogleSheetsConnector()

    result = connector.test_connection(
        spreadsheet_id=req.spreadsheet_url
    )

    sheets = connector.list_sheets(
        spreadsheet_id=req.spreadsheet_url
    )

    return {
        "success": True,
        "organization_id": organization_id,
        "spreadsheet_id": (
            result["spreadsheet_id"]
        ),
        "spreadsheet_title": (
            result.get("spreadsheet_title")
        ),
        "sheets": sheets,
    }

@router.post(
    "/connections/google-sheets/preview",
)
def preview_google_sheet(
    req: GoogleSheetsPreviewRequest,
    current_user: AuthUser = Depends(
        get_current_tenant_user
    ),
):
    require_current_organization_id(
        current_user
    )

    connector = GoogleSheetsConnector()

    rows = connector.read_rows(
        spreadsheet_id=req.spreadsheet_url,
        sheet_name=req.sheet_name,
        header_row=req.header_row,
        limit=req.preview_limit,
    )

    headers = (
        list(rows[0].keys())
        if rows
        else []
    )

    return {
        "headers": headers,
        "rows": rows,
        "row_count": len(rows),
    }

@router.post(
    "/connections/csv/preview",
    response_model=CsvPreviewResponse,
)
async def preview_csv_upload(
    file: UploadFile = File(...),
    preview_rows: int = Form(25),
    current_user: AuthUser = Depends(
        get_current_tenant_user
    ),
) -> CsvPreviewResponse:
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    try:
        file_bytes = await file.read()

        result = (
            csv_ingestion_service.preview_csv(
                organization_id=organization_id,
                file_name=file.filename,
                file_bytes=file_bytes,
                preview_rows=preview_rows,
            )
        )

        return CsvPreviewResponse(
            organization_id=organization_id,
            file_name=result.file_name,
            columns=result.columns,
            rows=result.rows,
            total_preview_rows=(
                result.total_preview_rows
            ),
            delimiter=result.delimiter,
            encoding=result.encoding,
        )

    except CsvIngestionError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "CSV preview failed for "
            "organization_id=%s",
            organization_id,
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to preview CSV file: "
                f"{str(exc)}"
            ),
        ) from exc

@router.post(
    "/dq/profile/csv",
    response_model=CsvProfileResponse,
)
async def profile_csv_upload(
    file: UploadFile = File(...),
    domain: str = Form(...),
    business_key_field: str = Form(...),
    column_mappings: str = Form(...),
    minimum_record_score: float = Form(80.0),
    current_user: AuthUser = Depends(
        get_current_tenant_user
    ),
) -> CsvProfileResponse:
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    try:
        normalized_domain = str(
            domain or ""
        ).strip().upper()

        if not normalized_domain:
            raise ValueError(
                "domain is required."
            )

        normalized_business_key = str(
            business_key_field or ""
        ).strip()

        if not normalized_business_key:
            raise ValueError(
                "business_key_field is required."
            )

        try:
            raw_mappings = json.loads(
                column_mappings
            )
        except json.JSONDecodeError as exc:
            raise ValueError(
                "column_mappings must be valid JSON."
            ) from exc

        if not isinstance(raw_mappings, list):
            raise ValueError(
                "column_mappings must be a JSON array."
            )

        normalized_mappings = []

        seen_source_columns: set[str] = set()
        seen_target_fields: set[str] = set()

        for item in raw_mappings:
            if not isinstance(item, dict):
                raise ValueError(
                    "Each column mapping must be "
                    "a JSON object."
                )

            source_column = str(
                item.get("source_column")
                or ""
            ).strip()

            target_field = str(
                item.get("target_field")
                or ""
            ).strip()

            if not source_column:
                raise ValueError(
                    "source_column is required "
                    "for every mapping."
                )

            if not target_field:
                raise ValueError(
                    "target_field is required "
                    "for every mapping."
                )

            if (
                source_column
                in seen_source_columns
            ):
                raise ValueError(
                    "Each CSV source column can "
                    "only be mapped once."
                )

            if (
                target_field
                in seen_target_fields
            ):
                raise ValueError(
                    "Each target field can only "
                    "be mapped once."
                )

            seen_source_columns.add(
                source_column
            )

            seen_target_fields.add(
                target_field
            )

            normalized_mappings.append(
                {
                    "source_column":
                        source_column,
                    "target_field":
                        target_field,
                }
            )

        if not normalized_mappings:
            raise ValueError(
                "At least one column mapping "
                "is required."
            )

        if (
            normalized_business_key
            not in seen_target_fields
        ):
            raise ValueError(
                "business_key_field must be "
                "included in column_mappings."
            )

        file_bytes = await file.read()

        parsed = (
            csv_ingestion_service
            .parse_csv_records(
                organization_id=(
                    organization_id
                ),
                file_name=file.filename,
                file_bytes=file_bytes,
            )
        )

        source_columns = set(
            parsed["columns"]
        )

        for mapping in normalized_mappings:
            if (
                mapping["source_column"]
                not in source_columns
            ):
                raise ValueError(
                    "Mapped CSV column was not "
                    "found in the uploaded file: "
                    f"{mapping['source_column']}"
                )

        mapped_rows = []

        for source_row in parsed["records"]:
            mapped_row = {
                mapping["target_field"]:
                    source_row.get(
                        mapping[
                            "source_column"
                        ]
                    )
                for mapping
                in normalized_mappings
            }

            mapped_rows.append(
                mapped_row
            )

        if not mapped_rows:
            raise ValueError(
                "CSV file contains no data rows."
            )


        source_name = (
            f"CSV:{parsed['file_name']}"
        )

        config = build_quality_profile_config(
                domain=normalized_domain,
                source_name=source_name,
                business_key_field=normalized_business_key,
                mapped_fields={
                    mapping["target_field"]
                    for mapping in normalized_mappings
                },
                minimum_record_score=minimum_record_score,
)

        result = (
            csv_quality_profiler_service
            .profile_rows(
                organization_id=(
                    organization_id
                ),
                rows=mapped_rows,
                config=config,
                source_name=source_name,
            )
        )

        csv_quality_profiler_repository.save_profile_result(
            result=result
        )

        return CsvProfileResponse(
            organization_id=organization_id,
            profile_run_id=(
                result.profile_run_id
            ),
            file_name=parsed["file_name"],
            domain=result.domain,
            record_count=parsed[
                "record_count"
            ],
            total_records=(
                result.total_records
            ),
            avg_record_score=(
                result.avg_record_score
            ),
            records_below_threshold=(
                result.records_below_threshold
            ),
            records_with_findings=(
                result.records_with_findings
            ),
            total_findings=(
                result.total_findings
            ),
            duplicate_record_count=(
                result.duplicate_record_count
            ),
            source_type="CSV",
            generated_at=(
                result.generated_at
            ),
        )

    except (
        CsvIngestionError,
        ValueError,
    ) as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "CSV DQ profile failed for "
            "organization_id=%s",
            organization_id,
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to profile CSV file: "
                f"{str(exc)}"
            ),
        ) from exc

@router.post(
    "/dq/profile/google-sheets",
)
def profile_google_sheet(
    req: GoogleSheetsProfileRequest,
    current_user: AuthUser = Depends(
        get_current_tenant_user
    ),
):
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    connector = GoogleSheetsConnector()

    raw_rows = connector.read_rows(
        spreadsheet_id=req.spreadsheet_url,
        sheet_name=req.sheet_name,
        header_row=req.header_row,
        limit=100_000,
    )

    mapping = {
        item.source_column: item.target_field
        for item in req.column_mappings
    }

    normalized_rows = []

    for raw_row in raw_rows:
        normalized_row = {}

        for source_column, target_field in mapping.items():
            normalized_row[target_field] = (
                raw_row.get(source_column)
            )

        normalized_rows.append(
            normalized_row
        )

    normalized_domain = (
        req.domain.strip().upper()
    )

    if normalized_domain != "PRODUCT":
        raise HTTPException(
            status_code=400,
            detail=(
                "Google Sheets DQ profiling currently "
                "supports PRODUCT during this rollout."
            ),
        )

    config = QualityProfileConfig(
        domain="PRODUCT",
        source_table="unused.for.google.sheets",
        business_key_field=(
            req.business_key_field
        ),
        minimum_record_score=85.0,
        fields=[
            QualityFieldConfig(
                field_name="product_id",
                required=True,
                uniqueness_key=True,
                weight=2.0,
                severity="CRITICAL",
            ),
            QualityFieldConfig(
                field_name="product_name",
                required=True,
                uniqueness_key=False,
                weight=2.0,
                severity="HIGH",
            ),
            QualityFieldConfig(
                field_name="item_category",
                required=True,
                uniqueness_key=False,
                weight=1.0,
                severity="MEDIUM",
            ),
        ],
    )

    profiler = QualityProfilerService()

    result = profiler.profile_rows(
        organization_id=organization_id,
        rows=normalized_rows,
        config=config,
        source_name=(
            f"GOOGLE_SHEETS:{req.sheet_name}"
        ),
    )

    repository = QualityProfilerRepository()

    repository.save_profile_result(
        result=result
    )

    return {
        "success": True,
        "organization_id": organization_id,
        "profile_run_id": result.profile_run_id,
        "domain": result.domain,
        "total_records": result.total_records,
        "avg_record_score": result.avg_record_score,
        "records_below_threshold": (
            result.records_below_threshold
        ),
        "records_with_findings": (
            result.records_with_findings
        ),
        "total_findings": (
            result.total_findings
        ),
        "duplicate_record_count": (
            result.duplicate_record_count
        ),
    }

@router.get(
    "/metrics/governance-overview",
    response_model=GovernanceOverviewResponse,
)
def get_governance_overview(
    days: int = Query(30, ge=1, le=365),
    current_user: AuthUser = Depends(get_current_user),
):
    project_id = "api-project-503305938314"
    dataset_id = "ai_data_steward_mvp"
    organization_id = require_current_organization_id(
        current_user
    )

    ai_feedback_metrics = (
        metrics.get_ai_recommendation_feedback_metrics(
            organization_id=organization_id,
            days=days,
        )
    )

    bq_client = bigquery.Client(
        project=project_id
    )

    kpi_sql = f"""
    SELECT *
    FROM `{project_id}.{dataset_id}.V_GOVERNANCE_INTELLIGENCE`
    WHERE organization_id = @organization_id
    """

    dataset_sql = f"""
    SELECT *
    FROM `{project_id}.{dataset_id}.V_GOVERNANCE_DATASET_DETAIL`
    WHERE organization_id = @organization_id
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
    WHERE organization_id = @organization_id
    LIMIT 10
    """

    policy_sql = f"""
    SELECT *
    FROM `{project_id}.{dataset_id}.V_GOVERNANCE_POLICY_ACTIVITY`
    WHERE organization_id = @organization_id
      AND (
        DATE(changed_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL @days DAY)
        OR changed_at IS NULL
      )
    ORDER BY changed_at DESC
    LIMIT 20
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter(
                "organization_id",
                "STRING",
                organization_id,
            ),
            bigquery.ScalarQueryParameter(
                "days",
                "INT64",
                days,
            ),
        ]
    )

    try:
        kpi_rows = [
            dict(row)
            for row in bq_client.query(
                kpi_sql,
                job_config=job_config,
            ).result()
        ]
        dataset_rows = [
            dict(row)
            for row in bq_client.query(
                dataset_sql,
                job_config=job_config,
            ).result()
        ]
        blocker_rows = [
            dict(row)
            for row in bq_client.query(
                blockers_sql,
                job_config=job_config,
            ).result()
        ]
        policy_rows = [
            dict(row)
            for row in bq_client.query(
                policy_sql,
                job_config=job_config,
            ).result()
        ]
        enriched_dataset_rows = []

        import pprint

                   
        if not kpi_rows:
            raise HTTPException(
                status_code=404,
                detail="No governance KPI data found",
            )

        kpi_row = kpi_rows[0]

        enriched_dataset_rows = []

        for dataset_row in dataset_rows:
            enriched_row = dict(dataset_row)

            enriched_row["ai_recommendation"] = (
                build_governance_ai_recommendation(
                    enriched_row,
                    organization_id=organization_id,
                )
            )

            enriched_dataset_rows.append(
                enriched_row
            )


        kpis = GovernanceKPI(
            total_datasets=int(
                kpi_row.get("total_datasets") or 0
            ),
            certified_datasets=int(
                kpi_row.get("certified_datasets") or 0
            ),
            ready_for_certification=int(
                kpi_row.get("ready_for_certification") or 0
            ),
            in_progress_certifications=int(
                kpi_row.get("in_progress_certifications") or 0
            ),
            avg_fair_score=float(
                kpi_row.get("avg_fair_score") or 0
            ),
            open_governance_issues=int(
                kpi_row.get("open_governance_issues") or 0
            ),
            total_checks=int(
                kpi_row.get("total_checks") or 0
            ),
            passed_checks=int(
                kpi_row.get("passed_checks") or 0
            ),
            failed_checks=int(
                kpi_row.get("failed_checks") or 0
            ),
            check_pass_rate=float(
                kpi_row.get("check_pass_rate") or 0
            ),
            active_policies=int(
                kpi_row.get("active_policies") or 0
            ),
            recent_policy_changes_30d=int(
                kpi_row.get("recent_policy_changes_30d") or 0
            ),
        )

        dataset_statuses = [GovernanceDatasetStatus(**row) for row in enriched_dataset_rows]
        top_blockers = [GovernanceBlocker(**row) for row in blocker_rows]
        policy_activity = [GovernancePolicyActivity(**row) for row in policy_rows]

        return GovernanceOverviewResponse(
            organization_id=organization_id,
            kpis=kpis,
            dataset_statuses=dataset_statuses,
            top_blockers=top_blockers,
            policy_activity=policy_activity,
            ai_feedback_metrics=ai_feedback_metrics,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load governance overview: {str(e)}",
        )
@router.post(
    "/dq/profiles/{profile_run_id}/ai-analysis"
)
def analyze_dq_profile(
    profile_run_id: str,
    current_user: AuthUser = Depends(
        get_current_tenant_user
    ),
):
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    try:
        service = (
            DqAiRecommendationService()
        )

        return service.analyze_profile(
            organization_id=organization_id,
            profile_run_id=profile_run_id,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

@router.post(
    "/ai-recommendations/feedback"
)
def submit_ai_recommendation_feedback(
    req: AiRecommendationFeedbackRequest,
    current_user: AuthUser = Depends(
        get_current_tenant_user
    ),
):
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    repository = (
        AiRecommendationFeedbackRepository()
    )

    return repository.save_feedback(
        organization_id=organization_id,
        recommendation_id=(
            req.recommendation_id
        ),
        recommendation_type=(
            req.recommendation_type
        ),
        profile_run_id=req.profile_run_id,
        domain=req.domain,
        rule_id=req.rule_id,
        ai_recommendation=(
            req.ai_recommendation
        ),
        ai_confidence=req.ai_confidence,
        steward_decision=(
            req.steward_decision
        ),
        steward_comment=(
            req.steward_comment
        ),
        override_reason=(
            req.override_reason
        ),
        submitted_by=(
            getattr(
                current_user,
                "email",
                None,
            )
        ),
        source_component=(
            req.source_component
        ),
    )

@router.get(
    "/metrics/dq-overview",
    response_model=DqDashboardResponse,
)
def get_dq_metrics_overview(
    days: int = Query(30, ge=1, le=365),
    domain: Optional[str] = Query(None),
    current_user: AuthUser = Depends(get_current_user),
):
    organization_id = require_current_organization_id(
        current_user
    )

    try:
        result = metrics.get_dq_dashboard_overview(
            days=days,
            domain=domain,
            organization_id=organization_id,
        )

        return {
            "organization_id": organization_id,
            **result,
        }

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception(
            "Failed to load DQ dashboard overview "
            "for organization_id=%s",
            organization_id,
        )

        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc

@router.get(
    "/onboarding/status",
    response_model=OnboardingStatusResponse,
)
def get_onboarding_status(
    current_user: AuthUser = Depends(get_current_user),
) -> OnboardingStatusResponse:
    result = onboarding_service.get_onboarding_status(
        current_user=current_user,
    )

    return OnboardingStatusResponse.model_validate(result)

@router.post(
    "/onboarding/organization",
    response_model=OrganizationOnboardingResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_onboarding_organization(
    request: OrganizationOnboardingRequest,
    current_user: AuthUser = Depends(get_current_user),
) -> OrganizationOnboardingResponse:
    result = onboarding_service.create_organization_and_owner(
        current_user=current_user,
        request=request,
    )

    return OrganizationOnboardingResponse.model_validate(result)

@router.post("/oauth/google/start")
def start_google_oauth(
    request: GoogleOAuthStartRequest,
    current_user: AuthUser = Depends(get_current_user),
):
    result = google_oauth_service.start_authorization(
        connection_id=request.connection_id,
        organization_id=require_current_organization_id(current_user),
        current_user_email=current_user.email,
        project_id=request.project_id,
        return_url=request.return_url,
    )

    return {
        "authorization_url": result.authorization_url,
        "connection_id": result.connection_id,
    }

@router.get("/oauth/google/callback")
def google_oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    try:
        logger.info(
            "Google OAuth callback received. "
            "code_present=%s state_present=%s error=%s",
            bool(code),
            bool(state),
            error,
        )

        result = google_oauth_service.complete_authorization(
            code=code,
            state_token=state,
            oauth_error=error,
            oauth_error_description=error_description,
        )

        logger.info(
            "Google OAuth completed successfully. connection_id=%s",
            result.connection_id,
        )

        return RedirectResponse(
            url=result.return_url,
            status_code=status.HTTP_302_FOUND,
        )

    except HTTPException as exc:
        logger.warning(
            "Google OAuth callback failed with HTTP error: %s",
            exc.detail,
        )

        failure_url = google_oauth_service.build_failure_return_url(
            state_token=state,
            message=str(exc.detail),
        )

        return RedirectResponse(
            url=failure_url,
            status_code=status.HTTP_302_FOUND,
        )

    except Exception as exc:
        logger.exception(
            "Unexpected Google OAuth callback failure."
        )

        failure_url = google_oauth_service.build_failure_return_url(
            state_token=state,
            message=(
                "Google authorization completed, but the connection "
                "could not be activated."
            ),
        )

        return RedirectResponse(
            url=failure_url,
            status_code=status.HTTP_302_FOUND,
        )


@router.post(
    "/connections",
    response_model=EnterpriseConnectionResponse,
)
def create_enterprise_connection(
    request: EnterpriseConnectionCreate,
    current_user: AuthUser = Depends(
        get_current_tenant_user
    ),
) -> EnterpriseConnectionResponse:

    if not current_user.customer_id:
        raise HTTPException(
            status_code=403,
            detail=(
                "A valid customer membership is required "
                "to create an enterprise connection."
            ),
        )

    return connection_service.create_connection(
        request=request,
        organization_id=require_current_organization_id(
            current_user
        ),
        customer_id=current_user.customer_id,
        created_by=str(current_user.email),
    )

@router.get(
    "/connections",
    response_model=EnterpriseConnectionListResponse,
    status_code=status.HTTP_200_OK,
)
def list_enterprise_connections(
    page: int = Query(
        default=1,
        ge=1,
        description="Page number beginning at 1",
    ),
    page_size: int = Query(
        default=25,
        ge=1,
        le=100,
        description="Number of connections returned per page",
    ),
    current_user: AuthUser = Depends(get_current_tenant_user),
    
) -> EnterpriseConnectionListResponse:

    print(
            "CURRENT USER TENANT:",
            repr(current_user.organization_id),
            flush=True,
        )
    return connection_service.list_connections(
        page=page,
        page_size=page_size,
        organization_id=require_current_organization_id(current_user),
        
    )

@router.get(
    "/connections/{connection_id}",
    response_model=EnterpriseConnectionResponse,
    status_code=status.HTTP_200_OK,
)
def get_enterprise_connection(
    connection_id: str,
    current_user: AuthUser = Depends(get_current_tenant_user),
) -> EnterpriseConnectionResponse:
    return connection_service.get_connection(
        connection_id=connection_id,
        organization_id=require_current_organization_id(current_user),
    )

@router.post(
    "/connections/{connection_id}/test",
    response_model=ConnectionTestResponse,
    status_code=status.HTTP_200_OK,
)
def test_enterprise_connection(
    connection_id: str,
    current_user: AuthUser = Depends(get_current_tenant_user),
) -> ConnectionTestResponse:
    return connection_service.test_connection(
        connection_id=connection_id,
        organization_id=require_current_organization_id(current_user),
        tested_by=str(current_user.email),
    )

@router.get(
    "/records/search",
    response_model=RecordSearchResponse,
)
async def search_records(
    domain: str,
    q: str,
    current_user: AuthUser = Depends(
        get_current_tenant_user
    ),
):
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    rows = record_search_service.search_records(
        domain=domain,
        search_text=q,
        organization_id=organization_id,
    )

    return {
        "organization_id": organization_id,
        "results": [
            {
                "organization_id": organization_id,
                "record_id": row["record_id"],
                "mdm_id": row["mdm_id"],
                "domain": row["domain"],
                "display_name": row["display_name"],
                "source_system": row["source_system"],
                "golden_record_flag": row[
                    "golden_record_flag"
                ],
                "human_id": row.get("human_id"),
                "phone_number": row.get(
                    "phone_number"
                ),
                "record": {
                    "member_id": row.get(
                        "member_id"
                    ),
                    "patient_id": row.get(
                        "patient_id"
                    ),
                    "provider_id": row.get(
                        "provider_id"
                    ),
                    "supplier_id": row.get(
                        "supplier_id"
                    ),
                    "product_id": row.get(
                        "product_id"
                    ),

                    # Supplier fields
                    "supplier_name": row.get(
                        "supplier_name"
                    ),
                    "supplier_name_line_1": (
                        row.get(
                            "supplier_name_line_1"
                        )
                        or row.get(
                            "supplier_name"
                        )
                    ),
                    "supplier_name_line_2": (
                        row.get(
                            "supplier_name_line_2"
                        )
                    ),
                    "contact_email": (
                        row.get(
                            "contact_email"
                        )
                        or row.get("email")
                    ),
                    "supplier_address": (
                        row.get(
                            "supplier_address"
                        )
                        or row.get("address")
                    ),

                    "first_name": row.get(
                        "first_name"
                    ),
                    "last_name": row.get(
                        "last_name"
                    ),
                    "email": row.get("email"),
                    "address": row.get(
                        "address"
                    ),
                    "dob": row.get("dob"),
                    "npi": row.get("npi"),
                    "phone_number": row.get(
                        "phone_number"
                    ),
                    "specialty": row.get(
                        "specialty"
                    ),
                    "tax_id": row.get(
                        "tax_id"
                    ),
                    "gtin": row.get("gtin"),
                    "sku": row.get("sku"),
                    "product_name": row.get(
                        "product_name"
                    ),
                    "product_variant": row.get(
                        "product_variant"
                    ),
                    "effective_lot_date": (
                        row.get(
                            "effective_lot_date"
                        )
                    ),
                    "item_category": row.get(
                        "item_category"
                    ),
                    "source_system": row.get(
                        "source_system"
                    ),
                },
            }
            for row in rows
        ],
    }

@router.get(
    "/metrics/dq-rule-suggestions",
    response_model=DqRuleSuggestionsResponse,
)
def get_dq_rule_suggestions(
    days: int = Query(30, ge=1, le=365),
    domain: Optional[str] = Query(None),
    provider: Optional[str] = Query(
        None,
        description="Optional LLM provider override",
    ),
    use_llm: bool = Query(
        True,
        description=(
            "When false, returns deterministic rule suggestions "
            "without calling the LLM"
        ),
    ),
    current_user: AuthUser = Depends(get_current_tenant_user),
    
):
    """
    Generate implementation-ready AI data quality rule suggestions from
    the most recent Data Quality Intelligence metrics.
    """
    try:
        dq_result = metrics.get_dq_dashboard_overview(
            days=days,
            domain=domain,
            organization_id=require_current_organization_id(current_user),
        )

        if not isinstance(dq_result, dict):
            raise HTTPException(
                status_code=500,
                detail="DQ overview returned an unexpected response format.",
            )

        latest_dq_row = dq_result.get("latest")

        # Defensive fallback if the metrics service returns rows but does
        # not explicitly populate latest.
        if not latest_dq_row:
            rows = dq_result.get("rows") or []

            if rows:
                latest_dq_row = rows[-1]

        if not latest_dq_row:
            return DqRuleSuggestionsResponse(
                organization_id=require_current_organization_id(
                    current_user
                ),
                days=days,
                domain=domain,
                dataset_id=None,
                dataset_name=None,
                metric_date=None,
                model_provider=(
                    provider
                    or os.getenv("QUALITY_INTELLIGENCE_LLM_PROVIDER")
                    or os.getenv("GOVERNANCE_LLM_PROVIDER")
                    or "claude"
                ),
                used_llm=False,
                ai_rule_suggestions={
                    "headline": "No DQ recommendations available yet",
                    "summary": (
                        "No Data Quality Intelligence metrics are available "
                        "for this organization in the selected lookback window."
                    ),
                    "priority": "LOW",
                    "rule_strategy": "MIXED",
                    "suggested_rules": [],
                    "recommended_sequence": [],
                    "supporting_evidence": [],
                    "confidence": 0.0,
                },
                generated_at=datetime.now(timezone.utc),
            )

        if hasattr(latest_dq_row, "model_dump"):
            latest_dq_row = latest_dq_row.model_dump()
        elif not isinstance(latest_dq_row, dict):
            latest_dq_row = dict(latest_dq_row)

        suggestions = build_quality_rule_suggestions(
            latest_dq_row,
            organization_id=str(current_user.organization_id),
            provider=provider,
            use_llm=use_llm,
)

        generated_at = datetime.now(timezone.utc)

        return DqRuleSuggestionsResponse(
            days=days,
            domain=domain or latest_dq_row.get("domain"),
            dataset_id=latest_dq_row.get("dataset_id"),
            dataset_name=(
                latest_dq_row.get("dataset_name")
                or latest_dq_row.get("table_name")
                or latest_dq_row.get("asset_name")
            ),
            metric_date=(
                str(latest_dq_row.get("metric_date"))
                if latest_dq_row.get("metric_date") is not None
                else None
            ),
            model_provider=(
                provider
                or os.getenv("QUALITY_INTELLIGENCE_LLM_PROVIDER")
                or os.getenv("GOVERNANCE_LLM_PROVIDER")
                or "claude"
            ),
            used_llm=use_llm,
            ai_rule_suggestions=suggestions,
            generated_at=generated_at,
            organization_id=require_current_organization_id(
                                current_user
            ),
        )

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception(
            "Failed to generate DQ rule suggestions."
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Failed to generate DQ rule suggestions: "
                f"{str(exc)}"
            ),
        ) from exc


@router.get("/policy/config", response_model=PolicyConfigResponse)
def get_policy_config(
    domain: str = Query(...),
    policy_version: Optional[str] = Query(None),
    current_user: AuthUser = Depends(get_current_tenant_user),
):
    try:
        result = policy_engine.get_policy_config_bundle(
            domain=domain,
            policy_version=policy_version,
            organization_id=require_current_organization_id(current_user),
        )
        return PolicyConfigResponse(**result)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load policy config: {str(e)}",
        )


@router.post(
    "/policy/config/draft",
    response_model=PolicyDraftResponse,
)
def save_policy_config_draft(
    payload: PolicyDraftRequest,
    current_user: AuthUser = Depends(
        get_current_tenant_user
    ),
):
    try:
        policy_payload = payload.model_dump()

        organization_id = (
            require_current_organization_id(
                current_user
            )
        )

        entitlement_service.require_domain_entitlement(
            organization_id=organization_id,
            domain=payload.domain,
        )

        policy_payload["created_by"] = (
            current_user.email
        )
        policy_payload["updated_by"] = (
            current_user.email
        )

        result = policy_engine.save_policy_draft(
            policy_payload,
            organization_id=organization_id,
        )

        return PolicyDraftResponse(**result)

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Failed to save policy draft: "
                f"{str(exc)}"
            ),
        ) from exc


@router.post(
    "/policy/config/publish",
    response_model=PolicyPublishResponse,
)
def publish_policy_config(
    payload: PolicyPublishRequest,
    current_user: AuthUser = Depends(
        get_current_tenant_user
    ),
):
    try:
        organization_id = (
            require_current_organization_id(
                current_user
            )
        )

        entitlement_service.require_domain_entitlement(
            organization_id=organization_id,
            domain=payload.domain,
        )

        result = policy_engine.publish_policy_version(
            domain=payload.domain,
            policy_version=payload.policy_version,
            organization_id=organization_id,
            published_by=str(current_user.email),
        )

        return PolicyPublishResponse(**result)

    except HTTPException:
        raise

    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Failed to publish policy config: "
                f"{str(exc)}"
            ),
        ) from exc
    
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
    
@router.get("/entitlements/domains")
def get_enabled_domains(
    current_user: AuthUser = Depends(
        get_current_tenant_user
    ),
):
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    enabled_domains = (
        entitlement_service.list_enabled_domains(
            organization_id=organization_id,
        )
    )

    return {
        "enabled_domains": enabled_domains,
    }

@router.post("/match/explain")
async def match_explain(
    request: Request,
    req: MatchExplainRequest,
    current_user: AuthUser = Depends(get_current_tenant_user),
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

        organization_id = require_current_organization_id(
            current_user
        )

    # ---------------------------------------------------
    # Domain Entitlement Enforcement
    # ---------------------------------------------------
        requested_domain = str(
            req.domain or ""
        ).strip().upper()

        if not requested_domain:
            raise HTTPException(
                status_code=400,
                detail="domain is required",
            )

        entitlement_service.require_domain_entitlement(
            organization_id=organization_id,
            domain=requested_domain,
        )

        # Normalize the request once entitlement has been verified.
        req.domain = requested_domain

        model_provider, model_version = get_model_metadata(
            provider
        )

        llm = LLMService(
            provider=provider
        )

        decision_ctx = policy_engine.build_decision_context(
            req,
            organization_id=organization_id,
        )

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

        domain = (req.domain or "CUSTOMER").upper()

        address_field = {
            "PROVIDER": "provider_address",
            "SUPPLIER": "supplier_address",
            "PATIENT": "patient_address",
        }.get(domain, "address")

        address_a = getattr(req.record_a, address_field, None)
        address_b = getattr(req.record_b, address_field, None)

        logger.info(
        f"[ADDRESS DEBUG] domain={domain} "
        f"address_a={address_a} "
        f"address_b={address_b}"
)

        record_a_address_intelligence = address_service.validate(address_a)
        record_b_address_intelligence = address_service.validate(address_b)

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
            organization_id=require_current_organization_id(
                current_user
            ),
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
                # ---------------------------------------------------
        # Attribute Conflict Risk Evaluation
        # ---------------------------------------------------
        attribute_risk = evaluate_risk(
            domain=domain,
            record_a=req.record_a,
            record_b=req.record_b,
            signal_packets=entity_resolution.get("signals"),
            recommended_action=(
                entity_resolution.get("final_recommended_action")
                or final_recommended_action
            ),
        )

        risk_drivers = attribute_risk.get(
            "risk_drivers",
            [],
)

        attribute_risk_flag = attribute_risk.get("risk_flag", "LOW")
        attribute_risk_score = attribute_risk.get("risk_score", 0)
        attribute_primary_risk_driver = attribute_risk.get(
            "primary_risk_driver",
            "NO_MAJOR_CONFLICT",
        )


        policy_risk_flag = str(
            policy_rec.get("highest_risk_level") or ""
        ).strip().upper()

        if policy_risk_flag not in VALID_RISK_FLAGS:
            policy_risk_flag = "LOW"

        risk_rank = {
            "LOW": 1,
            "MEDIUM": 2,
            "HIGH": 3,
            "CRITICAL": 4,
            "SEVERE": 4,
        }

        response_risk_flag = (
            attribute_risk_flag
            if risk_rank.get(attribute_risk_flag, 1)
            >= risk_rank.get(policy_risk_flag, 1)
            else policy_risk_flag
        )

        primary_risk_driver = (
            attribute_primary_risk_driver
            if attribute_primary_risk_driver != "NO_MAJOR_CONFLICT"
            else policy_rec.get("primary_risk_driver")
        )

        composite_risk_score = max(
            int(policy_rec.get("composite_risk_score") or 0),
            int(attribute_risk_score or 0),
        )


        prompt = build_match_explain_prompt(
            req=req,
            organization_id=organization_id,
            learning_context=decision_ctx.get("learning_context"),
            policy_context=decision_ctx.get("policy_context"),
            policy_recommendation=decision_ctx.get(
                "policy_recommendation"
            ),
            signal_packets=decision_ctx.get("signal_packets"),
)
        try:
           ai_payload = llm.generate_explanation(
            prompt,
            organization_id=require_current_organization_id(
                current_user
            ),
        )
        except Exception as e:
            print("LLM PARSE FAILURE:")
            print(str(e))

            retry_prompt = build_retry_prompt(prompt)
            ai_payload = llm.generate_explanation(
                retry_prompt,
                organization_id=organization_id
                ),
            

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
                "organization_id": require_current_organization_id(current_user),
                "domain": domain,
                "policy_version": policy_version,
                "record_a": req.record_a.model_dump(),
                "record_b": req.record_b.model_dump(),
                "primary_risk_driver": primary_risk_driver,
                "composite_risk_score": composite_risk_score,
                "composite_risk_band": policy_rec.get("composite_risk_band"),
                "risk_flag": response_risk_flag,
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
        ai_insight = generate_text_insight(
                    llm,
                    insight_prompt,
                    organization_id=require_current_organization_id(
                        current_user
    ),
)

        response_obj = MatchExplainResponse(
            explanation_id=explanation_id,
            organization_id=require_current_organization_id(current_user),
            ai_decision=ai_payload["ai_decision"],
            confidence=ai_payload["confidence"],
            risk_flag=response_risk_flag,
            risk_drivers=risk_drivers,
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
            composite_risk_score=composite_risk_score,
            composite_risk_band=policy_rec.get("composite_risk_band"),
            primary_risk_driver=primary_risk_driver,
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
            signal_packets=normalize_entity_resolution_signals(
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
            entity_resolution_signals=normalize_entity_resolution_signals(
            entity_resolution.get("signals")
            ),
            
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
            "organization_id": require_current_organization_id(current_user),
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
            "requested_by": current_user.email,
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

        logger.info(
            "Logging explanation for authenticated user: %s",
            current_user.email,
        )

        bq.log_explanation(
            log_row,
            organization_id=require_current_organization_id(current_user),
        )

                # ---------------------------------------------------
        # Tenant-aware Record Search Index Upsert
        # ---------------------------------------------------
        try:
                record_search_repository.upsert_record(
                    organization_id=organization_id,
                    domain=domain,
                    record=req.record_a.model_dump(),
                    created_by=current_user.email,
                    record_origin="MANUAL_ENTRY",
                )

                record_search_repository.upsert_record(
                    organization_id=organization_id,
                    domain=domain,
                    record=req.record_b.model_dump(),
                    created_by=current_user.email,
                    record_origin="MANUAL_ENTRY",
                )

        except Exception as index_error:
            logger.exception(
                "Record search index upsert failed. "
                "organization_id=%s explanation_id=%s error=%s",
                organization_id,
                explanation_id,
                index_error,
            )

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

@router.post(
    "/entitlements/domains/{domain}",
    response_model=DomainEntitlementResponse,
)
def grant_domain_entitlement(
    domain: str,
    current_user: AuthUser = Depends(
        get_current_tenant_user
    ),
) -> DomainEntitlementResponse:
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    result = (
        entitlement_service.grant_domain_entitlement(
            organization_id=organization_id,
            domain=domain,
            granted_by=str(current_user.email),
        )
    )

    return DomainEntitlementResponse(
        **result
    )

@router.post("/match/feedback", dependencies=[Depends(get_current_tenant_user)])
def match_feedback(req: MatchFeedbackRequest, current_user: AuthUser = Depends(get_current_tenant_user)):
    try:
        if not req.domain:
            raise HTTPException(status_code=400, detail="domain is required")

        if not req.policy_version:
            raise HTTPException(status_code=400, detail="policy_version is required")

        request_id = ensure_request_id(req.request_id)
        decision_id = f"dec_{uuid.uuid4().hex[:12]}"
        submitted_at = utc_now_iso()
        organization_id = require_current_organization_id(current_user)

        
        recommended_action = metrics.get_recommended_action(
                req.explanation_id,
                organization_id=organization_id,
            )

        if recommended_action is None:
            raise HTTPException(status_code=404, detail="explanation_id not found")

        override_flag = "Y" if req.steward_decision != recommended_action else "N"

        row = bq.log_feedback_event(
            explanation_id=req.explanation_id,
            organization_id=organization_id,
            steward_decision=req.steward_decision,
            steward_user=current_user.email,
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
            organization_id=organization_id,
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


@router.get(
    "/metrics/overview",
    response_model=MetricsOverviewResponse,
)
def metrics_overview(
    days: int = Query(7, ge=1, le=365),
    current_user: AuthUser = Depends(get_current_tenant_user),
):
    try:
        data = metrics.overview(
            days,
            organization_id=require_current_organization_id(current_user),
        )

        payload = {
            "days": days,
            "generated_at": utc_now_iso(),
            **data,
        }

        return JSONResponse(content=jsonable_encoder(payload))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))