from typing import Any, Optional

from app.services.entity_resolution.scoring_helpers import safe_score


def resolve_weights(
    policy_config: Optional[dict[str, Any]],
    domain_weights: dict[str, float],
) -> dict[str, float]:
    weights = domain_weights.copy()

    if not policy_config:
        return weights

    policy_weights = (
        policy_config.get("signal_weights")
        or policy_config.get("weights")
    )

    if not isinstance(policy_weights, dict):
        return weights

    for key in weights:
        value = policy_weights.get(key)
        if value is None:
            continue
        try:
            weights[key] = max(0.0, min(float(value), 1.0))
        except (TypeError, ValueError):
            continue

    total = sum(weights.values())

    if total > 0:
        weights = {
            key: round(value / total, 6)
            for key, value in weights.items()
        }

    return weights


def resolve_source_trust_map(
    policy_config: Optional[dict[str, Any]],
    default_source_trust_map: dict[str, float],
) -> dict[str, float]:
    source_trust_map = default_source_trust_map.copy()

    if not policy_config:
        return source_trust_map

    policy_map = policy_config.get("source_trust_map")

    if not isinstance(policy_map, dict):
        return source_trust_map

    for key, value in policy_map.items():
        try:
            source_trust_map[str(key).upper()] = max(
                0.0,
                min(float(value), 1.0),
            )
        except (TypeError, ValueError):
            continue

    return source_trust_map


def resolve_automation_thresholds(
    policy_config: Optional[dict[str, Any]],
    default_thresholds: dict[str, int],
) -> dict[str, int]:
    thresholds = default_thresholds.copy()

    if not policy_config:
        return thresholds

    policy_thresholds = policy_config.get("automation_thresholds") or {}

    for key in thresholds:
        value = policy_thresholds.get(key)

        if value is None:
            continue

        try:
            thresholds[key] = max(0, min(int(value), 100))
        except (TypeError, ValueError):
            continue

    return thresholds


def resolve_signal_tone_thresholds(
    policy_config: Optional[dict[str, Any]],
    default_thresholds: dict[str, float],
) -> dict[str, float]:
    thresholds = default_thresholds.copy()

    if not policy_config:
        return thresholds

    policy_thresholds = policy_config.get("signal_tone_thresholds") or {}

    for key in thresholds:
        value = policy_thresholds.get(key)

        if value is None:
            continue

        try:
            thresholds[key] = max(
                0.0,
                min(float(value), 1.0),
            )
        except (TypeError, ValueError):
            continue

    return thresholds


def resolve_readiness_label_thresholds(
    policy_config: Optional[dict[str, Any]],
    default_thresholds: dict[str, int],
) -> dict[str, int]:
    thresholds = default_thresholds.copy()

    if not policy_config:
        return thresholds

    policy_thresholds = (
        policy_config.get("readiness_label_thresholds")
        or {}
    )

    for key in thresholds:
        value = policy_thresholds.get(key)

        if value is None:
            continue

        try:
            thresholds[key] = max(0, min(int(value), 100))
        except (TypeError, ValueError):
            continue

    return thresholds


def signal_tone(
    signal_name: str,
    score: float,
    thresholds: dict[str, float],
) -> str:
    if score is None:
        return "neutral"

    safe_value = safe_score(score)

    strong_positive = thresholds.get("strong_positive", 0.92)
    positive = thresholds.get("positive", 0.85)
    neutral = thresholds.get("neutral", 0.70)
    neutral_source = thresholds.get("neutral_source", 0.65)

    # Deterministic identity anchors
    if signal_name in {
        "supplier_id_match",
        "product_id_match",
        "provider_id_match",
        "patient_id_match",
        "member_id_match",
        "tax_id_match",
        "gtin_match",
        "item_category_match",
        "npi_match",
        "human_id_match",
        "dob_match",
    }:
        if float(safe_value or 0) >= 1.0:
            return "positive"

        return "warning"

    # Email identity signal
    if signal_name in {
        "email_match",
        "contact_email_match",
    }:
        if safe_value >= strong_positive:
            return "positive"

        if safe_value >= neutral:
            return "neutral"

        return "warning"

    # Name similarity
    if signal_name == "name_similarity":
        if safe_value >= positive:
            return "positive"

        if safe_value >= neutral:
            return "neutral"

        return "warning"

    # Address / Attribute matching
    if signal_name in {
        "address_similarity",
        "attribute_similarity",
    }:
        if safe_value >= positive:
            return "positive"

        if safe_value >= neutral:
            return "neutral"

        return "warning"

    # Source trust
    if signal_name == "source_trust":
        if safe_value >= 0.85:
            return "positive"

        if safe_value >= neutral_source:
            return "neutral"

        return "warning"

    return "neutral"


