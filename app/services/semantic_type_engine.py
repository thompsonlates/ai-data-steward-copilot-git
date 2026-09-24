from __future__ import annotations

"""
Deterministic semantic-type inference for AI Data Steward Copilot.

Architecture
------------
SOURCE / CANONICAL COLUMN MAPPING
    -> SemanticTypeEngine
    -> ProfileRuleRegistry
    -> UniversalProfileRulesEngine
    -> QualityFinding / QualityRuleExecution
    -> Quality Intelligence
    -> AI rule suggestions
    -> Steward approval
    -> ACTIVE governed rule registry

This module intentionally does NOT:
- call an LLM
- decide whether a value is "good" or "bad"
- activate governance rules
- persist rule definitions
- infer tenant policy
- execute remediation

Its only responsibility is to classify mapped attributes into reusable semantic
types using deterministic, explainable evidence.

Governance principle
--------------------
Inference is evidence, not authority.

A field inferred as IDENTIFIER does not imply a character policy, requiredness,
uniqueness, or remediation rule. Those standards must come from ACTIVE governed
rules resolved by ProfileRuleRegistry.
"""

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Iterable, Mapping, Sequence


# ---------------------------------------------------------------------------
# Canonical semantic types
# ---------------------------------------------------------------------------

class SemanticType(str, Enum):
    UNKNOWN = "UNKNOWN"

    # Identity / descriptive
    IDENTIFIER = "IDENTIFIER"
    CODE = "CODE"
    PERSON_NAME = "PERSON_NAME"
    ORGANIZATION_NAME = "ORGANIZATION_NAME"
    LOCATION_NAME = "LOCATION_NAME"
    PRODUCT_NAME = "PRODUCT_NAME"
    # Compound Product attribute: quantity + governed unit-of-measure token.
    # This is intentionally distinct from UOM, which represents the unit token only.
    PRODUCT_VARIANT = "PRODUCT_VARIANT"

    # Contact / location
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    ADDRESS = "ADDRESS"
    CITY = "CITY"
    STATE_PROVINCE = "STATE_PROVINCE"
    POSTAL_CODE = "POSTAL_CODE"
    COUNTRY = "COUNTRY"

    # Temporal
    DATE = "DATE"
    DATETIME = "DATETIME"

    # Classification / state
    STATUS = "STATUS"
    CLASSIFICATION = "CLASSIFICATION"

    # Domain-specialized identifiers / attributes
    NPI = "NPI"
    GTIN = "GTIN"
    SKU = "SKU"
    TAX_IDENTIFIER = "TAX_IDENTIFIER"
    ROUTING_NUMBER = "ROUTING_NUMBER"
    ACCOUNT_IDENTIFIER = "ACCOUNT_IDENTIFIER"
    UOM = "UOM"

    # Generic data semantics
    BOOLEAN = "BOOLEAN"
    INTEGER = "INTEGER"
    DECIMAL = "DECIMAL"
    TEXT = "TEXT"


class InferenceSource(str, Enum):
    GOVERNED_OVERRIDE = "GOVERNED_OVERRIDE"
    CANONICAL_FIELD = "CANONICAL_FIELD"
    FIELD_NAME = "FIELD_NAME"
    VALUE_EVIDENCE = "VALUE_EVIDENCE"
    DATA_TYPE = "DATA_TYPE"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Result contracts
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SemanticTypeCandidate:
    semantic_type: SemanticType
    confidence: float
    source: InferenceSource
    reason: str

    def __post_init__(self) -> None:
        confidence = max(0.0, min(1.0, float(self.confidence)))
        object.__setattr__(self, "confidence", round(confidence, 4))
        object.__setattr__(self, "reason", str(self.reason or "").strip())


