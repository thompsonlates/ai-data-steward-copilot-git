from __future__ import annotations

import re

from rapidfuzz.distance import JaroWinkler
from rapidfuzz.fuzz import WRatio

JUNK_VALUES = {
    "",
    "na",
    "n/a",
    "none",
    "null",
    "unknown",
    "test",
    "testing",
    "sample",
    "TEST",
    "TESTING"
    "dummy",
    "xxx",
    "xxxx",
    "asdf",
    "qwerty",
    "placeholder",
    "abcdefj",
    "123r23r23",
}


class SimilarityEngine:
    """
    Stateless similarity utilities.

    This module does not read or write tenant data and therefore should not
    accept organization_id or perform tenant filtering. Tenant isolation must
    happen before records are passed into the similarity engine.
    """

    @staticmethod
    def normalize_text(
        value: str | None,
    ) -> str:
        if not value:
            return ""

        normalized = value.lower().strip()
        normalized = re.sub(
            r"[^a-z0-9@ ]+",
            "",
            normalized,
        )

        return normalized

    @staticmethod
    def jaro_similarity(
        value_a: str | None,
        value_b: str | None,
    ) -> float:
        a = SimilarityEngine.normalize_text(
            value_a
        )
        b = SimilarityEngine.normalize_text(
            value_b
        )

        if not a or not b:
            return 0.0

        score = WRatio(a, b) / 100.0

        return round(score, 4)

    @staticmethod
    def normalize_address(
        value: str | None,
    ) -> str:
        if not value:
            return ""

        normalized = f" {value.lower().strip()} "

        replacements = {
            " street ": " st ",
            " avenue ": " ave ",
            " boulevard ": " blvd ",
            " road ": " rd ",
            " drive ": " dr ",
        }

        for old, new in replacements.items():
            normalized = normalized.replace(
                old,
                new,
            )

        normalized = re.sub(
            r"[^a-z0-9 ]",
            "",
            normalized,
        )

        return re.sub(
            r"\s+",
            " ",
            normalized,
        ).strip()

    @staticmethod
    def field_evidence_quality(
        field_name: str,
        value: str | None,
    ) -> float:
        text = str(value or "").strip().lower()

        if not text:
            return 0.0

        if text in JUNK_VALUES:
            return 0.0

        compact = re.sub(
            r"\s+",
            "",
            text,
        )

        # Extremely low-information values.
        if len(compact) <= 2:
            return 0.05

        if len(compact) == 3:
            return 0.15

        # Repeated character / low-diversity junk.
        unique_ratio = (
            len(set(compact))
            / max(len(compact), 1)
        )

        if len(compact) >= 3 and unique_ratio < 0.35:
            return 0.10

        normalized_field = (
            str(field_name or "")
            .strip()
            .lower()
        )

        if normalized_field in {
            "email",
            "contact_email",
        }:
            if not re.fullmatch(
                r"[^@\s]+@[^@\s]+\.[^@\s]+",
                text,
            ):
                return 0.0

        if normalized_field in {
            "tax_id",
            "supplier_tax_id",
            "ein",
            "tin",
        }:
            identifier = re.sub(
                r"[^a-z0-9]",
                "",
                text,
            )

            if len(identifier) < 5:
                return 0.05

        if normalized_field in {
            "phone",
            "contact_phone",
        }:
            digits = re.sub(
                r"\D",
                "",
                text,
            )

            if len(digits) < 7:
                return 0.05

        if normalized_field in {
            "supplier_name_line_1",
            "supplier_name_line_2",
            "customer_name",
            "provider_name",
            "product_name",
            "name",
        }:
            if len(compact) < 4:
                return 0.15

        if normalized_field in {
            "supplier_address",
            "address",
            "address_line_1",
            "address_line_2",
        }:
            if len(compact) < 6:
                return 0.10

        return 1.0
    @staticmethod
    def pair_evidence_quality(
        field_name: str,
        value_a: str | None,
        value_b: str | None,
    ) -> float:
        quality_a = (
            SimilarityEngine.field_evidence_quality(
                field_name,
                value_a,
            )
        )

        quality_b = (
            SimilarityEngine.field_evidence_quality(
                field_name,
                value_b,
            )
        )

        return round(
            min(
                quality_a,
                quality_b,
            ),
            4,
        )

    @staticmethod
    def record_evidence_quality(
        record: dict[str, str | None],
    ) -> dict[str, object]:
        field_scores: dict[str, float] = {}

        normalized_values: list[str] = []

        for field_name, value in record.items():
            score = (
                SimilarityEngine.field_evidence_quality(
                    field_name,
                    value,
                )
            )

            field_scores[field_name] = score

            normalized = str(
                value or ""
            ).strip().lower()

            if normalized:
                normalized_values.append(
                    normalized
                )

        populated_scores = [
            score
            for field_name, score
            in field_scores.items()
            if str(
                record.get(field_name) or ""
            ).strip()
        ]

        base_quality = (
            sum(populated_scores)
            / len(populated_scores)
            if populated_scores
            else 0.0
        )

        repeated_value_ratio = 0.0

        if normalized_values:
            repeated_value_ratio = (
                1.0
                - (
                    len(set(normalized_values))
                    / len(normalized_values)
                )
            )

        triggered_rules: list[str] = []

        if base_quality < 0.35:
            triggered_rules.append(
                "LOW_EVIDENCE_QUALITY"
            )

        if repeated_value_ratio >= 0.40:
            triggered_rules.append(
                "REPEATED_LOW_INFORMATION_VALUES"
            )
            base_quality *= 0.50

        invalid_email_fields = [
            field_name
            for field_name, score
            in field_scores.items()
            if "email" in field_name.lower()
            and str(
                record.get(field_name) or ""
            ).strip()
            and score == 0.0
        ]

        if invalid_email_fields:
            triggered_rules.append(
                "INVALID_EMAIL_EVIDENCE"
            )

        return {
            "evidence_quality": round(
                max(
                    0.0,
                    min(
                        base_quality,
                        1.0,
                    ),
                ),
                4,
            ),
            "field_quality": field_scores,
            "repeated_value_ratio": round(
                repeated_value_ratio,
                4,
            ),
            "triggered_rules": triggered_rules,
        }

    @staticmethod
    def adjust_similarity_for_evidence(
        similarity_score: float,
        evidence_quality_score: float,
    ) -> float:
        similarity = max(
            0.0,
            min(
                float(similarity_score),
                1.0,
            ),
        )

        quality = max(
            0.0,
            min(
                float(evidence_quality_score),
                1.0,
            ),
        )

        if quality < 0.20:
            multiplier = 0.30
        elif quality < 0.40:
            multiplier = 0.50
        elif quality < 0.60:
            multiplier = 0.75
        else:
            multiplier = 1.00

        return round(
            similarity * multiplier,
            4,
        )

    @staticmethod
    def product_id_similarity(
        value_a: str | None,
        value_b: str | None,
    ) -> float:
        a = SimilarityEngine.normalize_text(
            value_a
        )
        b = SimilarityEngine.normalize_text(
            value_b
        )

        if not a or not b:
            return 0.0

        if a == b:
            return 1.0

        return round(
            JaroWinkler.similarity(
                a,
                b,
            ),
            4,
        )

    @staticmethod
    def address_similarity(
        value_a: str | None,
        value_b: str | None,
    ) -> float:
        norm_a = (
            SimilarityEngine.normalize_address(
                value_a
            )
        )
        norm_b = (
            SimilarityEngine.normalize_address(
                value_b
            )
        )

        if not norm_a or not norm_b:
            return 0.0

        return round(
            JaroWinkler.similarity(
                norm_a,
                norm_b,
            ),
            4,
        )

    @staticmethod
    def normalize_email(
        email: str | None,
    ) -> str:
        if not email:
            return ""

        normalized = email.lower().strip()

        if "@" not in normalized:
            return normalized

        local, domain = normalized.split(
            "@",
            1,
        )

        if domain in {
            "gmail.com",
            "googlemail.com",
        }:
            local = local.replace(".", "")
            local = local.split("+", 1)[0]

        return f"{local}@{domain}"

    @staticmethod
    def email_similarity(
        email_a: str | None,
        email_b: str | None,
    ) -> float:
        a = SimilarityEngine.normalize_email(
            email_a
        )
        b = SimilarityEngine.normalize_email(
            email_b
        )

        if not a or not b:
            return 0.0

        return SimilarityEngine.jaro_similarity(
            a,
            b,
        )

    @staticmethod
    def normalize_phone(
        phone: str | None,
    ) -> str:
        if not phone:
            return ""

        digits = re.sub(
            r"\D",
            "",
            str(phone),
        )

        if (
            len(digits) == 11
            and digits.startswith("1")
        ):
            digits = digits[1:]

        return digits

    @staticmethod
    def phone_similarity(
        phone_a: str | None,
        phone_b: str | None,
    ) -> float | None:
        a = SimilarityEngine.normalize_phone(
            phone_a
        )
        b = SimilarityEngine.normalize_phone(
            phone_b
        )

        if not a or not b:
            return None

        return 1.0 if a == b else 0.0

    @staticmethod
    def similarity_band(
        score: float,
    ) -> str:
        normalized_score = max(
            0.0,
            min(
                float(score),
                1.0,
            ),
        )

        if normalized_score >= 0.95:
            return "EXACT"

        if normalized_score >= 0.70:
            return "SIMILAR"

        if normalized_score >= 0.50:
            return "FUZZY"

        return "DIFFERENT"


BUSINESS_EMAIL_DOMAINS = {
    "hospital.org",
    "baptisthealth.com",
    "acme.com",
}

PUBLIC_EMAIL_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "hotmail.com",
    "outlook.com",
    "icloud.com",
}


def email_domain_trust(
    domain: str,
) -> float:
    normalized = str(
        domain or ""
    ).lower().strip()

    if normalized in BUSINESS_EMAIL_DOMAINS:
        return 1.10

    if normalized.endswith(".edu"):
        return 1.05

    if normalized in PUBLIC_EMAIL_DOMAINS:
        return 0.95

    return 1.00
