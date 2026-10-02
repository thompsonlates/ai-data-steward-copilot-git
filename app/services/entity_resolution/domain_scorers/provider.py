from typing import Any

from app.services.similarity_engine import (
    SimilarityEngine,
    email_domain_trust,
)


def score_provider(
    *,
    engine: Any,
    record_a: Any,
    record_b: Any,
    domain: str,
    domain_weights: dict[str, float],
    provider_id_score: float,
    npi_score: float,
    source_score: float,
    learning_score: float,
    risk_multiplier: float,
    address_match_insight: str | None,
) -> dict[str, Any]:
    """
    Score PROVIDER records using existing EntityResolutionEngine helpers.

    Structural extraction only. Existing scoring semantics are intentionally
    preserved.
    """

    signals = []

    provider_email_score = 0.0

    provider_email_match_level = "DIFFERENT"
    provider_domain_trust_score = 0.0

    provider_email_a = (
        getattr(record_a, "provider_email", None)
        or getattr(record_a, "email", None)
        or ""
    ).strip().lower()

    provider_email_b = (
        getattr(record_b, "provider_email", None)
        or getattr(record_b, "email", None)
        or ""
    ).strip().lower()

    if provider_email_a and provider_email_b:
        provider_email_similarity = SimilarityEngine.email_similarity(
            provider_email_a,
            provider_email_b,
        )

        provider_email_score = round(
            provider_email_similarity,
            4,
        )

        provider_domain_a = ""
        provider_domain_b = ""

        if "@" in provider_email_a:
            provider_domain_a = provider_email_a.split("@")[1]

        if "@" in provider_email_b:
            provider_domain_b = provider_email_b.split("@")[1]

        provider_domain_trust_a = email_domain_trust(
            provider_domain_a
        )
        provider_domain_trust_b = email_domain_trust(
            provider_domain_b
        )

        provider_domain_trust_score = min(
            provider_domain_trust_a,
            provider_domain_trust_b,
        )

        provider_email_score = min(
            provider_email_similarity
            * provider_domain_trust_score,
            1.0,
        )

        if provider_email_score >= 0.99:
            provider_email_match_level = "EXACT"
        elif provider_email_score >= 0.90:
            provider_email_match_level = "SIMILAR"
        elif provider_email_score >= 0.70:
            provider_email_match_level = "FUZZY"

    provider_name_a = " ".join(
        [
            getattr(record_a, "provider_first_name", "") or "",
            getattr(record_a, "provider_last_name", "") or "",
        ]
    ).strip()

    provider_name_b = " ".join(
        [
            getattr(record_b, "provider_first_name", "") or "",
            getattr(record_b, "provider_last_name", "") or "",
        ]
    ).strip()

    name_score = engine._similarity(
        provider_name_a,
        provider_name_b,
    )

    # Provider specialty similarity.
    specialty_score = engine._similarity(
        getattr(record_a, "specialty", None),
        getattr(record_b, "specialty", None),
    )

    provider_address_a = (
        getattr(record_a, "provider_address", None)
        or getattr(record_a, "address", None)
    )

    provider_address_b = (
        getattr(record_b, "provider_address", None)
        or getattr(record_b, "address", None)
    )

    address_score = engine._similarity(
        provider_address_a,
        provider_address_b,
    )

    provider_phone_a = getattr(
        record_a,
        "phone_number",
        None,
    )
    provider_phone_b = getattr(
        record_b,
        "phone_number",
        None,
    )

    phone_score = SimilarityEngine.phone_similarity(
        provider_phone_a,
        provider_phone_b,
    )

    phone_weight = (
        domain_weights.get("phone_match", 0.0)
        if phone_score is not None
        else 0.0
    )

    phone_score_value = (
        phone_score
        if phone_score is not None
        else 0.0
    )

    match_score = (
        provider_id_score
        * domain_weights.get("provider_id_match", 0.0)
        + npi_score
        * domain_weights.get("npi_match", 0.0)
        + phone_score_value
        * domain_weights.get("phone_match", 0.0)
        + name_score
        * domain_weights.get("name_similarity", 0.0)
        + provider_email_score
        * domain_weights.get("provider_email_match", 0.0)
        + address_score
        * domain_weights.get("address_similarity", 0.0)
        + specialty_score
        * domain_weights.get("specialty_similarity", 0.0)
        + source_score
        * domain_weights.get("source_trust", 0.0)
        + learning_score
        * domain_weights.get("steward_learning", 0.0)
    )

    signals.append(
        engine._build_signal(
            "provider_id_match",
            provider_id_score,
            domain_weights.get("provider_id_match", 0.0),
            engine._provider_id_detail(
                getattr(record_a, "provider_id", None),
                getattr(record_b, "provider_id", None),
            ),
        )
    )

    signals.append(
        engine._build_signal(
            "provider_email_match",
            provider_email_score,
            domain_weights.get("provider_email_match", 0.0),
            engine._email_detail(
                provider_email_a,
                provider_email_b,
            ),
            match_level=provider_email_match_level,
            domain_trust=provider_domain_trust_score,
            signal_type="probabilistic",
        )
    )

    signals.append(
        engine._build_signal(
            "address_similarity",
            address_score,
            domain_weights.get("address_similarity", 0.0),
            engine._address_detail(
                provider_address_a,
                provider_address_b,
                address_score,
                domain,
                address_match_insight,
            ),
            signal_type="probabilistic",
        )
    )

    signals.append(
        engine._build_signal(
            "phone_match",
            phone_score,
            phone_weight,
            engine._phone_detail(
                provider_phone_a,
                provider_phone_b,
            ),
            signal_type="probabilistic",
        )
    )

    signals.append(
        engine._build_signal(
            "npi_match",
            npi_score,
            domain_weights.get("npi_match", 0.0),
            engine._npi_detail(
                getattr(record_a, "npi", None),
                getattr(record_b, "npi", None),
            ),
            signal_type="deterministic",
        )
    )

    signals.append(
        engine._build_signal(
            "name_similarity",
            name_score,
            domain_weights.get("name_similarity", 0.0),
            engine._name_detail(
                (
                    getattr(
                        record_a,
                        "provider_first_name",
                        None,
                    )
                    or getattr(record_a, "first_name", None)
                ),
                (
                    getattr(
                        record_b,
                        "provider_first_name",
                        None,
                    )
                    or getattr(record_b, "first_name", None)
                ),
                (
                    getattr(
                        record_a,
                        "provider_last_name",
                        None,
                    )
                    or getattr(record_a, "last_name", None)
                ),
                (
                    getattr(
                        record_b,
                        "provider_last_name",
                        None,
                    )
                    or getattr(record_b, "last_name", None)
                ),
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

    # Preserve existing composite signal reinforcement.
    if phone_score is None or phone_score != 0.0:
        if (
            provider_email_score >= 1.0
            and name_score >= 0.85
            and address_score >= 0.90
        ):
            match_score += 0.15

        elif (
            name_score >= 0.90
            and address_score >= 0.90
        ):
            match_score += 0.10

        elif (
            provider_email_score >= 1.0
            and name_score >= 0.80
        ):
            match_score += 0.08

    match_score = min(
        match_score,
        1.0,
    )

    raw_entity_score = round(
        match_score * risk_multiplier,
        4,
    )

    return {
        "signals": signals,
        "match_score": match_score,
        "raw_entity_score": raw_entity_score,
        "provider_id_score": provider_id_score,
        "npi_score": npi_score,
        "phone_score": phone_score,
        "name_score": name_score,
        "provider_email_score": provider_email_score,
        "address_score": address_score,
        "specialty_score": specialty_score,
    }
