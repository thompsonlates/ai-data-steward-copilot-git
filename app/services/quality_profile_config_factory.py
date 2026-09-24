from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from app.services.quality_profiler_service import (
    CrossFieldRule,
    QualityFieldConfig,
    QualityProfileConfig,
)


# ---------------------------------------------------------------------------
# Reusable field patterns
# ---------------------------------------------------------------------------

EMAIL_REGEX = (
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)

PHONE_REGEX = (
    r"^(?:\+?1[\s.\-]?)?"
    r"(?:\(?\d{3}\)?[\s.\-]?)"
    r"\d{3}[\s.\-]?\d{4}$"
)

DATE_REGEX = (
    r"^(?:\d{4}-\d{2}-\d{2}|"
    r"\d{2}/\d{2}/\d{4}|"
    r"\d{4}/\d{2}/\d{2})$"
)

PRODUCT_VARIANT_REGEX = (
    r"^[0-9]+(?:\.[0-9]+)? "
    r"[A-Z]+$"
)

GLOBAL_PRODUCT_UOMS = (
    "OZ",
    "LB",
    "KG",
    "G",
    "EA",
    "CS",
    "PK",
    "IN",
)


# ---------------------------------------------------------------------------
# Governed / policy-facing DQ rules
# ---------------------------------------------------------------------------
#
# These definitions identify the deterministic DQ rules that are suitable
# for direct Governance Policy evaluation.
#
# Diagnostic rules may still explain *why* a record failed, but Governance
# Intelligence should map policy compliance to the authoritative rule.
#
# PRODUCT example:
#   PRODUCT_VARIANT_FORMAT_VALIDITY -> diagnostic
#   PRODUCT_VARIANT_UOM_VALIDITY    -> diagnostic
#   PRODUCT_VARIANT_STANDARDIZATION -> remediation opportunity
#   PRODUCT_VARIANT_VALIDITY        -> authoritative policy-facing rule
#
# The profiler service owns execution of these deterministic rules. This
# factory owns their domain semantics and exposes the mapping so policy /
# governance services do not need to infer authoritative rule relationships.
DOMAIN_GOVERNED_DQ_RULES: dict[str, tuple[dict[str, Any], ...]] = {
    "PRODUCT": (
        {
            "rule_id": "PRODUCT_VARIANT_VALIDITY",
            "field_name": "product_variant",
            "dimension": "VALIDITY",
            "severity": "HIGH",
            "authoritative": True,
            "description": (
                "Product Variant must contain a numeric quantity followed by "
                "exactly one space and an approved governed UOM."
            ),
            "diagnostic_rule_ids": (
                "PRODUCT_VARIANT_FORMAT_VALIDITY",
                "PRODUCT_VARIANT_UOM_VALIDITY",
                "PRODUCT_VARIANT_STANDARDIZATION",
            ),
        },
    ),
}


def get_governed_dq_rules(
    *,
    domain: str,
    mapped_fields: set[str] | None = None,
) -> tuple[dict[str, Any], ...]:
    """
    Return authoritative policy-facing DQ rule definitions for a domain.

    When mapped_fields is supplied, only rules whose governed field is
    actually mapped by the current source are returned. This prevents
    Governance Intelligence from evaluating a policy against a field that
    was not present in the profile.
    """
    normalized_domain = str(domain or "").strip().upper()

    if not normalized_domain:
        return tuple()

    normalized_mapped_fields = None

    if mapped_fields is not None:
        normalized_mapped_fields = {
            _normalize_field_name(field_name)
            for field_name in mapped_fields
            if _normalize_field_name(field_name)
        }

    rules: list[dict[str, Any]] = []

    for definition in DOMAIN_GOVERNED_DQ_RULES.get(
        normalized_domain,
        (),
    ):
        field_name = _normalize_field_name(
            str(definition.get("field_name") or "")
        )

        if (
            normalized_mapped_fields is not None
            and field_name not in normalized_mapped_fields
        ):
            continue

        rules.append(deepcopy(definition))

    return tuple(rules)

NAME_REGEX = r"^[A-Za-zÀ-ÖØ-öø-ÿ' .\-]{1,150}$"
GTIN_REGEX = r"^(?:\d{8}|\d{12}|\d{13}|\d{14})$"


