from typing import Any, Optional

from rapidfuzz.distance import JaroWinkler


def safe_score(value: Optional[float], default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return default


def normalize_text(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def similarity(a: Optional[str], b: Optional[str]) -> float:
    a_norm = normalize_text(a)
    b_norm = normalize_text(b)

    if not a_norm or not b_norm:
        return 0.0

    return round(
        JaroWinkler.similarity(a_norm, b_norm),
        4,
    )


def name_similarity(
    first_a: Optional[str],
    last_a: Optional[str],
    first_b: Optional[str],
    last_b: Optional[str],
) -> float:
    first_score = similarity(first_a, first_b)
    last_score = similarity(last_a, last_b)
    return round((first_score * 0.4) + (last_score * 0.6), 4)


def exact_match_score(value_a: Any, value_b: Any) -> float:
    normalized_a = normalize_text(value_a)
    normalized_b = normalize_text(value_b)

    return (
        1.0
        if normalized_a and normalized_a == normalized_b
        else 0.0
    )


def dob_match(dob_a: Optional[str], dob_b: Optional[str]) -> float:
    a = normalize_text(dob_a)
    b = normalize_text(dob_b)

    if not a or not b:
        return 0.0

    return 1.0 if a == b else 0.0


def member_id_match(
    member_id_a: Optional[str],
    member_id_b: Optional[str],
) -> float:
    a = normalize_text(member_id_a)
    b = normalize_text(member_id_b)

    if not a or not b:
        return 0.0

    return 1.0 if a == b else 0.0


def normalize_phone(value: Optional[str]) -> str:
    digits = "".join(
        character
        for character in str(value or "")
        if character.isdigit()
    )

    # Normalize US country code: 1-904-555-1212 -> 9045551212
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]

    return digits

def phone_score(
    phone_a: Optional[str],
    phone_b: Optional[str],
) -> Optional[float]:
    normalized_a = normalize_phone(phone_a)
    normalized_b = normalize_phone(phone_b)

    # Missing phone evidence is unavailable, not a mismatch.
    if not normalized_a or not normalized_b:
        return None

    return 1.0 if normalized_a == normalized_b else 0.0


def source_trust_score(
    source_a: Optional[str],
    source_b: Optional[str],
    source_trust_map: dict[str, float],
) -> float:
    trust_a = source_trust_map.get((source_a or "").upper(), 0.65)
    trust_b = source_trust_map.get((source_b or "").upper(), 0.65)

    avg_trust = (trust_a + trust_b) / 2.0
    distance_penalty = abs(trust_a - trust_b) * 0.25

    return round(
        max(0.0, min(avg_trust - distance_penalty, 1.0)),
        4,
    )