def learning_adjustment(
    override_rate_estimate: Optional[float],
) -> float:
    if override_rate_estimate is None:
        return 0.0

    try:
        rate = max(
            0.0,
            min(float(override_rate_estimate), 1.0),
        )
    except (TypeError, ValueError):
        return 0.0

    if rate >= 0.30:
        return -0.10

    if rate >= 0.15:
        return -0.06

    if rate >= 0.05:
        return -0.03

    return 0.02


def risk_multiplier(
    composite_risk_score: Optional[int],
) -> float:
    if composite_risk_score is None:
        return 1.0

    try:
        risk = max(
            0,
            min(int(composite_risk_score), 100),
        )
    except (TypeError, ValueError):
        return 1.0

    return round(
        1.0 - (risk / 100.0) * 0.45,
        4,
    )


def automation_tier(
    confidence_score: int,
    thresholds: dict[str, int],
) -> str:
    if confidence_score >= thresholds["auto_merge_ready_min"]:
        return "AUTO_MERGE_READY"

    if confidence_score >= thresholds["suggested_merge_min"]:
        return "SUGGESTED_MERGE"

    if confidence_score >= thresholds["review_advised_min"]:
        return "REVIEW_ADVISED"

    return "DO_NOT_AUTOMATE"


def recommended_action_from_tier(
    automation_tier_value: str,
) -> str:
    tier = (automation_tier_value or "").upper()

    if tier == "AUTO_MERGE_READY":
        return "AUTO_MERGE"

    if tier == "SUGGESTED_MERGE":
        return "APPROVE_MERGE"

    if tier == "REVIEW_ADVISED":
        return "REVIEW_REQUIRED"

    return "BLOCK_MERGE"


def automation_readiness_score(
    decision_confidence_score: int,
    composite_risk_score: Optional[int],
    final_recommended_action: str,
) -> int:
    risk_score = 100 - max(
        0,
        min(int(composite_risk_score or 50), 100),
    )

    action_bonus_map = {
        "AUTO_MERGE": 100,
        "APPROVE_MERGE": 75,
        "REVIEW_REQUIRED": 45,
        "REVIEW": 45,
        "BLOCK_MERGE": 10,
        "REJECT_MERGE": 10,
    }

    action_bonus = action_bonus_map.get(
        (final_recommended_action or "").upper(),
        40,
    )

    readiness = (
        decision_confidence_score * 0.55
        + risk_score * 0.35
        + action_bonus * 0.10
    )

    return max(
        0,
        min(round(readiness), 100),
    )


def automation_readiness_label(
    readiness_score: int,
    thresholds: dict[str, int],
) -> str:
    if readiness_score >= thresholds["high"]:
        return "HIGH_AUTOMATION_READINESS"

    if readiness_score >= thresholds["moderate"]:
        return "MODERATE_AUTOMATION_READINESS"

    return "LOW_AUTOMATION_READINESS"


def automation_policy_status(
    final_recommended_action: str,
) -> str:
    action = (final_recommended_action or "").upper()

    if action == "AUTO_MERGE":
        return "ELIGIBLE_FOR_AUTO_MERGE"

    if action == "APPROVE_MERGE":
        return "APPROVAL_READY"

    if action in {"REVIEW_REQUIRED", "REVIEW"}:
        return "MANUAL_REVIEW_REQUIRED"

    if action in {"BLOCK_MERGE", "REJECT_MERGE"}:
        return "BLOCKED_BY_POLICY"

    return "MANUAL_REVIEW_REQUIRED"


def estimated_false_positive_risk(
    decision_confidence_score: int,
    composite_risk_score: Optional[int],
) -> int:
    risk_component = max(
        0,
        min(int(composite_risk_score or 50), 100),
    )

    confidence_inverse = 100 - max(
        0,
        min(decision_confidence_score, 100),
    )

    blended = round(
        (risk_component * 0.65)
        + (confidence_inverse * 0.35)
    )

    return max(
        0,
        min(blended, 100),
    )