@dataclass(frozen=True)
class SemanticTypeInference:
    field_name: str
    semantic_type: SemanticType
    confidence: float
    source: InferenceSource
    reason: str

    canonical_field_name: str | None = None
    physical_field_name: str | None = None
    candidates: tuple[SemanticTypeCandidate, ...] = field(default_factory=tuple)

    # Important for Governed AI:
    # low-confidence / ambiguous classifications may be surfaced as proposals,
    # but must not silently establish new governed standards.
    requires_review: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "field_name",
            str(self.field_name or "").strip().lower(),
        )
        object.__setattr__(
            self,
            "canonical_field_name",
            (
                str(self.canonical_field_name).strip().lower()
                if self.canonical_field_name
                else None
            ),
        )
        object.__setattr__(
            self,
            "physical_field_name",
            (
                str(self.physical_field_name).strip()
                if self.physical_field_name
                else None
            ),
        )
        object.__setattr__(
            self,
            "confidence",
            round(max(0.0, min(1.0, float(self.confidence))), 4),
        )
        object.__setattr__(self, "reason", str(self.reason or "").strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_name": self.field_name,
            "canonical_field_name": self.canonical_field_name,
            "physical_field_name": self.physical_field_name,
            "semantic_type": self.semantic_type.value,
            "confidence": self.confidence,
            "source": self.source.value,
            "reason": self.reason,
            "requires_review": self.requires_review,
            "candidates": [
                {
                    "semantic_type": candidate.semantic_type.value,
                    "confidence": candidate.confidence,
                    "source": candidate.source.value,
                    "reason": candidate.reason,
                }
                for candidate in self.candidates
            ],
        }


# ---------------------------------------------------------------------------
# Deterministic canonical-field semantics
# ---------------------------------------------------------------------------

# These mappings describe semantic meaning only.
# They DO NOT define requiredness, uniqueness, allowed values, regex policy,
# severity, or remediation behavior.

CANONICAL_FIELD_SEMANTICS: dict[str, SemanticType] = {
    # Generic identity
    "id": SemanticType.IDENTIFIER,
    "identifier": SemanticType.IDENTIFIER,
    "member_id": SemanticType.IDENTIFIER,
    "customer_id": SemanticType.IDENTIFIER,
    "patient_id": SemanticType.IDENTIFIER,
    "provider_id": SemanticType.IDENTIFIER,
    "supplier_id": SemanticType.IDENTIFIER,
    "product_id": SemanticType.IDENTIFIER,
    "location_id": SemanticType.IDENTIFIER,
    "site_id": SemanticType.IDENTIFIER,
    "parent_location_id": SemanticType.IDENTIFIER,
    "organization_entity_id": SemanticType.IDENTIFIER,
    "parent_organization_id": SemanticType.IDENTIFIER,

    # Codes
    "location_code": SemanticType.CODE,
    "organization_code": SemanticType.CODE,
    "source_system": SemanticType.CODE,
    "source_system_id": SemanticType.CODE,

    # Names
    "first_name": SemanticType.PERSON_NAME,
    "middle_name": SemanticType.PERSON_NAME,
    "last_name": SemanticType.PERSON_NAME,
    "full_name": SemanticType.PERSON_NAME,
    "customer_name": SemanticType.PERSON_NAME,
    "provider_name": SemanticType.PERSON_NAME,
    "supplier_name": SemanticType.ORGANIZATION_NAME,
    "organization_name": SemanticType.ORGANIZATION_NAME,
    "location_name": SemanticType.LOCATION_NAME,
    "product_name": SemanticType.PRODUCT_NAME,
    "product_variant": SemanticType.PRODUCT_VARIANT,

    # Contact / address
    "email": SemanticType.EMAIL,
    "contact_email": SemanticType.EMAIL,
    "phone": SemanticType.PHONE,
    "phone_number": SemanticType.PHONE,
    "contact_phone": SemanticType.PHONE,
    "address": SemanticType.ADDRESS,
    "location_address": SemanticType.ADDRESS,
    "address_line_1": SemanticType.ADDRESS,
    "address_line_2": SemanticType.ADDRESS,
    "city": SemanticType.CITY,
    "state": SemanticType.STATE_PROVINCE,
    "state_code": SemanticType.STATE_PROVINCE,
    "province": SemanticType.STATE_PROVINCE,
    "postal_code": SemanticType.POSTAL_CODE,
    "zip": SemanticType.POSTAL_CODE,
    "zip_code": SemanticType.POSTAL_CODE,
    "country": SemanticType.COUNTRY,
    "country_code": SemanticType.COUNTRY,

    # Dates / timestamps
    "dob": SemanticType.DATE,
    "date_of_birth": SemanticType.DATE,
    "effective_date": SemanticType.DATE,
    "expiration_date": SemanticType.DATE,
    "effective_lot_date": SemanticType.DATE,
    "created_at": SemanticType.DATETIME,
    "updated_at": SemanticType.DATETIME,
    "modified_at": SemanticType.DATETIME,

    # Classification / status
    "status": SemanticType.STATUS,
    "organization_status": SemanticType.STATUS,
    "location_status": SemanticType.STATUS,
    "organization_type": SemanticType.CLASSIFICATION,
    "location_type": SemanticType.CLASSIFICATION,
    "item_category": SemanticType.CLASSIFICATION,
    "specialty": SemanticType.CLASSIFICATION,

    # Specialized governed semantics
    "npi": SemanticType.NPI,
    "gtin": SemanticType.GTIN,
    "sku": SemanticType.SKU,
    "tax_id": SemanticType.TAX_IDENTIFIER,
    "supplier_tax_id": SemanticType.TAX_IDENTIFIER,
    "ein": SemanticType.TAX_IDENTIFIER,
    "tin": SemanticType.TAX_IDENTIFIER,
    "routing_number": SemanticType.ROUTING_NUMBER,
    "aba_routing_number": SemanticType.ROUTING_NUMBER,
    "account_id": SemanticType.ACCOUNT_IDENTIFIER,
    "account_number": SemanticType.ACCOUNT_IDENTIFIER,
    "uom": SemanticType.UOM,
}


