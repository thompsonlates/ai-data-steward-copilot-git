from __future__ import annotations

from app.services.quality_profiler_service import (
    QualityFieldConfig,
    QualityProfileConfig,
)

def build_quality_profile_config(
    *,
    domain: str,
    source_name: str,
    business_key_field: str,
    mapped_fields: set[str],
    minimum_record_score: float = 80.0,
) -> QualityProfileConfig:
    normalized_domain = str(domain or "").strip().upper()

    if normalized_domain == "CUSTOMER":
        definitions = {
            "member_id": dict(
                required=True,
                uniqueness_key=True,
                weight=2.0,
                severity="CRITICAL",
            ),
            "full_name": dict(
                required=True,
                uniqueness_key=False,
                regex_pattern=r"^[A-Za-zÀ-ÖØ-öø-ÿ' .\-]{2,150}$",
                standardization_required=True,
                weight=1.5,
                severity="HIGH",
            ),
            "dob": dict(
                required=False,
                uniqueness_key=False,
                regex_pattern=(
                    r"^(?:\d{4}-\d{2}-\d{2}|"
                    r"\d{2}/\d{2}/\d{4}|"
                    r"\d{4}/\d{2}/\d{2})$"
                ),
                standardization_required=False,
                weight=1.0,
                severity="MEDIUM",
            ),
            "email": dict(
                required=False,
                uniqueness_key=False,
                regex_pattern=(
                    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
                    r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
                ),
                standardization_required=False,
                weight=1.0,
                severity="MEDIUM",
            ),
            "phone_number": dict(
                required=False,
                uniqueness_key=False,
                regex_pattern=r"^(?:\+?1[\s.\-]?)?(?:\(?\d{3}\)?[\s.\-]?)\d{3}[\s.\-]?\d{4}$",
                standardization_required=True,
                weight=1.0,
                severity="MEDIUM",
            ),
            "address": dict(
                required=False,
                uniqueness_key=False,
                weight=1.0,
                severity="MEDIUM",
            ),
            "source_system": dict(
                required=False,
                uniqueness_key=False,
                weight=0.5,
                severity="LOW",
            ),
        }

    elif normalized_domain == "PATIENT":
        definitions = {
            "patient_id": dict(
                required=True,
                uniqueness_key=True,
                weight=2.0,
                severity="CRITICAL",
            ),
            "first_name": dict(
                required=True,
                uniqueness_key=False,
                regex_pattern=r"^[A-Za-zÀ-ÖØ-öø-ÿ' .\-]{1,100}$",
                standardization_required=True,
                weight=1.5,
                severity="HIGH",
            ),
            "last_name": dict(
                required=True,
                uniqueness_key=False,
                regex_pattern=r"^[A-Za-zÀ-ÖØ-öø-ÿ' .\-]{1,100}$",
                standardization_required=True,
                weight=1.5,
                severity="HIGH",
),
            "dob": dict(
                required=False,
                uniqueness_key=False,
                regex_pattern=(
                    r"^(?:\d{4}-\d{2}-\d{2}|"
                    r"\d{2}/\d{2}/\d{4}|"
                    r"\d{4}/\d{2}/\d{2})$"
                ),
                standardization_required=False,
                weight=1.5,
                severity="HIGH",
            ),
            "email": dict(
                required=False,
                uniqueness_key=False,
                weight=1.0,
                severity="MEDIUM",
            ),
            "phone_number": dict(
                required=False,
                uniqueness_key=False,
                weight=1.0,
                severity="MEDIUM",
            ),
            "address": dict(
                required=False,
                uniqueness_key=False,
                weight=1.0,
                severity="MEDIUM",
            ),
            "source_system": dict(
                required=False,
                uniqueness_key=False,
                weight=0.5,
                severity="LOW",
            ),
        }

    elif normalized_domain == "PROVIDER":
        definitions = {
            "provider_id": dict(
                required=True,
                uniqueness_key=True,
                weight=2.0,
                severity="CRITICAL",
            ),
            "npi": dict(
                required=False,
                uniqueness_key=False,
                regex_pattern=r"^\d{10}$",
                standardization_required=False,
                weight=2.0,
                severity="CRITICAL",
            ),
            "first_name": dict(
                required=True,
                uniqueness_key=False,
                regex_pattern=r"^[A-Za-zÀ-ÖØ-öø-ÿ' .\-]{1,100}$",
                standardization_required=True,
                weight=1.5,
                severity="HIGH",
            ),
            "last_name": dict(
                required=True,
                uniqueness_key=False,
                regex_pattern=r"^[A-Za-zÀ-ÖØ-öø-ÿ' .\-]{1,100}$",
                standardization_required=True,
                weight=1.5,
                severity="HIGH",
            ),
            "specialty": dict(
                required=False,
                uniqueness_key=False,
                weight=1.0,
                severity="MEDIUM",
            ),
            "dob": dict(
                required=False,
                uniqueness_key=False,
                regex_pattern=(
                    r"^(?:\d{4}-\d{2}-\d{2}|"
                    r"\d{2}/\d{2}/\d{4}|"
                    r"\d{4}/\d{2}/\d{2})$"
                ),
                standardization_required=False,
                weight=1.0,
                severity="MEDIUM",
            ),
            "email": dict(
                required=False,
                uniqueness_key=False,
                regex_pattern=(
                    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
                    r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
                ),
                standardization_required=False,
                weight=1.0,
                severity="MEDIUM",
            ),
            "phone_number": dict(
                required=False,
                uniqueness_key=False,
                regex_pattern=r"^(?:\+?1[\s.\-]?)?(?:\(?\d{3}\)?[\s.\-]?)\d{3}[\s.\-]?\d{4}$",
                standardization_required=True,
                weight=1.0,
                severity="MEDIUM",
            ),
            "address": dict(
                required=False,
                uniqueness_key=False,
                weight=1.0,
                severity="MEDIUM",
            ),
            "source_system": dict(
                required=False,
                uniqueness_key=False,
                weight=0.5,
                severity="LOW",
            ),
        }

    elif normalized_domain == "SUPPLIER":
        definitions = {
            "supplier_id": dict(
                required=True,
                uniqueness_key=True,
                weight=2.0,
                severity="CRITICAL",
            ),
            "supplier_name": dict(
                required=True,
                uniqueness_key=False,
                weight=1.5,
                severity="HIGH",
            ),
           "tax_id": dict(
                required=False,
                uniqueness_key=False,
                regex_pattern=r"^\d{2}-?\d{7}$",
                standardization_required=True,
                weight=1.5,
                severity="HIGH",
            ),
            "email": dict(
                required=False,
                uniqueness_key=False,
                weight=1.0,
                severity="MEDIUM",
            ),
            "address": dict(
                required=False,
                uniqueness_key=False,
                weight=1.0,
                severity="MEDIUM",
            ),
            "source_system": dict(
                required=False,
                uniqueness_key=False,
                weight=0.5,
                severity="LOW",
            ),
        }

    elif normalized_domain == "PRODUCT":
        definitions = {
            "product_id": dict(
                required=True,
                uniqueness_key=True,
                weight=2.0,
                severity="CRITICAL",
            ),
            "product_name": dict(
                required=True,
                uniqueness_key=False,
                weight=2.0,
                severity="HIGH",
            ),
            "item_category": dict(
                required=False,
                uniqueness_key=False,
                weight=1.0,
                severity="MEDIUM",
            ),
            "product_variant": dict(
                required=False,
                uniqueness_key=False,
                weight=1.0,
                severity="MEDIUM",
            ),
            "sku": dict(
                required=False,
                uniqueness_key=False,
                regex_pattern=r"^[A-Za-z0-9._/\-]{1,100}$",
                standardization_required=False,
                weight=1.5,
                severity="HIGH",
            ),
           "gtin": dict(
                required=False,
                uniqueness_key=False,
                regex_pattern=r"^(?:\d{8}|\d{12}|\d{13}|\d{14})$",
                standardization_required=False,
                weight=1.5,
                severity="HIGH",
),
            "effective_lot_date": dict(
                required=False,
                uniqueness_key=False,
                regex_pattern=r"^\d{4}-\d{2}-\d{2}$",
                standardization_required=True,
                weight=1.0,
                severity="MEDIUM",
),
            "source_system": dict(
                required=False,
                uniqueness_key=False,
                weight=0.5,
                severity="LOW",
            ),
        }

    elif normalized_domain == "BANKING":
        definitions = {
            "account_id": dict(
    required=True,
    uniqueness_key=True,
    regex_pattern=r"^[A-Za-z0-9._\-]{1,100}$",
    standardization_required=True,
    weight=2.0,
    severity="CRITICAL",
),

            "banking_customer_id": dict(
                required=False,
                uniqueness_key=False,
                regex_pattern=r"^[A-Za-z0-9._\-]{1,100}$",
                standardization_required=True,
                weight=2.0,
                severity="CRITICAL",
            ),

            "sap_business_partner_id": dict(
                required=False,
                uniqueness_key=False,
                regex_pattern=r"^[A-Za-z0-9._\-]{1,100}$",
                standardization_required=True,
                weight=2.0,
                severity="CRITICAL",
            ),

            "routing_number": dict(
                required=False,
                uniqueness_key=False,
                regex_pattern=r"^\d{9}$",
                standardization_required=True,
                weight=1.5,
                severity="HIGH",
            ),

            "account_number_last4": dict(
                required=False,
                uniqueness_key=False,
                regex_pattern=r"^\d{4}$",
                standardization_required=True,
                weight=1.0,
                severity="MEDIUM",
            ),

            "account_type": dict(
                required=False,
                uniqueness_key=False,
                allowed_values=(
                    "CHECKING",
                    "SAVINGS",
                    "MONEY_MARKET",
                    "CREDIT",
                    "LOAN",
                    "INVESTMENT",
                    "OTHER",
                ),
                standardization_required=True,
                weight=1.0,
                severity="MEDIUM",
            ),

            "currency": dict(
                required=False,
                uniqueness_key=False,
                regex_pattern=r"^[A-Z]{3}$",
                standardization_required=True,
                weight=1.0,
                severity="MEDIUM",
            ),

            "account_status": dict(
                required=False,
                uniqueness_key=False,
                allowed_values=(
                    "ACTIVE",
                    "INACTIVE",
                    "CLOSED",
                    "FROZEN",
                    "DORMANT",
                    "PENDING",
                ),
                standardization_required=True,
                weight=1.0,
                severity="MEDIUM",
        ),
            "email": dict(
                required=False,
                uniqueness_key=False,
                regex_pattern=(
                    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
                    r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
                ),
                standardization_required=False,
                weight=1.0,
                severity="MEDIUM",
            ),
            "phone_number": dict(
                required=False,
                uniqueness_key=False,
                regex_pattern=r"^(?:\+?1[\s.\-]?)?(?:\(?\d{3}\)?[\s.\-]?)\d{3}[\s.\-]?\d{4}$",
                standardization_required=True,
                weight=1.0,
                severity="MEDIUM",
),
            "address": dict(
                required=False,
                uniqueness_key=False,
                weight=1.0,
                severity="MEDIUM",
            ),
            "source_system": dict(
                required=False,
                uniqueness_key=False,
                weight=0.5,
                severity="LOW",
            ),
        }

    else:
        definitions = {}

    # IMPORTANT:
    # This must be OUTSIDE the if/elif/else chain.
    fields: list[QualityFieldConfig] = []

    for field_name in mapped_fields:
        settings = definitions.get(
            field_name,
            dict(
                required=False,
                uniqueness_key=(
                    field_name
                    == business_key_field
                ),
                weight=1.0,
                severity="MEDIUM",
            ),
        )

        fields.append(
                QualityFieldConfig(
                    field_name=field_name,
                    **settings,
                )
            )

    return QualityProfileConfig(
        domain=normalized_domain,
        source_table=source_name,
        business_key_field=business_key_field,
        fields=fields,
        minimum_record_score=minimum_record_score,
    )