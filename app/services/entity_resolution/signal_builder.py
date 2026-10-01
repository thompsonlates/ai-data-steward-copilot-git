from typing import Any, Callable

from app.services.similarity_engine import SimilarityEngine


def build_signal(
    *,
    name: str,
    score: float | None,
    weight: float,
    detail: str,
    safe_score_fn: Callable[[Any], float],
    signal_impact_fn: Callable[[Any], str],
    match_level: str | None = None,
    signal_type: str = "probabilistic",
    **kwargs,
) -> dict[str, Any]:
    """
    Build a normalized explainability signal.

    Structural extraction from EntityResolutionEngine only.
    Existing scoring and band semantics are intentionally preserved.
    """

    evidence_available = score is not None
    safe_score = (
        safe_score_fn(score)
        if evidence_available
        else 0.0
    )

    detail = (
        detail
        or f"{name.replace('_', ' ').title()} was evaluated."
    )

    weighted_score = (
        round(safe_score * weight, 4)
        if evidence_available
        else 0.0
    )

    signal_band = (
        "UNAVAILABLE"
        if not evidence_available
        else (
            match_level
            or (
                "EXACT"
                if name
                in {
                    "product_id_match",
                    "sku_match",
                    "patient_id_match",
                    "provider_id_match",
                    "human_id_match",
                }
                and safe_score >= 0.999
                else "SIMILAR"
                if name
                in {
                    "product_id_match",
                    "sku_match",
                    "patient_id_match",
                    "provider_id_match",
                }
                and safe_score >= 0.85
                else "DIFFERENT"
                if name
                in {
                    "product_id_match",
                    "sku_match",
                }
                else SimilarityEngine.similarity_band(
                    safe_score
                )
            )
        )
    )

    return {
        "signal_name": name,
        "signal_score": round(safe_score, 4),
        "signal_weight": round(weight, 4),
        "weighted_score": weighted_score,
        "evidence_available": evidence_available,
        "detail": detail,
        "signal_band": signal_band,
        "signal_impact": signal_impact_fn(safe_score),
        "signal_type": signal_type,
        **kwargs,
    }

def build_signal_contributions(
    *,
    signal_scores: dict[str, float],
    signal_weights: dict[str, float],
    safe_score_fn: Callable[[Any], float],
) -> list[dict[str, float | str]]:
    """
    Build weighted signal contributions.

    Existing EntityResolutionEngine behavior is intentionally preserved.
    """
    contributions: list[dict[str, float | str]] = []

    for signal_key, score in signal_scores.items():
        weight = safe_score_fn(
            signal_weights.get(signal_key, 0.0)
        )

        # Ignore unavailable evidence
        if score is None or weight == 0:
            continue

        safe_score = safe_score_fn(score)
        contribution = safe_score * weight

        contributions.append(
            {
                "signal_name": signal_key,
                "score": round(safe_score, 4),
                "weight": round(weight, 4),
                "contribution": round(contribution, 4),
                "contribution_pct": round(
                    contribution * 100,
                    2,
                ),
            }
        )

    return contributions


def primary_signal_from_contributions(
    signal_contributions: list[dict[str, Any]],
) -> str | None:
    """
    Return the signal with the largest weighted contribution.

    Existing ordering/tie behavior is intentionally preserved.
    """
    if not signal_contributions:
        return None

    strongest = max(
        signal_contributions,
        key=lambda item: float(
            item.get("contribution") or 0.0
        ),
    )

    return str(strongest.get("signal_name"))