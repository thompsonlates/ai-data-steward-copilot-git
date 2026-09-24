"""Behavior-preserving route extraction from the former monolithic routes.py."""

from app.api.route_dependencies import *
from app.api.route_dependencies import (
    _get_cached_dq_rule_suggestions,
    _set_cached_dq_rule_suggestions,
    _get_onedrive_connector,
)

router = APIRouter()


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


@router.get(
    "/governance/policy-compliance",
    response_model=GovernanceDqPolicyComplianceResponse,
)
def get_governance_policy_compliance(
    profile_run_id: str = Query(
        ...,
        min_length=1,
        description=(
            "Profile run whose deterministic DQ rule evidence "
            "should be evaluated against governance policy."
        ),
    ),
    domain: str = Query(
        ...,
        min_length=1,
        description="Business domain, for example PRODUCT.",
    ),
    policy_id: Optional[str] = Query(
        default=None,
        min_length=1,
        description=(
            "Optional policy ID. When omitted, the active policy "
            "for the authenticated tenant and domain is resolved."
        ),
    ),
    policy_version: Optional[str] = Query(
        default=None,
        min_length=1,
        description=(
            "Optional policy version. When omitted, the active "
            "policy version is resolved."
        ),
    ),
    current_user: AuthUser = Depends(
        get_current_user
    ),
) -> GovernanceDqPolicyComplianceResponse:
    """
    Return deterministic Governance Intelligence evidence for the
    Policy -> DQ Rule -> Profile Execution compliance chain.

    organization_id is always derived from the authenticated user.
    The client cannot provide or override tenant context.
    """
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    normalized_profile_run_id = str(
        profile_run_id or ""
    ).strip()

    normalized_domain = str(
        domain or ""
    ).strip().upper()

    normalized_policy_id = (
        str(policy_id).strip()
        if policy_id
        else None
    )

    normalized_policy_version = (
        str(policy_version).strip()
        if policy_version
        else None
    )

    try:
        result = (
            build_dq_policy_governance_context(
                organization_id=organization_id,
                profile_run_id=(
                    normalized_profile_run_id
                ),
                domain=normalized_domain,
                policy_id=normalized_policy_id,
                policy_version=(
                    normalized_policy_version
                ),
            )
        )

        return (
            GovernanceDqPolicyComplianceResponse(
                **result
            )
        )

    except ValueError as exc:
        message = str(exc)

        if (
            "not found" in message.lower()
            or "no active policy" in message.lower()
        ):
            raise HTTPException(
                status_code=(
                    status.HTTP_404_NOT_FOUND
                ),
                detail=message,
            ) from exc

        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
            ),
            detail=message,
        ) from exc

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception(
            "Failed to load governance DQ policy "
            "compliance. organization_id=%s "
            "domain=%s profile_run_id=%s",
            organization_id,
            normalized_domain,
            normalized_profile_run_id,
        )

        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "Unable to load governance policy "
                "compliance evidence."
            ),
        ) from exc
