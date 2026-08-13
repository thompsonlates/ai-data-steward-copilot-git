from __future__ import annotations

import re

from rapidfuzz.distance import JaroWinkler
from rapidfuzz.fuzz import WRatio


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
