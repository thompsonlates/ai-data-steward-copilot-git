from typing import Any

from app.services.similarity_engine import (
    SimilarityEngine,
    email_domain_trust,
)


def score_patient(
    *,
    engine: Any,
    record_a: Any,
    record_b: Any,
    domain: str,
    domain_weights: dict[str, float],
    patient_id_score: float | None,
    source_score: float,
    risk_multiplier: float,
    address_match_insight: str | None,
) -> dict[str, Any]:
    """
    Score PATIENT records using existing EntityResolutionEngine helpers.

    Structural extraction only. Existing scoring semantics are intentionally
    preserved.
    """

    signals = []

    patient_first_name_a = (
        getattr(record_a, "patient_first_name", None)
        or getattr(record_a, "first_name", None)
    )
    patient_first_name_b = (
        getattr(record_b, "patient_first_name", None)
        or getattr(record_b, "first_name", None)
    )

    patient_last_name_a = (
        getattr(record_a, "patient_last_name", None)
        or getattr(record_a, "last_name", None)
    )
    patient_last_name_b = (
        getattr(record_b, "patient_last_name", None)
        or getattr(record_b, "last_name", None)
    )

    patient_dob_a = (
        getattr(record_a, "patient_dob", None)
        or getattr(record_a, "dob", None)
    )
    patient_dob_b = (
        getattr(record_b, "patient_dob", None)
        or getattr(record_b, "dob", None)
    )

    patient_email_a = (
        getattr(record_a, "patient_email", None)
        or getattr(record_a, "email", None)
        or ""
    ).strip().lower()

    patient_email_b = (
        getattr(record_b, "patient_email", None)
        or getattr(record_b, "email", None)
        or ""
    ).strip().lower()

    patient_address_a = (
        getattr(record_a, "patient_address", None)
        or getattr(record_a, "address", None)
    )
    patient_address_b = (
        getattr(record_b, "patient_address", None)
        or getattr(record_b, "address", None)
    )

    patient_phone_a = getattr(
        record_a,
        "phone_number",
        None,
    )
    patient_phone_b = getattr(
        record_b,
        "phone_number",
        None,
    )

    phone_score = SimilarityEngine.phone_similarity(
        patient_phone_a,
        patient_phone_b,
    )

    name_score = engine._name_similarity(
        patient_first_name_a,
        patient_last_name_a,
        patient_first_name_b,
        patient_last_name_b,
    )

    dob_a_valid = engine._is_valid_dob(patient_dob_a)
    dob_b_valid = engine._is_valid_dob(patient_dob_b)

    if patient_dob_a and not dob_a_valid:
        dob_score = 0.0
        dob_detail = (
            f"Record A DOB value '{patient_dob_a}' "
            "is invalid for a DOB field."
        )
    elif patient_dob_b and not dob_b_valid:
        dob_score = 0.0
        dob_detail = (
            f"Record B DOB value '{patient_dob_b}' "
            "is invalid for a DOB field."
        )
    elif dob_a_valid and dob_b_valid:
        dob_score = engine._dob_match(
            patient_dob_a,
            patient_dob_b,
        )
        dob_detail = engine._dob_detail(
            patient_dob_a,
            patient_dob_b,
        )
    else:
        dob_score = 0.0
        dob_detail = "DOB evidence is unavailable or incomplete."

    patient_email_similarity = SimilarityEngine.email_similarity(
        patient_email_a,
        patient_email_b,
    )

    patient_domain_a = (
        patient_email_a.split("@")[1]
        if "@" in patient_email_a
        else ""
    )
    patient_domain_b = (
        patient_email_b.split("@")[1]
        if "@" in patient_email_b
        else ""
    )

    patient_domain_trust_score = min(
        email_domain_trust(patient_domain_a),
        email_domain_trust(patient_domain_b),
    )

    email_score = min(
        patient_email_similarity * patient_domain_trust_score,
        1.0,
    )

    email_match_level = "DIFFERENT"
    if email_score >= 0.99:
        email_match_level = "EXACT"
    elif email_score >= 0.90:
        email_match_level = "SIMILAR"
    elif email_score >= 0.70:
        email_match_level = "FUZZY"

    address_score = SimilarityEngine.address_similarity(
        patient_address_a,
        patient_address_b,
    )

    patient_identity_score = (
        patient_id_score
        if patient_id_score is not None
        else 0.0
    )

    phone_weight = (
        domain_weights.get("phone_match", 0.0)
        if phone_score is not None
        else 0.0
    )

    phone_score = phone_score or 0.0

    match_score = (
        patient_identity_score * 0.20
        + phone_score * phone_weight
        + dob_score * 0.25
        + name_score * 0.20
        + address_score * 0.15
        + email_score * 0.10
    )

    total_weight = (
        0.20
        + phone_weight
        + 0.25
        + 0.20
        + 0.15
        + 0.10
    )

    match_score /= total_weight

    match_score = round(
        min(match_score, 0.95),
        4,
    )

    # Preserve existing branch behavior.
    raw_entity_score = match_score

    if patient_identity_score >= 0.90:
        match_score = min(
            match_score + 0.10,
            1.0,
        )

    if (
        patient_identity_score >= 0.90
        and dob_score == 1.0
        and name_score >= 0.90
        and address_score >= 0.95
    ):
        match_score = min(
            max(match_score, 0.90),
            0.92,
        )

    elif (
        patient_identity_score >= 0.90
        and dob_score == 1.0
        and name_score >= 0.85
    ):
        match_score = min(
            max(match_score, 0.86),
            0.90,
        )

    match_score = min(
        match_score,
        0.92,
    )

    raw_entity_score = round(
        match_score * risk_multiplier,
        4,
    )

    signals.append(
        engine._build_signal(
            "patient_id_match",
            patient_id_score,
            domain_weights.get("patient_id_match", 0.0),
            engine._patient_id_detail(
                getattr(record_a, "patient_id", None),
                getattr(record_b, "patient_id", None),
            ),
            signal_type="deterministic",
        )
    )

    signal_score = (
        phone_score
        if phone_score is not None
        else 0.5
    )

    signal_weight = (
        domain_weights.get("phone_match", 0.0)
        if phone_score is not None
        else 0.0
    )

    signals.append(
        engine._build_signal(
            "phone_match",
            signal_score,
            signal_weight,
            engine._phone_detail(
                patient_phone_a,
                patient_phone_b,
            ),
            signal_type="deterministic",
        )
    )

    signals.append(
        engine._build_signal(
            "dob_match",
            dob_score,
            domain_weights.get("dob_match", 0.0),
            dob_detail,
            signal_type="deterministic",
        )
    )

    signals.append(
        engine._build_signal(
            "name_similarity",
            name_score,
            domain_weights.get("name_similarity", 0.0),
            engine._name_detail(
                patient_first_name_a,
                patient_last_name_a,
                patient_first_name_b,
                patient_last_name_b,
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
                patient_email_a,
                patient_email_b,
            ),
            match_level=email_match_level,
            domain_trust=patient_domain_trust_score,
            signal_type="probabilistic",
        )
    )

    signals.append(
        engine._build_signal(
            "address_similarity",
            address_score,
            domain_weights.get("address_similarity", 0.0),
            engine._address_detail(
                patient_address_a,
                patient_address_b,
                address_score,
                domain,
                address_match_insight,
            ),
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
        "patient_id_score": patient_id_score,
        "phone_score": phone_score,
        "dob_score": dob_score,
        "name_score": name_score,
        "email_score": email_score,
        "address_score": address_score,
    }