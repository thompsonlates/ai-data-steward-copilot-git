from typing import Any


def score_organization(
    *,
    engine: Any,
    record_a: Any,
    record_b: Any,
    domain_weights: dict[str, float],
    source_score: float,
    learning_score: float,
    risk_multiplier: float,
) -> dict[str, Any]:
    """
    Score ORGANIZATION records using the existing EntityResolutionEngine
    helpers. Structural extraction only; scoring semantics remain unchanged.
    """

    signals = []

    organization_entity_id_a = getattr(
        record_a, "organization_entity_id", None
    )
    organization_entity_id_b = getattr(
        record_b, "organization_entity_id", None
    )
    organization_code_a = getattr(
        record_a, "organization_code", None
    )
    organization_code_b = getattr(
        record_b, "organization_code", None
    )
    organization_name_a = getattr(
        record_a, "organization_name", None
    )
    organization_name_b = getattr(
        record_b, "organization_name", None
    )
    organization_type_a = getattr(
        record_a, "organization_type", None
    )
    organization_type_b = getattr(
        record_b, "organization_type", None
    )
    parent_organization_id_a = getattr(
        record_a, "parent_organization_id", None
    )
    parent_organization_id_b = getattr(
        record_b, "parent_organization_id", None
    )
    organization_status_a = getattr(
        record_a, "organization_status", None
    )
    organization_status_b = getattr(
        record_b, "organization_status", None
    )

    organization_entity_id_score = engine._exact_match_score(
        organization_entity_id_a,
        organization_entity_id_b,
    )
    organization_code_score = engine._exact_match_score(
        organization_code_a,
        organization_code_b,
    )
    organization_name_score = engine._similarity(
        organization_name_a,
        organization_name_b,
    )
    organization_type_score = engine._exact_match_score(
        organization_type_a,
        organization_type_b,
    )
    parent_organization_id_score = engine._exact_match_score(
        parent_organization_id_a,
        parent_organization_id_b,
    )
    organization_status_score = engine._exact_match_score(
        organization_status_a,
        organization_status_b,
    )

    match_score = (
        organization_entity_id_score
        * domain_weights.get("organization_entity_id_match", 0.0)
        + organization_code_score
        * domain_weights.get("organization_code_match", 0.0)
        + organization_name_score
        * domain_weights.get("organization_name_similarity", 0.0)
        + organization_type_score
        * domain_weights.get("organization_type_match", 0.0)
        + parent_organization_id_score
        * domain_weights.get("parent_organization_id_match", 0.0)
        + organization_status_score
        * domain_weights.get("organization_status_match", 0.0)
        + source_score
        * domain_weights.get("source_trust", 0.0)
        + learning_score
        * domain_weights.get("steward_learning", 0.0)
    )

    if organization_entity_id_score == 1.0:
        match_score = max(match_score, 0.96)
    elif (
        organization_code_score == 1.0
        and organization_name_score >= 0.90
    ):
        match_score = max(match_score, 0.92)
    elif (
        organization_name_score >= 0.95
        and organization_type_score == 1.0
    ):
        match_score = max(match_score, 0.85)

    match_score = min(match_score, 1.0)

    for signal_name, score_value, weight_key, detail, signal_type in [
        (
            "organization_entity_id_match",
            organization_entity_id_score,
            "organization_entity_id_match",
            f"Compared Organization entity IDs "
            f"'{organization_entity_id_a or 'UNKNOWN'}' and "
            f"'{organization_entity_id_b or 'UNKNOWN'}'.",
            "deterministic",
        ),
        (
            "organization_code_match",
            organization_code_score,
            "organization_code_match",
            f"Compared organization codes "
            f"'{organization_code_a or 'UNKNOWN'}' and "
            f"'{organization_code_b or 'UNKNOWN'}'.",
            "deterministic",
        ),
        (
            "organization_name_similarity",
            organization_name_score,
            "organization_name_similarity",
            f"Compared organization names "
            f"'{organization_name_a or 'UNKNOWN'}' and "
            f"'{organization_name_b or 'UNKNOWN'}'.",
            "probabilistic",
        ),
        (
            "organization_type_match",
            organization_type_score,
            "organization_type_match",
            f"Compared organization types "
            f"'{organization_type_a or 'UNKNOWN'}' and "
            f"'{organization_type_b or 'UNKNOWN'}'.",
            "deterministic",
        ),
        (
            "parent_organization_id_match",
            parent_organization_id_score,
            "parent_organization_id_match",
            f"Compared parent Organization IDs "
            f"'{parent_organization_id_a or 'UNKNOWN'}' and "
            f"'{parent_organization_id_b or 'UNKNOWN'}'.",
            "deterministic",
        ),
        (
            "organization_status_match",
            organization_status_score,
            "organization_status_match",
            f"Compared organization statuses "
            f"'{organization_status_a or 'UNKNOWN'}' and "
            f"'{organization_status_b or 'UNKNOWN'}'.",
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
        "name_score": organization_name_score,
        "raw_entity_score": raw_entity_score,
        "organization_entity_id_score": organization_entity_id_score,
        "organization_code_score": organization_code_score,
        "organization_name_score": organization_name_score,
        "organization_type_score": organization_type_score,
        "parent_organization_id_score": parent_organization_id_score,
        "organization_status_score": organization_status_score,
    }