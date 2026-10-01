from typing import Any

from app.services.similarity_engine import SimilarityEngine


def score_location(
    *,
    engine: Any,
    record_a: Any,
    record_b: Any,
    domain: str,
    domain_weights: dict[str, float],
    source_score: float,
    learning_score: float,
    risk_multiplier: float,
    address_match_insight: str | None,
) -> dict[str, Any]:
    """
    Score LOCATION records using the existing EntityResolutionEngine
    helpers. This is a structural extraction only; scoring semantics
    remain unchanged.
    """

    signals = []

    location_id_a = getattr(record_a, "location_id", None)
    location_id_b = getattr(record_b, "location_id", None)
    site_id_a = getattr(record_a, "site_id", None)
    site_id_b = getattr(record_b, "site_id", None)
    location_code_a = getattr(record_a, "location_code", None)
    location_code_b = getattr(record_b, "location_code", None)
    location_name_a = getattr(record_a, "location_name", None)
    location_name_b = getattr(record_b, "location_name", None)

    location_address_a = (
        getattr(record_a, "location_address", None)
        or getattr(record_a, "address", None)
    )
    location_address_b = (
        getattr(record_b, "location_address", None)
        or getattr(record_b, "address", None)
    )

    parent_location_id_a = getattr(record_a, "parent_location_id", None)
    parent_location_id_b = getattr(record_b, "parent_location_id", None)

    location_org_id_a = getattr(record_a, "organization_entity_id", None)
    location_org_id_b = getattr(record_b, "organization_entity_id", None)

    location_id_score = engine._exact_match_score(
        location_id_a,
        location_id_b,
    )
    site_id_score = engine._exact_match_score(
        site_id_a,
        site_id_b,
    )
    location_code_score = engine._exact_match_score(
        location_code_a,
        location_code_b,
    )
    location_name_score = engine._similarity(
        location_name_a,
        location_name_b,
    )

    address_score = SimilarityEngine.address_similarity(
        location_address_a,
        location_address_b,
    )

    parent_location_id_score = engine._exact_match_score(
        parent_location_id_a,
        parent_location_id_b,
    )

    organization_entity_id_available = bool(
        engine._normalize_text(location_org_id_a)
        and engine._normalize_text(location_org_id_b)
    )

    organization_entity_id_score = (
        engine._exact_match_score(
            location_org_id_a,
            location_org_id_b,
        )
        if organization_entity_id_available
        else None
    )

    match_score = (
        location_id_score
        * domain_weights.get("location_id_match", 0.0)
        + site_id_score
        * domain_weights.get("site_id_match", 0.0)
        + location_code_score
        * domain_weights.get("location_code_match", 0.0)
        + location_name_score
        * domain_weights.get("location_name_similarity", 0.0)
        + address_score
        * domain_weights.get("address_similarity", 0.0)
        + parent_location_id_score
        * domain_weights.get("parent_location_id_match", 0.0)
        + (
            organization_entity_id_score
            if organization_entity_id_score is not None
            else 0.0
        )
        * domain_weights.get("organization_entity_id_match", 0.0)
        + source_score
        * domain_weights.get("source_trust", 0.0)
        + learning_score
        * domain_weights.get("steward_learning", 0.0)
    )

    if location_id_score == 1.0 and site_id_score == 1.0:
        match_score = max(match_score, 0.97)
    elif site_id_score == 1.0 and address_score >= 0.95:
        match_score = max(match_score, 0.92)
    elif location_code_score == 1.0 and address_score >= 0.95:
        match_score = max(match_score, 0.88)

    match_score = min(match_score, 1.0)

    for signal_name, score_value, weight_key, detail, signal_type in [
        (
            "location_id_match",
            location_id_score,
            "location_id_match",
            f"Compared Location IDs '{location_id_a or 'UNKNOWN'}' "
            f"and '{location_id_b or 'UNKNOWN'}'.",
            "deterministic",
        ),
        (
            "site_id_match",
            site_id_score,
            "site_id_match",
            f"Compared governed Site IDs '{site_id_a or 'UNKNOWN'}' "
            f"and '{site_id_b or 'UNKNOWN'}'.",
            "deterministic",
        ),
        (
            "location_code_match",
            location_code_score,
            "location_code_match",
            f"Compared location codes '{location_code_a or 'UNKNOWN'}' "
            f"and '{location_code_b or 'UNKNOWN'}'.",
            "deterministic",
        ),
        (
            "location_name_similarity",
            location_name_score,
            "location_name_similarity",
            f"Compared location names '{location_name_a or 'UNKNOWN'}' "
            f"and '{location_name_b or 'UNKNOWN'}'.",
            "probabilistic",
        ),
        (
            "address_similarity",
            address_score,
            "address_similarity",
            engine._address_detail(
                location_address_a,
                location_address_b,
                address_score,
                domain,
                address_match_insight,
            ),
            "probabilistic",
        ),
        (
            "parent_location_id_match",
            parent_location_id_score,
            "parent_location_id_match",
            f"Compared parent Location IDs "
            f"'{parent_location_id_a or 'UNKNOWN'}' "
            f"and '{parent_location_id_b or 'UNKNOWN'}'.",
            "deterministic",
        ),
        (
            "organization_entity_id_match",
            organization_entity_id_score,
            "organization_entity_id_match",
            (
                f"Compared governing Organization entity IDs "
                f"'{location_org_id_a}' and '{location_org_id_b}'."
                if organization_entity_id_available
                else
                "Governing Organization entity ID was not provided "
                "for both records; this signal is unavailable."
            ),
            "deterministic",
        ),
        (
            "source_trust",
            source_score,
            "source_trust",
            engine._source_detail(
                record_a.source_system,
                record_b.source_system,
            ),
            "probabilistic",
        ),
    ]:
        signals.append(
            engine._build_signal(
                signal_name,
                score_value,
                domain_weights.get(weight_key, 0.0),
                detail,
                signal_type=signal_type,
            )
        )

    raw_entity_score = round(
        match_score * risk_multiplier,
        4,
    )

    return {
        "signals": signals,
        "match_score": match_score,
        "name_score": location_name_score,
        "address_score": address_score,
        "raw_entity_score": raw_entity_score,
    }