# ---------------------------------------------------------------------------
# Generic domain defaults
#
# IMPORTANT:
# These are domain semantics, not customer-specific rules.
#
# Customer / organization / dataset overrides can be layered onto these
# defaults at runtime through field_overrides without changing Python code.
# ---------------------------------------------------------------------------

DOMAIN_FIELD_DEFINITIONS: dict[str, dict[str, dict[str, Any]]] = {
    "CUSTOMER": {
        "member_id": {
            "required": True,
            "uniqueness_key": True,
            "weight": 2.0,
            "severity": "CRITICAL",
        },
        "full_name": {
            "required": True,
            "uniqueness_key": False,
            "regex_pattern": r"^[A-Za-zÀ-ÖØ-öø-ÿ' .\-]{2,150}$",
            "standardization_required": True,
            "weight": 1.5,
            "severity": "HIGH",
        },
        "dob": {
            "required": False,
            "uniqueness_key": False,
            "regex_pattern": DATE_REGEX,
            "standardization_required": False,
            "weight": 1.0,
            "severity": "MEDIUM",
        },
        "email": {
            "required": False,
            "uniqueness_key": False,
            "regex_pattern": EMAIL_REGEX,
            "standardization_required": False,
            "weight": 1.0,
            "severity": "MEDIUM",
        },
        "phone_number": {
            "required": False,
            "uniqueness_key": False,
            "regex_pattern": PHONE_REGEX,
            "standardization_required": True,
            "weight": 1.0,
            "severity": "MEDIUM",
        },
        "address": {
            "required": False,
            "uniqueness_key": False,
            "weight": 1.0,
            "severity": "MEDIUM",
        },
        "source_system": {
            "required": False,
            "uniqueness_key": False,
            "weight": 0.5,
            "severity": "LOW",
        },
    },
    "PATIENT": {
        "patient_id": {
            "required": True,
            "uniqueness_key": True,
            "weight": 2.0,
            "severity": "CRITICAL",
        },
        "first_name": {
            "required": True,
            "uniqueness_key": False,
            "regex_pattern": NAME_REGEX,
            "standardization_required": True,
            "weight": 1.5,
            "severity": "HIGH",
        },
        "last_name": {
            "required": True,
            "uniqueness_key": False,
            "regex_pattern": NAME_REGEX,
            "standardization_required": True,
            "weight": 1.5,
            "severity": "HIGH",
        },
        "dob": {
            "required": False,
            "uniqueness_key": False,
            "regex_pattern": DATE_REGEX,
            "standardization_required": False,
            "weight": 1.5,
            "severity": "HIGH",
        },
        "email": {
            "required": False,
            "uniqueness_key": False,
            "regex_pattern": EMAIL_REGEX,
            "standardization_required": False,
            "weight": 1.0,
            "severity": "MEDIUM",
        },
        "phone_number": {
            "required": False,
            "uniqueness_key": False,
            "regex_pattern": PHONE_REGEX,
            "standardization_required": True,
            "weight": 1.0,
            "severity": "MEDIUM",
        },
        "address": {
            "required": False,
            "uniqueness_key": False,
            "weight": 1.0,
            "severity": "MEDIUM",
        },
        "source_system": {
            "required": False,
            "uniqueness_key": False,
            "weight": 0.5,
            "severity": "LOW",
        },
    },
    "PROVIDER": {
        "provider_id": {
            "required": True,
            "uniqueness_key": True,
            "weight": 2.0,
            "severity": "CRITICAL",
        },
        "npi": {
            "required": False,
            "uniqueness_key": False,
            "regex_pattern": r"^\d{10}$",
            "standardization_required": False,
            "weight": 2.0,
            "severity": "CRITICAL",
        },
        "first_name": {
            "required": True,
            "uniqueness_key": False,
            "regex_pattern": NAME_REGEX,
            "standardization_required": True,
            "weight": 1.5,
            "severity": "HIGH",
        },
        "last_name": {
            "required": True,
            "uniqueness_key": False,
            "regex_pattern": NAME_REGEX,
            "standardization_required": True,
            "weight": 1.5,
            "severity": "HIGH",
        },
        "specialty": {
            "required": False,
            "uniqueness_key": False,
            "weight": 1.0,
            "severity": "MEDIUM",
        },
        "dob": {
            "required": False,
            "uniqueness_key": False,
            "regex_pattern": DATE_REGEX,
            "standardization_required": False,
            "weight": 1.0,
            "severity": "MEDIUM",
        },
        "email": {
            "required": False,
            "uniqueness_key": False,
            "regex_pattern": EMAIL_REGEX,
            "standardization_required": False,
            "weight": 1.0,
            "severity": "MEDIUM",
        },
        "phone_number": {
            "required": False,
            "uniqueness_key": False,
            "regex_pattern": PHONE_REGEX,
            "standardization_required": True,
            "weight": 1.0,
            "severity": "MEDIUM",
        },
        "address": {
            "required": False,
            "uniqueness_key": False,
            "weight": 1.0,
            "severity": "MEDIUM",
        },
        "source_system": {
            "required": False,
            "uniqueness_key": False,
            "weight": 0.5,
            "severity": "LOW",
        },
    },
    "SUPPLIER": {
        "supplier_id": {
            "required": True,
            "uniqueness_key": True,
            "weight": 2.0,
            "severity": "CRITICAL",
        },
        "supplier_name": {
            "required": True,
            "uniqueness_key": False,
            "weight": 1.5,
            "severity": "HIGH",
        },
        "tax_id": {
            "required": False,
            "uniqueness_key": False,
            "regex_pattern": r"^\d{2}-?\d{7}$",
            "standardization_required": True,
            "weight": 1.5,
            "severity": "HIGH",
        },
        "email": {
            "required": False,
            "uniqueness_key": False,
            "regex_pattern": EMAIL_REGEX,
            "standardization_required": False,
            "weight": 1.0,
            "severity": "MEDIUM",
        },
        "address": {
            "required": False,
            "uniqueness_key": False,
            "weight": 1.0,
            "severity": "MEDIUM",
        },
        "source_system": {
            "required": False,
            "uniqueness_key": False,
            "weight": 0.5,
            "severity": "LOW",
        },
    },
    "PRODUCT": {
        # Generic PRODUCT semantics:
        # product_id may identify a product family/base item and therefore
        # must NOT be assumed unique across all customer datasets.
        "product_id": {
            "required": False,
            "uniqueness_key": False,
            "weight": 2.0,
            "severity": "CRITICAL",
        },
        "product_name": {
            "required": False,
            "uniqueness_key": False,
            "weight": 2.0,
            "severity": "HIGH",
        },
        "item_category": {
            "required": False,
            "uniqueness_key": False,
            "weight": 1.0,
            "severity": "MEDIUM",
        },
        "product_variant": {
            "required": False,
            "uniqueness_key": False,
            "regex_pattern": PRODUCT_VARIANT_REGEX,
            "allowed_uom_values": GLOBAL_PRODUCT_UOMS,
            "standardization_required": True,
            "weight": 1.5,
            "severity": "HIGH",
        },
        "sku": {
            "required": False,
            "uniqueness_key": False,
            "regex_pattern": r"^[A-Za-z0-9._/\-]{1,100}$",
            "standardization_required": False,
            "weight": 1.5,
            "severity": "HIGH",
        },
        "gtin": {
            "required": False,
            "uniqueness_key": False,
            "regex_pattern": GTIN_REGEX,
            "standardization_required": False,
            "weight": 1.5,
            "severity": "HIGH",
        },
        "uom": {
            "required": False,
            "uniqueness_key": False,
            "allowed_values": GLOBAL_PRODUCT_UOMS,
            "standardization_required": True,
            "weight": 1.0,
            "severity": "MEDIUM",
        },
        "effective_lot_date": {
            "required": False,
            "uniqueness_key": False,
            "regex_pattern": r"^\d{4}-\d{2}-\d{2}$",
            "standardization_required": True,
            "weight": 1.0,
            "severity": "MEDIUM",
        },
        "source_system": {
            "required": False,
            "uniqueness_key": False,
            "weight": 0.5,
            "severity": "LOW",
        },
    },
    "BANKING": {
        "account_id": {
            "required": True,
            "uniqueness_key": True,
            "regex_pattern": r"^[A-Za-z0-9._\-]{1,100}$",
            "standardization_required": True,
            "weight": 2.0,
            "severity": "CRITICAL",
        },
        "banking_customer_id": {
            "required": False,
            "uniqueness_key": False,
            "regex_pattern": r"^[A-Za-z0-9._\-]{1,100}$",
            "standardization_required": True,
            "weight": 2.0,
            "severity": "CRITICAL",
        },
        "sap_business_partner_id": {
            "required": False,
            "uniqueness_key": False,
            "regex_pattern": r"^[A-Za-z0-9._\-]{1,100}$",
            "standardization_required": True,
            "weight": 2.0,
            "severity": "CRITICAL",
        },
        "routing_number": {
            "required": False,
            "uniqueness_key": False,
            "regex_pattern": r"^\d{9}$",
            "standardization_required": True,
            "weight": 1.5,
            "severity": "HIGH",
        },
        "account_number_last4": {
            "required": False,
            "uniqueness_key": False,
            "regex_pattern": r"^\d{4}$",
            "standardization_required": True,
            "weight": 1.0,
            "severity": "MEDIUM",
        },
        "account_type": {
            "required": False,
            "uniqueness_key": False,
            "allowed_values": (
                "CHECKING",
                "SAVINGS",
                "MONEY_MARKET",
                "CREDIT",
                "LOAN",
                "INVESTMENT",
                "OTHER",
            ),
            "standardization_required": True,
            "weight": 1.0,
            "severity": "MEDIUM",
        },
        "currency": {
            "required": False,
            "uniqueness_key": False,
            "regex_pattern": r"^[A-Z]{3}$",
            "standardization_required": True,
            "weight": 1.0,
            "severity": "MEDIUM",
        },
        "account_status": {
            "required": False,
            "uniqueness_key": False,
            "allowed_values": (
                "ACTIVE",
                "INACTIVE",
                "CLOSED",
                "FROZEN",
                "DORMANT",
                "PENDING",
            ),
            "standardization_required": True,
            "weight": 1.0,
            "severity": "MEDIUM",
        },
        "email": {
            "required": False,
            "uniqueness_key": False,
            "regex_pattern": EMAIL_REGEX,
            "standardization_required": False,
            "weight": 1.0,
            "severity": "MEDIUM",
        },
        "phone_number": {
            "required": False,
            "uniqueness_key": False,
            "regex_pattern": PHONE_REGEX,
            "standardization_required": True,
            "weight": 1.0,
            "severity": "MEDIUM",
        },
        "address": {
            "required": False,
            "uniqueness_key": False,
            "weight": 1.0,
            "severity": "MEDIUM",
        },
        "source_system": {
            "required": False,
            "uniqueness_key": False,
            "weight": 0.5,
            "severity": "LOW",
        },
    },
}


