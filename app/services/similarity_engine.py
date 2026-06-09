from rapidfuzz.fuzz import WRatio
from rapidfuzz.distance import JaroWinkler
import re
print("USING THIS similarity_engine FILE")

class SimilarityEngine:

    @staticmethod
    def normalize_text(value: str | None) -> str:
        if not value:
            return ""

        value = value.lower().strip()
        value = re.sub(r"[^a-z0-9@ ]+", "", value)

        return value

    @staticmethod
    def jaro_similarity(
        value_a: str | None,
        value_b: str | None,
    ) -> float:

        a = SimilarityEngine.normalize_text(value_a)
        b = SimilarityEngine.normalize_text(value_b)

        if not a or not b:
            return 0.0

        score = WRatio(a, b) / 100.0

        return round(score, 4)

    @staticmethod
    def normalize_address(value: str | None) -> str:
        if not value:
            return ""

        value = value.lower().strip()

        replacements = {
            " street ": " st ",
            " avenue ": " ave ",
            " boulevard ": " blvd ",
            " road ": " rd ",
            " drive ": " dr ",
        }

        for old, new in replacements.items():
            value = value.replace(old, new)

        value = re.sub(r"[^a-z0-9 ]", "", value)

        return value
    
    @staticmethod
    def product_id_similarity(a, b):

        a = SimilarityEngine.normalize_text(a)
        b = SimilarityEngine.normalize_text(b)

        if not a or not b:
            return 0.0

        if a == b:
            return 1.0

        return round(
        JaroWinkler.similarity(a, b),
        4,
    )

    @staticmethod
    def address_similarity(a: str | None, b: str | None) -> float:

        norm_a = SimilarityEngine.normalize_address(a)
        norm_b = SimilarityEngine.normalize_address(b)

        if not norm_a or not norm_b:
            return 0.0

        return round(
            JaroWinkler.similarity(norm_a, norm_b),
            4,
        )

    @staticmethod
    def normalize_email(email: str | None) -> str:

        if not email:
            return ""

        email = email.lower().strip()

        if "@" not in email:
            return email

        local, domain = email.split("@", 1)

        if domain in {"gmail.com", "googlemail.com"}:
            local = local.replace(".", "")
            local = local.split("+")[0]

        return f"{local}@{domain}"

    @staticmethod
    def email_similarity(
        email_a: str | None,
        email_b: str | None,
    ) -> float:

        a = SimilarityEngine.normalize_email(email_a)
        b = SimilarityEngine.normalize_email(email_b)

        if not a or not b:
            return 0.0

        return SimilarityEngine.jaro_similarity(a, b)

    @staticmethod
    def similarity_band(score: float) -> str:

        if score >= 0.95:
            return "EXACT"

        if score >= 0.70:
            return "SIMILAR"

        if score >= 0.50:
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


def email_domain_trust(domain: str) -> float:

    domain = domain.lower().strip()

    if domain in BUSINESS_EMAIL_DOMAINS:
        return 1.10

    if domain.endswith(".edu"):
        return 1.05

    if domain in PUBLIC_EMAIL_DOMAINS:
        return 0.95

    return 1.00