# Conservative field-name aliases.
# Exact canonical mappings above always outrank these heuristics.
_FIELD_NAME_RULES: tuple[tuple[re.Pattern[str], SemanticType, float, str], ...] = (
    (re.compile(r"(^|_)e_?mail($|_)|email", re.I),
     SemanticType.EMAIL, 0.98, "Field name indicates email."),
    (re.compile(r"(^|_)(phone|telephone|mobile|fax)($|_)", re.I),
     SemanticType.PHONE, 0.96, "Field name indicates telephone/contact number."),
    (re.compile(r"(^|_)(address|addr)($|_)", re.I),
     SemanticType.ADDRESS, 0.94, "Field name indicates postal/street address."),
    (re.compile(r"(^|_)(postal|zip|zipcode|zip_code)($|_)", re.I),
     SemanticType.POSTAL_CODE, 0.96, "Field name indicates postal code."),
    (re.compile(r"(^|_)(city)($|_)", re.I),
     SemanticType.CITY, 0.96, "Field name indicates city."),
    (re.compile(r"(^|_)(state|province)($|_)", re.I),
     SemanticType.STATE_PROVINCE, 0.94, "Field name indicates state/province."),
    (re.compile(r"(^|_)(country)($|_)", re.I),
     SemanticType.COUNTRY, 0.94, "Field name indicates country."),
    (re.compile(r"(^|_)(npi)($|_)", re.I),
     SemanticType.NPI, 0.995, "Field name explicitly indicates NPI."),
    (re.compile(r"(^|_)(gtin|upc)($|_)", re.I),
     SemanticType.GTIN, 0.98, "Field name indicates GTIN/UPC product identifier."),
    (re.compile(r"(^|_)(sku)($|_)", re.I),
     SemanticType.SKU, 0.98, "Field name explicitly indicates SKU."),
    (re.compile(r"(^|_)(ein|tin|tax_id|taxid)($|_)", re.I),
     SemanticType.TAX_IDENTIFIER, 0.97, "Field name indicates tax identifier."),
    (re.compile(r"(^|_)(routing|aba)(_?number)?($|_)", re.I),
     SemanticType.ROUTING_NUMBER, 0.98, "Field name indicates routing number."),
    (re.compile(r"(^|_)(uom|unit_of_measure)($|_)", re.I),
     SemanticType.UOM, 0.98, "Field name indicates unit of measure."),
    (re.compile(r"(^|_)(status|state_flag)($|_)", re.I),
     SemanticType.STATUS, 0.90, "Field name indicates lifecycle/status."),
    (re.compile(r"(^|_)(type|category|class|classification)($|_)", re.I),
     SemanticType.CLASSIFICATION, 0.86, "Field name indicates classification."),
    (re.compile(r"(^|_)(date|dob|birth_date|effective_date|expiration_date)($|_)", re.I),
     SemanticType.DATE, 0.92, "Field name indicates date."),
    (re.compile(r"(^|_)(timestamp|datetime|created_at|updated_at|modified_at)($|_)", re.I),
     SemanticType.DATETIME, 0.94, "Field name indicates date/time."),
    (re.compile(r"(^|_)(code|cd)($|_)", re.I),
     SemanticType.CODE, 0.82, "Field name indicates governed/business code."),
    (re.compile(r"(^|_)(id|identifier|numberid|number_id)($|_)", re.I),
     SemanticType.IDENTIFIER, 0.84, "Field name indicates identifier."),
)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class SemanticTypeEngine:
    """
    Stateless deterministic semantic-type classifier.

    The engine accepts canonical field mappings when available. Canonical
    mappings are authoritative semantic evidence because the steward/source
    mapping has already established what the physical column represents.

    Governed overrides, when supplied, outrank all inference. They should come
    only from an approved/ACTIVE configuration layer, never directly from an
    LLM suggestion.
    """

    REVIEW_THRESHOLD = 0.75
    AMBIGUITY_DELTA = 0.08

    @staticmethod
    def normalize_field_name(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""

        # camelCase / PascalCase -> snake_case
        text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
        text = re.sub(r"[^A-Za-z0-9]+", "_", text)
        text = re.sub(r"_+", "_", text).strip("_")
        return text.lower()

    @staticmethod
    def _normalize_declared_type(value: Any) -> str:
        return str(value or "").strip().upper()

    @staticmethod
    def _sample_values(
        values: Sequence[Any] | None,
        *,
        limit: int = 100,
    ) -> tuple[str, ...]:
        samples: list[str] = []

        for value in values or ():
            if value is None:
                continue
            text = str(value).strip()
            if not text:
                continue
            samples.append(text)
            if len(samples) >= limit:
                break

        return tuple(samples)

    @staticmethod
    def _candidate(
        semantic_type: SemanticType,
        confidence: float,
        source: InferenceSource,
        reason: str,
    ) -> SemanticTypeCandidate:
        return SemanticTypeCandidate(
            semantic_type=semantic_type,
            confidence=confidence,
            source=source,
            reason=reason,
        )

    def _canonical_candidate(
        self,
        canonical_field_name: str,
    ) -> SemanticTypeCandidate | None:
        semantic_type = CANONICAL_FIELD_SEMANTICS.get(canonical_field_name)
        if semantic_type is None:
            return None

        return self._candidate(
            semantic_type,
            1.0,
            InferenceSource.CANONICAL_FIELD,
            f"Canonical field '{canonical_field_name}' has governed semantic mapping.",
        )

    def _field_name_candidates(
        self,
        normalized_field_name: str,
    ) -> list[SemanticTypeCandidate]:
        candidates: list[SemanticTypeCandidate] = []

        exact = CANONICAL_FIELD_SEMANTICS.get(normalized_field_name)
        if exact is not None:
            candidates.append(
                self._candidate(
                    exact,
                    0.99,
                    InferenceSource.FIELD_NAME,
                    f"Field name exactly matches known semantic field '{normalized_field_name}'.",
                )
            )

        for pattern, semantic_type, confidence, reason in _FIELD_NAME_RULES:
            if pattern.search(normalized_field_name):
                candidates.append(
                    self._candidate(
                        semantic_type,
                        confidence,
                        InferenceSource.FIELD_NAME,
                        reason,
                    )
                )

        return candidates

    def _value_candidates(
        self,
        values: Sequence[Any] | None,
    ) -> list[SemanticTypeCandidate]:
        samples = self._sample_values(values)
        if not samples:
            return []

        candidates: list[SemanticTypeCandidate] = []

        def ratio(predicate) -> float:
            matches = sum(1 for value in samples if predicate(value))
            return matches / len(samples)

        email_regex = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
        phone_regex = re.compile(r"^(?:\+?1[\s.\-]?)?(?:\(?\d{3}\)?[\s.\-]?)\d{3}[\s.\-]?\d{4}$")
        date_regex = re.compile(r"^(?:\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}|\d{4}/\d{2}/\d{2})$")
        datetime_regex = re.compile(
            r"^\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+\-]\d{2}:?\d{2})?$"
        )

        checks = (
            (
                SemanticType.EMAIL,
                ratio(lambda v: bool(email_regex.fullmatch(v))),
                "Sample values structurally resemble email addresses.",
            ),
            (
                SemanticType.PHONE,
                ratio(lambda v: bool(phone_regex.fullmatch(v))),
                "Sample values structurally resemble U.S. phone numbers.",
            ),
            (
                SemanticType.DATETIME,
                ratio(lambda v: bool(datetime_regex.fullmatch(v))),
                "Sample values structurally resemble date/time values.",
            ),
            (
                SemanticType.DATE,
                ratio(lambda v: bool(date_regex.fullmatch(v))),
                "Sample values structurally resemble dates.",
            ),
        )

        for semantic_type, match_ratio, reason in checks:
            # Value evidence is intentionally conservative and cannot outrank
            # canonical mappings or strong field-name evidence.
            if match_ratio >= 0.95:
                candidates.append(
                    self._candidate(
                        semantic_type,
                        min(0.90, 0.75 + (match_ratio * 0.15)),
                        InferenceSource.VALUE_EVIDENCE,
                        f"{reason} Match ratio={match_ratio:.2%}.",
                    )
                )

        return candidates

    def _data_type_candidate(
        self,
        declared_type: Any,
    ) -> SemanticTypeCandidate | None:
        normalized = self._normalize_declared_type(declared_type)
        if not normalized:
            return None

        if normalized in {"BOOL", "BOOLEAN"}:
            semantic_type = SemanticType.BOOLEAN
        elif normalized in {
            "INT", "INTEGER", "INT64", "BIGINT", "SMALLINT",
        }:
            semantic_type = SemanticType.INTEGER
        elif normalized in {
            "FLOAT", "FLOAT64", "DOUBLE", "DECIMAL", "NUMERIC",
            "BIGNUMERIC", "NUMBER",
        }:
            semantic_type = SemanticType.DECIMAL
        elif normalized in {"DATE"}:
            semantic_type = SemanticType.DATE
        elif normalized in {
            "DATETIME", "TIMESTAMP", "TIMESTAMP_NTZ", "TIMESTAMP_TZ",
            "TIMESTAMP_LTZ",
        }:
            semantic_type = SemanticType.DATETIME
        elif normalized in {
            "STRING", "VARCHAR", "CHAR", "TEXT", "NVARCHAR",
        }:
            semantic_type = SemanticType.TEXT
        else:
            return None

        return self._candidate(
            semantic_type,
            0.60,
            InferenceSource.DATA_TYPE,
            f"Declared physical data type is {normalized}.",
        )

    @staticmethod
    def _deduplicate_candidates(
        candidates: Iterable[SemanticTypeCandidate],
    ) -> tuple[SemanticTypeCandidate, ...]:
        best: dict[SemanticType, SemanticTypeCandidate] = {}

        for candidate in candidates:
            current = best.get(candidate.semantic_type)
            if current is None or candidate.confidence > current.confidence:
                best[candidate.semantic_type] = candidate

        return tuple(
            sorted(
                best.values(),
                key=lambda item: (
                    -item.confidence,
                    item.semantic_type.value,
                ),
            )
        )

    def infer_field(
        self,
        *,
        field_name: str,
        canonical_field_name: str | None = None,
        physical_field_name: str | None = None,
        sample_values: Sequence[Any] | None = None,
        declared_type: Any = None,
        governed_override: str | SemanticType | None = None,
    ) -> SemanticTypeInference:
        normalized_field = self.normalize_field_name(field_name)
        normalized_canonical = self.normalize_field_name(
            canonical_field_name or field_name
        )

        if not normalized_field:
            raise ValueError("field_name is required.")

        # ---------------------------------------------------------------
        # 1. ACTIVE governed semantic override
        # ---------------------------------------------------------------
        if governed_override is not None:
            try:
                semantic_type = (
                    governed_override
                    if isinstance(governed_override, SemanticType)
                    else SemanticType(str(governed_override).strip().upper())
                )
            except ValueError as exc:
                raise ValueError(
                    f"Unsupported governed semantic type: {governed_override!r}."
                ) from exc

            return SemanticTypeInference(
                field_name=normalized_field,
                canonical_field_name=normalized_canonical,
                physical_field_name=physical_field_name,
                semantic_type=semantic_type,
                confidence=1.0,
                source=InferenceSource.GOVERNED_OVERRIDE,
                reason=(
                    "Semantic type supplied by ACTIVE governed configuration."
                ),
                candidates=(
                    self._candidate(
                        semantic_type,
                        1.0,
                        InferenceSource.GOVERNED_OVERRIDE,
                        "ACTIVE governed semantic type override.",
                    ),
                ),
                requires_review=False,
            )

        candidates: list[SemanticTypeCandidate] = []

        # ---------------------------------------------------------------
        # 2. Canonical source mapping
        # ---------------------------------------------------------------
        canonical_candidate = self._canonical_candidate(normalized_canonical)
        if canonical_candidate is not None:
            candidates.append(canonical_candidate)

        # ---------------------------------------------------------------
        # 3. Field-name evidence
        # ---------------------------------------------------------------
        candidates.extend(
            self._field_name_candidates(normalized_field)
        )

        if (
            normalized_canonical
            and normalized_canonical != normalized_field
        ):
            candidates.extend(
                self._field_name_candidates(normalized_canonical)
            )

        # ---------------------------------------------------------------
        # 4. Value-shape evidence
        # ---------------------------------------------------------------
        candidates.extend(
            self._value_candidates(sample_values)
        )

        # ---------------------------------------------------------------
        # 5. Physical data type fallback
        # ---------------------------------------------------------------
        data_type_candidate = self._data_type_candidate(declared_type)
        if data_type_candidate is not None:
            candidates.append(data_type_candidate)

        ranked = self._deduplicate_candidates(candidates)

        if not ranked:
            return SemanticTypeInference(
                field_name=normalized_field,
                canonical_field_name=normalized_canonical,
                physical_field_name=physical_field_name,
                semantic_type=SemanticType.UNKNOWN,
                confidence=0.0,
                source=InferenceSource.UNKNOWN,
                reason="No deterministic semantic evidence was available.",
                candidates=tuple(),
                requires_review=True,
            )

        winner = ranked[0]
        runner_up = ranked[1] if len(ranked) > 1 else None

        ambiguous = bool(
            runner_up
            and runner_up.semantic_type != winner.semantic_type
            and abs(winner.confidence - runner_up.confidence)
            <= self.AMBIGUITY_DELTA
        )

        requires_review = (
            winner.confidence < self.REVIEW_THRESHOLD
            or ambiguous
        )

        reason = winner.reason
        if ambiguous and runner_up is not None:
            reason = (
                f"{winner.reason} Competing evidence also suggests "
                f"{runner_up.semantic_type.value} "
                f"({runner_up.confidence:.2f}); steward review is recommended."
            )

        return SemanticTypeInference(
            field_name=normalized_field,
            canonical_field_name=normalized_canonical,
            physical_field_name=physical_field_name,
            semantic_type=winner.semantic_type,
            confidence=winner.confidence,
            source=winner.source,
            reason=reason,
            candidates=ranked,
            requires_review=requires_review,
        )

    def infer_mapped_fields(
        self,
        *,
        mapped_fields: Sequence[str],
        canonical_to_physical: Mapping[str, str] | None = None,
        sample_values_by_field: Mapping[str, Sequence[Any]] | None = None,
        declared_types_by_field: Mapping[str, Any] | None = None,
        governed_overrides: Mapping[str, str | SemanticType] | None = None,
    ) -> dict[str, SemanticTypeInference]:
        """
        Infer semantics for canonical fields participating in the profile run.

        Returned dictionary keys are normalized canonical field names so they
        plug directly into RuleResolutionContext.semantic_types_by_field.
        """
        mappings = {
            self.normalize_field_name(key): str(value or "").strip()
            for key, value in dict(canonical_to_physical or {}).items()
            if self.normalize_field_name(key)
        }

        samples = {
            self.normalize_field_name(key): value
            for key, value in dict(sample_values_by_field or {}).items()
            if self.normalize_field_name(key)
        }

        declared_types = {
            self.normalize_field_name(key): value
            for key, value in dict(declared_types_by_field or {}).items()
            if self.normalize_field_name(key)
        }

        overrides = {
            self.normalize_field_name(key): value
            for key, value in dict(governed_overrides or {}).items()
            if self.normalize_field_name(key)
        }

        results: dict[str, SemanticTypeInference] = {}

        for raw_field_name in mapped_fields:
            canonical = self.normalize_field_name(raw_field_name)
            if not canonical:
                continue

            physical = mappings.get(canonical) or str(raw_field_name).strip()

            results[canonical] = self.infer_field(
                field_name=canonical,
                canonical_field_name=canonical,
                physical_field_name=physical,
                sample_values=samples.get(canonical),
                declared_type=declared_types.get(canonical),
                governed_override=overrides.get(canonical),
            )

        return results

    @staticmethod
    def semantic_types_for_registry(
        inferences: Mapping[str, SemanticTypeInference],
        *,
        include_review_required: bool = False,
    ) -> dict[str, str]:
        """
        Convert inference results into ProfileRuleRegistry input.

        By default, ambiguous/low-confidence classifications are excluded from
        automatic semantic-rule activation. They can instead feed the governed
        AI rule-suggestion workflow for steward review.
        """
        results: dict[str, str] = {}

        for field_name, inference in inferences.items():
            if inference.semantic_type == SemanticType.UNKNOWN:
                continue

            if inference.requires_review and not include_review_required:
                continue

            results[str(field_name).strip().lower()] = (
                inference.semantic_type.value
            )

        return results

    @staticmethod
    def review_candidates(
        inferences: Mapping[str, SemanticTypeInference],
    ) -> tuple[SemanticTypeInference, ...]:
        """
        Return classifications appropriate for AI/steward review.

        This is deliberately a proposal feed only. Nothing returned here
        becomes governed merely because the engine inferred it.
        """
        return tuple(
            inference
            for inference in inferences.values()
            if inference.requires_review
            or inference.semantic_type == SemanticType.UNKNOWN
        )


__all__ = [
    "CANONICAL_FIELD_SEMANTICS",
    "InferenceSource",
    "SemanticType",
    "SemanticTypeCandidate",
    "SemanticTypeEngine",
    "SemanticTypeInference",
]
