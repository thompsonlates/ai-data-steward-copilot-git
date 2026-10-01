from typing import Any


def score_product(
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
    Score PRODUCT records using existing EntityResolutionEngine helpers.

    Structural extraction only. Existing scoring semantics are intentionally
    preserved.
    """

    signals = []

    product_id_a = getattr(record_a, "product_id", None)
    product_id_b = getattr(record_b, "product_id", None)

    product_name_a = getattr(record_a, "product_name", None)
    product_name_b = getattr(record_b, "product_name", None)

    product_variant_a = getattr(record_a, "product_variant", None)
    product_variant_b = getattr(record_b, "product_variant", None)

    item_category_a = getattr(record_a, "item_category", None)
    item_category_b = getattr(record_b, "item_category", None)

    gtin_a = getattr(record_a, "gtin", None)
    gtin_b = getattr(record_b, "gtin", None)

    sku_a = getattr(record_a, "sku", None)
    sku_b = getattr(record_b, "sku", None)

    product_id_score = engine._similarity(
        product_id_a,
        product_id_b,
    )

    product_name_score = engine._similarity(
        product_name_a,
        product_name_b,
    )

    gtin_score = (
        1.0
        if engine._normalize_text(gtin_a)
        and engine._normalize_text(gtin_a)
        == engine._normalize_text(gtin_b)
        else 0.0
    )

    sku_score = engine._similarity(
        sku_a,
        sku_b,
    )

    item_category_score = (
        1.0
        if engine._normalize_text(item_category_a)
        and engine._normalize_text(item_category_a)
        == engine._normalize_text(item_category_b)
        else 0.0
    )

    product_variant_score = engine._similarity(
        product_variant_a,
        product_variant_b,
    )

    attribute_similarity_score = engine._product_attribute_similarity(
        record_a,
        record_b,
    )

    # Preserve existing PRODUCT behavior: product name is the
    # domain-specific name signal.
    name_score = product_name_score

    match_score = (
        product_id_score
        * domain_weights.get("product_id_match", 0.0)
        + gtin_score
        * domain_weights.get("gtin_match", 0.0)
        + sku_score
        * domain_weights.get("sku_match", 0.0)
        + product_name_score
        * domain_weights.get("name_similarity", 0.0)
        + product_variant_score
        * domain_weights.get("product_variant_similarity", 0.0)
        + item_category_score
        * domain_weights.get("item_category_similarity", 0.0)
        + attribute_similarity_score
        * domain_weights.get("attribute_similarity", 0.0)
        + source_score
        * domain_weights.get("source_trust", 0.0)
        + learning_score
        * domain_weights.get("steward_learning", 0.0)
    )

    match_score = round(match_score, 4)

    signals.append(
        engine._build_signal(
            "product_id_match",
            product_id_score,
            domain_weights.get("product_id_match", 0.0),
            engine._product_id_detail(
                product_id_a,
                product_id_b,
            ),
            signal_type="deterministic",
        )
    )

    signals.append(
        engine._build_signal(
            "gtin_match",
            gtin_score,
            domain_weights.get("gtin_match", 0.0),
            engine._gtin_detail(
                gtin_a,
                gtin_b,
            ),
            signal_type="deterministic",
        )
    )

    signals.append(
        engine._build_signal(
            "sku_match",
            sku_score,
            domain_weights.get("sku_match", 0.0),
            engine._sku_detail(
                sku_a,
                sku_b,
            ),
            signal_type="deterministic",
        )
    )

    signals.append(
        engine._build_signal(
            "name_similarity",
            product_name_score,
            domain_weights.get("name_similarity", 0.0),
            engine._name_detail(
                product_name_a,
                product_variant_a,
                product_name_b,
                product_variant_b,
            ),
            signal_type="probabilistic",
        )
    )

    signals.append(
        engine._build_signal(
            "product_variant_similarity",
            product_variant_score,
            domain_weights.get(
                "product_variant_similarity",
                0.0,
            ),
            engine._product_variant_detail(
                product_variant_a,
                product_variant_b,
            ),
            signal_type="probabilistic",
        )
    )

    signals.append(
        engine._build_signal(
            "item_category_similarity",
            item_category_score,
            domain_weights.get(
                "item_category_similarity",
                0.0,
            ),
            engine._item_category_detail(
                item_category_a,
                item_category_b,
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
        "product_id_score": product_id_score,
        "gtin_score": gtin_score,
        "sku_score": sku_score,
        "name_score": name_score,
        "product_variant_score": product_variant_score,
        "item_category_score": item_category_score,
        "attribute_similarity_score": attribute_similarity_score,
    }