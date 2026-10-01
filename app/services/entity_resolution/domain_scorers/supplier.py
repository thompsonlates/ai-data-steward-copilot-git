from typing import Any

from app.services.similarity_engine import (
    SimilarityEngine,
    email_domain_trust,
)


def score_supplier(
    *,
    engine: Any,
    record_a: Any,
    record_b: Any,
    domain: str,
    domain_weights: dict[str, float],
    supplier_id_score: float,
    tax_id_score: float,
    supplier_id_a: Any,
    supplier_id_b: Any,
    address_score: float,
    source_score: float,
    risk_multiplier: float,
    address_match_insight: str | None,
) -> dict[str, Any]:
    """
    Score SUPPLIER records using existing EntityResolutionEngine helpers.

    Structural extraction only. Existing scoring semantics are intentionally
    preserved.
    """

    signals = []

    # Preserve existing initial assignment even though the value is
    # subsequently recalculated below.
    supplier_address_similarity_score = address_score

    supplier_email_a = (
        getattr(record_a, "contact_email", None)
        or getattr(record_a, "email", None)
        or ""
    ).strip().lower()

    supplier_email_b = (
        getattr(record_b, "contact_email", None)
        or getattr(record_b, "email", None)
        or ""
    ).strip().lower()

    supplier_email_similarity = SimilarityEngine.email_similarity(
        supplier_email_a,
        supplier_email_b,
    )

    supplier_domain_a = (
        supplier_email_a.split("@")[1]
        if "@" in supplier_email_a
        else ""
    )

    supplier_domain_b = (
        supplier_email_b.split("@")[1]
        if "@" in supplier_email_b
        else ""
    )

    supplier_domain_trust_score = min(
        email_domain_trust(supplier_domain_a),
        email_domain_trust(supplier_domain_b),
    )

    email_score = min(
        supplier_email_similarity * supplier_domain_trust_score,
        1.0,
    )

    supplier_email_match_level = "DIFFERENT"

    if email_score >= 0.99:
        supplier_email_match_level = "EXACT"
    elif email_score >= 0.90:
        supplier_email_match_level = "SIMILAR"
    elif email_score >= 0.70:
        supplier_email_match_level = "FUZZY"

    supplier_name_a = (
        getattr(record_a, "supplier_name", None)
        or getattr(record_a, "first_name", None)
        or ""
    )

    supplier_name_b = (
        getattr(record_b, "supplier_name", None)
        or getattr(record_b, "first_name", None)
        or ""
    )

    name_score = engine._similarity(
        supplier_name_a,
        supplier_name_b,
    )

    supplier_address_a = (
        getattr(record_a, "supplier_address", None)
        or getattr(record_a, "address", None)
        or ""
    )

    supplier_address_b = (
        getattr(record_b, "supplier_address", None)
        or getattr(record_b, "address", None)
        or ""
    )

    supplier_address_similarity_score = (
        SimilarityEngine.address_similarity(
            supplier_address_a,
            supplier_address_b,
        )
    )

    # Preserve existing duplicate supplier-name calculation.
    supplier_name_a = (
        getattr(record_a, "supplier_name", None)
        or getattr(record_a, "first_name", None)
        or ""
    )

    supplier_name_b = (
        getattr(record_b, "supplier_name", None)
        or getattr(record_b, "first_name", None)
        or ""
    )

    name_score = engine._similarity(
        supplier_name_a,
        supplier_name_b,
    )

    address_score = supplier_address_similarity_score

    # Preserve existing hard-coded SUPPLIER composite weights.
    match_score = (
        supplier_id_score * 0.40
        + tax_id_score * 0.25
        + name_score * 0.10
        + email_score * 0.15
        + address_score * 0.05
        + source_score * 0.05
    )

    match_score = round(match_score, 2)

    # Preserve existing authoritative identifier reinforcement.
    if supplier_id_score == 1.0 and tax_id_score == 1.0:
        match_score = max(match_score, 0.98)

    elif supplier_id_score == 1.0:
        match_score = max(match_score, 0.94)

    elif tax_id_score == 1.0:
        match_score = max(match_score, 0.90)

    signals.append(
        engine._build_signal(
            "supplier_id_match",
            supplier_id_score,
            domain_weights.get("supplier_id_match", 0.0),
            engine._supplier_id_detail(
                supplier_id_a,
                supplier_id_b,
            ),
        )
    )

    signals.append(
        engine._build_signal(
            "name_similarity",
            name_score,
            domain_weights.get("name_similarity", 0.0),
            engine._name_detail(
                supplier_name_a,
                None,
                supplier_name_b,
                None,
            ),
            signal_type="probabilistic",
        )
    )

    signals.append(
        engine._build_signal(
            "address_similarity",
            supplier_address_similarity_score,
            domain_weights.get("address_similarity", 0.0),
            engine._address_detail(
                supplier_address_a,
                supplier_address_b,
                supplier_address_similarity_score,
                domain,
                address_match_insight,
            ),
            signal_type="probabilistic",
        )
    )

    signals.append(
        engine._build_signal(
            "tax_id_match",
            tax_id_score,
            domain_weights.get("tax_id_match", 0.0),
            engine._tax_id_detail(
                getattr(record_a, "tax_id", None),
                getattr(record_b, "tax_id", None),
            ),
        )
    )

    signals.append(
        engine._build_signal(
            "contact_email_match",
            email_score,
            domain_weights.get("contact_email_match", 0.0),
            engine._contact_email_detail(
                supplier_email_a,
                supplier_email_b,
            ),
            match_level=supplier_email_match_level,
            domain_trust=supplier_domain_trust_score,
            signal_type="probabilistic",
        )
    )

    signals.append(
        engine._build_signal(
            "source_trust",
            source_score,
            domain_weights.get("source_trust", 0.0),
            engine._source_detail(
                record_a.source_system,
                record_b.source_system,
            ),
        )
    )

    raw_entity_score = round(
        match_score * risk_multiplier,
        4,
    )

    return {
        "signals": signals,
        "match_score": match_score,
        "raw_entity_score": raw_entity_score,
        "supplier_id_score": supplier_id_score,
        "tax_id_score": tax_id_score,
        "name_score": name_score,
        "email_score": email_score,
        "address_score": address_score,
    }