# ---------------------------------------------------------------------------
# Generic composite identity semantics
# ---------------------------------------------------------------------------
#
# These rules describe domain identity grain, not customer-specific data.
# They are emitted only when every required field is actually mapped.
#
# PRODUCT example:
# A product_id may legitimately repeat for variants/sizes. When GTIN is
# available, the composite (product_id, gtin) identifies the row grain.
# This prevents product-family repetition from being mislabeled as duplicate
# records while still detecting a truly repeated product_id + gtin pair.
#
DOMAIN_COMPOSITE_IDENTITY_RULES: dict[str, tuple[dict[str, Any], ...]] = {
    "PRODUCT": (
        {
            "rule_id": "PRODUCT_ID_GTIN_COMPOSITE_UNIQUENESS",
            "fields": ("product_id", "gtin"),
            "description": (
                "product_id may repeat across legitimate product variants; "
                "the product_id + gtin combination must be unique when GTIN "
                "is present."
            ),
            "severity": "HIGH",
        },
    ),
}


def _build_composite_identity_rules(
    *,
    domain: str,
    mapped_fields: set[str],
) -> tuple[CrossFieldRule, ...]:
    """
    Build only composite identity rules supported by the mapped source.

    sql_condition uses a small declarative token understood by the profiler
    service. It is intentionally not arbitrary SQL.
    """
    rules: list[CrossFieldRule] = []

    for definition in DOMAIN_COMPOSITE_IDENTITY_RULES.get(domain, ()):
        fields = tuple(
            _normalize_field_name(field_name)
            for field_name in definition.get("fields", ())
            if _normalize_field_name(field_name)
        )

        if len(fields) < 2:
            continue

        if not set(fields).issubset(mapped_fields):
            continue

        rules.append(
            CrossFieldRule(
                rule_id=str(definition["rule_id"]),
                sql_condition="COMPOSITE_UNIQUE:" + ",".join(fields),
                description=str(definition["description"]),
                severity=str(definition.get("severity", "HIGH")).upper(),
            )
        )

    return tuple(rules)


