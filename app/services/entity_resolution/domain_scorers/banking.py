from typing import Any

from app.services.similarity_engine import (
    SimilarityEngine,
    email_domain_trust,
)


def score_banking(
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
    Score BANKING records using existing EntityResolutionEngine helpers.

    Structural extraction only. Existing scoring semantics are intentionally
    preserved.
    """

    signals = []

    account_id_a = getattr(record_a, "account_id", None)
    account_id_b = getattr(record_b, "account_id", None)

    banking_customer_id_a = getattr(
        record_a,
        "banking_customer_id",
        None,
    )
    banking_customer_id_b = getattr(
        record_b,
        "banking_customer_id",
        None,
    )

    sap_bp_id_a = getattr(
        record_a,
        "sap_business_partner_id",
        None,
    )
    sap_bp_id_b = getattr(
        record_b,
        "sap_business_partner_id",
        None,
    )

    routing_number_a = getattr(
        record_a,
        "routing_number",
        None,
    )
    routing_number_b = getattr(
        record_b,
        "routing_number",
        None,
    )

    account_last4_a = getattr(
        record_a,
        "account_number_last4",
        None,
    )
    account_last4_b = getattr(
        record_b,
        "account_number_last4",
        None,
    )

    institution_name_a = getattr(
        record_a,
        "institution_name",
        None,
    )
    institution_name_b = getattr(
        record_b,
        "institution_name",
        None,
    )

    account_type_a = getattr(
        record_a,
        "account_type",
        None,
    )
    account_type_b = getattr(
        record_b,
        "account_type",
        None,
    )

    currency_a = getattr(
        record_a,
        "currency",
        None,
    )
    currency_b = getattr(
        record_b,
        "currency",
        None,
    )

    banking_email_a = (
        getattr(record_a, "email", None) or ""
    ).strip().lower()

    banking_email_b = (
        getattr(record_b, "email", None) or ""
    ).strip().lower()

    banking_phone_a = getattr(
        record_a,
        "phone_number",
        None,
    )
    banking_phone_b = getattr(
        record_b,
        "phone_number",
        None,
    )

    # -------------------------------------------------
    # Deterministic banking identifiers
    # -------------------------------------------------

    account_id_score = (
        1.0
        if engine._normalize_text(account_id_a)
        and engine._normalize_text(account_id_a)
        == engine._normalize_text(account_id_b)
        else 0.0
    )

    customer_id_score = (
        1.0
        if engine._normalize_text(banking_customer_id_a)
        and engine._normalize_text(banking_customer_id_a)
        == engine._normalize_text(banking_customer_id_b)
        else 0.0
    )

    sap_business_partner_id_score = (
        1.0
        if engine._normalize_text(sap_bp_id_a)
        and engine._normalize_text(sap_bp_id_a)
        == engine._normalize_text(sap_bp_id_b)
        else 0.0
    )

    routing_number_score = (
        1.0
        if engine._normalize_text(routing_number_a)
        and engine._normalize_text(routing_number_a)
        == engine._normalize_text(routing_number_b)
        else 0.0
    )

    account_number_last4_score = (
        1.0
        if engine._normalize_text(account_last4_a)
        and engine._normalize_text(account_last4_a)
        == engine._normalize_text(account_last4_b)
        else 0.0
    )

    # -------------------------------------------------
    # Probabilistic / contextual banking evidence
    # -------------------------------------------------

    institution_name_score = engine._similarity(
        institution_name_a,
        institution_name_b,
    )

    account_type_score = (
        1.0
        if engine._normalize_text(account_type_a)
        and engine._normalize_text(account_type_a)
        == engine._normalize_text(account_type_b)
        else 0.0
    )

    currency_score = (
        1.0
        if engine._normalize_text(currency_a)
        and engine._normalize_text(currency_a)
        == engine._normalize_text(currency_b)
        else 0.0
    )

    phone_score = SimilarityEngine.phone_similarity(
        banking_phone_a,
        banking_phone_b,
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

    banking_email_similarity = (
        SimilarityEngine.email_similarity(
            banking_email_a,
            banking_email_b,
        )
    )

    banking_domain_a = (
        banking_email_a.split("@")[1]
        if "@" in banking_email_a
        else ""
    )

    banking_domain_b = (
        banking_email_b.split("@")[1]
        if "@" in banking_email_b
        else ""
    )

    banking_domain_trust_score = min(
        email_domain_trust(banking_domain_a),
        email_domain_trust(banking_domain_b),
    )

    email_score = min(
        banking_email_similarity
        * banking_domain_trust_score,
        1.0,
    )

    banking_email_match_level = "DIFFERENT"

    if email_score >= 0.99:
        banking_email_match_level = "EXACT"
    elif email_score >= 0.90:
        banking_email_match_level = "SIMILAR"
    elif email_score >= 0.70:
        banking_email_match_level = "FUZZY"

    # -------------------------------------------------
    # Composite banking score
    # -------------------------------------------------

    match_score = (
        account_id_score
        * domain_weights.get("account_id_match", 0.0)
        + customer_id_score
        * domain_weights.get("customer_id_match", 0.0)
        + sap_business_partner_id_score
        * domain_weights.get(
            "sap_business_partner_id_match",
            0.0,
        )
        + routing_number_score
        * domain_weights.get("routing_number_match", 0.0)
        + account_number_last4_score
        * domain_weights.get(
            "account_number_last4_match",
            0.0,
        )
        + institution_name_score
        * domain_weights.get(
            "institution_name_similarity",
            0.0,
        )
        + account_type_score
        * domain_weights.get("account_type_match", 0.0)
        + currency_score
        * domain_weights.get("currency_match", 0.0)
        + email_score
        * domain_weights.get("email_match", 0.0)
        + phone_score_value
        * domain_weights.get("phone_match", 0.0)
        + source_score
        * domain_weights.get("source_trust", 0.0)
        + learning_score
        * domain_weights.get("steward_learning", 0.0)
    )

    # Preserve existing reinforcement rules.
    # Avoid allowing last-four alone to drive a high-confidence match.
    if (
        account_id_score == 1.0
        and routing_number_score == 1.0
    ):
        match_score = max(match_score, 0.95)

    elif (
        customer_id_score == 1.0
        and sap_business_partner_id_score == 1.0
    ):
        match_score = max(match_score, 0.92)

    elif (
        routing_number_score == 1.0
        and account_number_last4_score == 1.0
        and institution_name_score >= 0.90
    ):
        match_score = max(match_score, 0.82)

    match_score = min(match_score, 1.0)

    # -------------------------------------------------
    # Explainability signals
    # -------------------------------------------------

    signals.append(
        engine._build_signal(
            "account_id_match",
            account_id_score,
            domain_weights.get("account_id_match", 0.0),
            engine._account_id_detail(
                account_id_a,
                account_id_b,
            ),
            signal_type="deterministic",
        )
    )

    signals.append(
        engine._build_signal(
            "customer_id_match",
            customer_id_score,
            domain_weights.get("customer_id_match", 0.0),
            engine._customer_id_detail(
                banking_customer_id_a,
                banking_customer_id_b,
            ),
            signal_type="deterministic",
        )
    )

    signals.append(
        engine._build_signal(
            "sap_business_partner_id_match",
            sap_business_partner_id_score,
            domain_weights.get(
                "sap_business_partner_id_match",
                0.0,
            ),
            engine._sap_business_partner_id_detail(
                sap_bp_id_a,
                sap_bp_id_b,
            ),
            signal_type="deterministic",
        )
    )

    signals.append(
        engine._build_signal(
            "routing_number_match",
            routing_number_score,
            domain_weights.get(
                "routing_number_match",
                0.0,
            ),
            engine._routing_number_detail(
                routing_number_a,
                routing_number_b,
            ),
            signal_type="deterministic",
        )
    )

    signals.append(
        engine._build_signal(
            "account_number_last4_match",
            account_number_last4_score,
            domain_weights.get(
                "account_number_last4_match",
                0.0,
            ),
            engine._account_last4_detail(
                account_last4_a,
                account_last4_b,
            ),
            signal_type="deterministic",
        )
    )

    signals.append(
        engine._build_signal(
            "institution_name_similarity",
            institution_name_score,
            domain_weights.get(
                "institution_name_similarity",
                0.0,
            ),
            (
                "Compared financial institution names "
                f"'{institution_name_a or 'UNKNOWN'}' and "
                f"'{institution_name_b or 'UNKNOWN'}'."
            ),
            signal_type="probabilistic",
        )
    )

    signals.append(
        engine._build_signal(
            "account_type_match",
            account_type_score,
            domain_weights.get("account_type_match", 0.0),
            (
                "Compared banking account types "
                f"'{account_type_a or 'UNKNOWN'}' and "
                f"'{account_type_b or 'UNKNOWN'}'."
            ),
            signal_type="deterministic",
        )
    )

    signals.append(
        engine._build_signal(
            "currency_match",
            currency_score,
            domain_weights.get("currency_match", 0.0),
            (
                "Compared account currencies "
                f"'{currency_a or 'UNKNOWN'}' and "
                f"'{currency_b or 'UNKNOWN'}'."
            ),
            signal_type="deterministic",
        )
    )

    signals.append(
        engine._build_signal(
            "email_match",
            email_score,
            domain_weights.get("email_match", 0.0),
            engine._email_detail(
                banking_email_a,
                banking_email_b,
            ),
            match_level=banking_email_match_level,
            domain_trust=banking_domain_trust_score,
            signal_type="probabilistic",
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
                banking_phone_a,
                banking_phone_b,
            ),
            signal_type="deterministic",
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
        "account_id_score": account_id_score,
        "customer_id_score": customer_id_score,
        "sap_business_partner_id_score": (
            sap_business_partner_id_score
        ),
        "routing_number_score": routing_number_score,
        "account_number_last4_score": (
            account_number_last4_score
        ),
        "institution_name_score": institution_name_score,
        "account_type_score": account_type_score,
        "currency_score": currency_score,
        "email_score": email_score,
        "phone_score": phone_score,
    }