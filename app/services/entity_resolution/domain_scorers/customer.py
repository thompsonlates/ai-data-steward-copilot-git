from difflib import SequenceMatcher
from typing import Any

from app.services.similarity_engine import SimilarityEngine


def score_customer(
    *,
    engine: Any,
    record_a: Any,
    record_b: Any,
    domain: str,
    domain_weights: dict[str, float],
    member_id_score: float,
    record_a_id: Any,
    record_b_id: Any,
    dob_score: float,
    email_score: float,
    email_match_level: str,
    domain_trust_score: float,
    address_score: float,
    source_score: float,
    learning_score: float,
    risk_multiplier: float,
    address_match_insight: str | None,
) -> dict[str, Any]:
    """
    Score CUSTOMER records using existing EntityResolutionEngine helpers.

    Structural extraction only. Existing scoring semantics are intentionally
    preserved.
    """

    signals = []

    customer_full_name_a = (
        getattr(record_a, "full_name", None)
        or " ".join(
            [
                getattr(record_a, "first_name", "") or "",
                getattr(record_a, "last_name", "") or "",
            ]
        ).strip()
    )

    customer_full_name_b = (
        getattr(record_b, "full_name", None)
        or " ".join(
            [
                getattr(record_b, "first_name", "") or "",
                getattr(record_b, "last_name", "") or "",
            ]
        ).strip()
    )

    full_name_a = customer_full_name_a.strip().lower()
    full_name_b = customer_full_name_b.strip().lower()

    name_similarity_score = SequenceMatcher(
        None,
        full_name_a,
        full_name_b,
    ).ratio()

    customer_phone_a = getattr(
        record_a,
        "phone_number",
        None,
    )

    customer_phone_b = getattr(
        record_b,
        "phone_number",
        None,
    )

    phone_score = SimilarityEngine.phone_similarity(
        customer_phone_a,
        customer_phone_b,
    )

    phone_weight = (
        domain_weights.get(
            "phone_match",
            0.0,
        )
        if phone_score is not None
        else 0.0
    )

    phone_score_value = (
        phone_score
        if phone_score is not None
        else 0.0
    )

    match_score = (
        member_id_score
        * domain_weights.get(
            "member_id_match",
            0.0,
        )
        + phone_score_value
        * domain_weights.get(
            "phone_match",
            0.0,
        )
        + name_similarity_score
        * domain_weights.get(
            "name_similarity",
            0.0,
        )
        + dob_score
        * domain_weights.get(
            "dob_match",
            0.0,
        )
        + email_score
        * domain_weights.get(
            "email_match",
            0.0,
        )
        + address_score
        * domain_weights.get(
            "address_similarity",
            0.0,
        )
        + source_score
        * domain_weights.get(
            "source_trust",
            0.0,
        )
        + learning_score
        * domain_weights.get(
            "steward_learning",
            0.0,
        )
    )

    # Preserve existing CUSTOMER reinforcement rules.
    if (
        dob_score == 1.0
        and address_score >= 0.95
        and name_similarity_score >= 0.85
    ):
        match_score = max(match_score, 0.82)

    elif (
        dob_score == 1.0
        and name_similarity_score >= 0.90
    ):
        match_score = max(match_score, 0.78)

    signals.append(
        engine._build_signal(
            "name_similarity",
            name_similarity_score,
            domain_weights.get("name_similarity", 0.0),
            (
                "Names align closely across records."
                if name_similarity_score >= 0.90
                else "Names partially align."
            ),
        )
    )

    signals.append(
        engine._build_signal(
            "member_id_match",
            member_id_score,
            domain_weights.get("member_id_match", 0.0),
            engine._member_id_detail(
                record_a_id,
                record_b_id,
            ),
        )
    )

    signals.append(
        engine._build_signal(
            "phone_match",
            (
                phone_score
                if phone_score is not None
                else 0.5
            ),
            phone_weight,
            engine._phone_detail(
                customer_phone_a,
                customer_phone_b,
            ),
            signal_type="deterministic",
        )
    )

    signals.append(
        engine._build_signal(
            "dob_match",
            dob_score,
            domain_weights.get("dob_match", 0.0),
            engine._dob_detail(
                record_a.dob,
                record_b.dob,
            ),
        )
    )

    signals.append(
        engine._build_signal(
            "address_similarity",
            address_score,
            domain_weights.get("address_similarity", 0.0),
            engine._address_detail(
                record_a.address,
                record_b.address,
                address_score,
                domain,
                address_match_insight,
            ),
            signal_type="probabilistic",
        )
    )

    signals.append(
        engine._build_signal(
            "email_match",
            email_score,
            domain_weights.get("email_match", 0.0),
            engine._email_detail(
                record_a.email,
                record_b.email,
            ),
            match_level=email_match_level,
            domain_trust=domain_trust_score,
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
            signal_type="probabilistic",
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
        "member_id_score": member_id_score,
        "phone_score": phone_score,
        "name_score": name_similarity_score,
        "dob_score": dob_score,
        "email_score": email_score,
        "address_score": address_score,
    }