def _normalize_field_name(value: str) -> str:
    return str(value or "").strip().lower()


def _default_field_settings(
    *,
    field_name: str,
    business_key_field: str,
) -> dict[str, Any]:
    """
    Conservative defaults for mapped fields that do not yet have a
    domain-specific definition.

    Historical behavior is preserved: an otherwise-unknown business key is
    treated as unique. Known domain fields can explicitly override that
    behavior (for example PRODUCT.product_id).
    """
    return {
        "required": False,
        "uniqueness_key": field_name == business_key_field,
        "weight": 1.0,
        "severity": "MEDIUM",
    }


def _merge_field_overrides(
    definitions: dict[str, dict[str, Any]],
    field_overrides: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    """
    Layer tenant / dataset / connection policy over generic domain defaults.

    The factory does not know customer names. Callers can supply runtime policy
    from a tenant-aware repository later without modifying this module.
    """
    merged = deepcopy(definitions)

    if not field_overrides:
        return merged

    for raw_field_name, override_values in field_overrides.items():
        field_name = _normalize_field_name(raw_field_name)

        if not field_name:
            continue

        current = dict(merged.get(field_name, {}))
        current.update(dict(override_values))
        merged[field_name] = current

    return merged


def build_quality_profile_config(
    *,
    domain: str,
    source_name: str,
    business_key_field: str,
    mapped_fields: set[str],
    minimum_record_score: float = 80.0,
    field_overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> QualityProfileConfig:
    """
    Build the runtime QualityProfileConfig.

    Resolution order:
        1. generic domain defaults
        2. optional tenant/dataset/connection field overrides
        3. conservative fallback for unknown mapped fields

    Only mapped fields are emitted into QualityProfileConfig so profiling
    remains source-agnostic and does not fail because an optional domain field
    is absent from a particular source.
    """
    normalized_domain = str(domain or "").strip().upper()
    normalized_source_name = str(source_name or "").strip()
    normalized_business_key = _normalize_field_name(
        business_key_field
    )

    if not normalized_domain:
        raise ValueError("domain is required.")

    if not normalized_source_name:
        raise ValueError("source_name is required.")

    if not normalized_business_key:
        raise ValueError("business_key_field is required.")

    if not 0 <= float(minimum_record_score) <= 100:
        raise ValueError(
            "minimum_record_score must be between 0 and 100."
        )

    normalized_mapped_fields = {
        _normalize_field_name(field_name)
        for field_name in mapped_fields
        if _normalize_field_name(field_name)
    }

    if not normalized_mapped_fields:
        raise ValueError(
            "At least one mapped field is required."
        )

    domain_definitions = DOMAIN_FIELD_DEFINITIONS.get(
        normalized_domain,
        {},
    )

    definitions = _merge_field_overrides(
        domain_definitions,
        field_overrides,
    )

    fields: list[QualityFieldConfig] = []

    for field_name in sorted(normalized_mapped_fields):
        settings = definitions.get(field_name)

        if settings is None:
            settings = _default_field_settings(
                field_name=field_name,
                business_key_field=normalized_business_key,
            )

        fields.append(
            QualityFieldConfig(
                field_name=field_name,
                **settings,
            )
        )

    cross_field_rules = _build_composite_identity_rules(
        domain=normalized_domain,
        mapped_fields=normalized_mapped_fields,
    )

    return QualityProfileConfig(
        domain=normalized_domain,
        source_table=normalized_source_name,
        business_key_field=normalized_business_key,
        fields=fields,
        cross_field_rules=cross_field_rules,
        minimum_record_score=float(
            minimum_record_score
        ),
    )