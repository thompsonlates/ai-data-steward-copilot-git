from __future__ import annotations

import re
from typing import Any, Dict


HIGH_RISK_CONFLICTS = {
    "PROVIDER": {"npi"},
    "PATIENT": {
        "patient_id",
        "member_id",
        "human_id",
        "mrn",
        "dob",
        "ssn",
    },
    "SUPPLIER": {
        "tax_id",
        "supplier_id",
    },
    "PRODUCT": {
        "gtin",
        "product_id",
    },
    "CUSTOMER": {
        "member_id",
        "human_id",
    },
      "BANKING": {
        "account_id",
        "banking_customer_id",
        "sap_business_partner_id",
        "routing_number",
    },
}

MEDIUM_RISK_CONFLICTS = {
    "PROVIDER": {
        "specialty",
        "provider_id",
        "provider_address",
        "phone_number",
        "provider_email",
    },
    "PATIENT": {
        "patient_address",
        "patient_email",
    },
    "SUPPLIER": {
        "supplier_address",
        "contact_email",
    },
    "PRODUCT": {
        "sku",
        "product_name",
        "product_variant",
        "item_category",
    },
    "CUSTOMER": {
        "address",
        "email",
    },
     "BANKING": {
        "account_number_last4",
        "institution_name",
        "account_type",
        "currency",
        "account_status",
        "email",
        "phone_number",
    },
}

RISK_DRIVER_RANK = {
    "banking_identity": 0,
    "account_id": 1,
    "banking_customer_id": 2,
    "sap_business_partner_id": 3,
    "routing_number": 4,

    "member_id": 5,
    "patient_id": 6,
    "human_id": 7,
    "provider_id": 8,
    "supplier_id": 9,
    "product_id": 10,
    "npi": 11,
    "gtin": 12,
    "tax_id": 13,
    "dob": 14,
    "dob_invalid_format": 15,

    "account_number_last4": 20,
    "institution_name": 21,
    "account_type": 22,
    "currency": 23,
    "account_status": 24,

    "name": 30,
    "product_variant": 35,
    "item_category": 36,
    "product_name": 37,
    "sku": 38,
    "address": 40,
    "email": 50,
    "phone_number": 55,
    "specialty": 60,
    "source_system": 70,
}


def _val(
    record: Any,
    field: str,
) -> str:
    if not record:
        return ""

    if isinstance(record, dict):
        value = record.get(field)
    else:
        value = getattr(
            record,
            field,
            None,
        )

    if value in {
        None,
        "",
    }:
        return ""

    if field == "phone_number":
        digits = re.sub(
            r"\D",
            "",
            str(value),
        )

        if (
            len(digits) == 11
            and digits.startswith("1")
        ):
            digits = digits[1:]

        return digits

    if field in {
        "routing_number",
        "account_number_last4",
    }:
        return re.sub(
            r"\D",
            "",
            str(value),
        )

    return str(value).strip().lower()

def normalize_risk_driver(
    driver: str,
) -> str:
    return (
        str(driver or "")
        .strip()
        .lower()
        .replace("_conflict", "")
        .replace("_match", "")
    )


def sort_risk_drivers(
    drivers: list[str],
) -> list[str]:
    return sorted(
        list(
            dict.fromkeys(drivers)
        ),
        key=lambda driver: (
            RISK_DRIVER_RANK.get(
                normalize_risk_driver(driver),
                999,
            )
        ),
    )


def _conflicts(
    record_a: Any,
    record_b: Any,
    field: str,
) -> bool:
    a = _val(
        record_a,
        field,
    )
    b = _val(
        record_b,
        field,
    )

    return bool(
        a
        and b
        and a != b
    )


def _has_invalid_dob_signal(
    signal_packets: list[
        Dict[str, Any]
    ]
    | None,
) -> bool:
    if not signal_packets:
        return False

    for signal in signal_packets:
        name = str(
            signal.get("signal_name")
            or ""
        ).lower()

        detail = str(
            signal.get("detail")
            or ""
        ).lower()

        if (
            name == "dob_match"
            and (
                "invalid" in detail
                or "not a valid" in detail
                or "data quality" in detail
            )
        ):
            return True

    return False


def evaluate_risk(
    domain: str,
    record_a: Any,
    record_b: Any,
    signal_packets: list[
        Dict[str, Any]
    ]
    | None = None,
    recommended_action: str | None = None,
) -> Dict[str, Any]:
    """
    Evaluate record-level match risk.

    This module is intentionally tenant-agnostic because it performs only
    in-memory calculations and does not read or write organization data.
    Tenant isolation must happen before tenant-owned records are passed into
    this function.
    """
    normalized_domain = (
        domain or "CUSTOMER"
    ).upper()

    high_fields = (
        HIGH_RISK_CONFLICTS.get(
            normalized_domain,
            set(),
        )
    )

    medium_fields = (
        MEDIUM_RISK_CONFLICTS.get(
            normalized_domain,
            set(),
        )
    )

    high_conflicts = [
        field
        for field in high_fields
        if _conflicts(
            record_a,
            record_b,
            field,
        )
    ]

    medium_conflicts = [
        field
        for field in medium_fields
        if _conflicts(
            record_a,
            record_b,
            field,
        )
    ]

    risk_score = 0

    risk_drivers = (
        high_conflicts
        + medium_conflicts
    )

    if high_conflicts:
        risk_score += 70

    if medium_conflicts:
        risk_score += 20

        if normalized_domain == "BANKING":
            banking_identity_conflicts = {
                "account_id",
                "banking_customer_id",
                "sap_business_partner_id",
                "routing_number",
            }.intersection(
                high_conflicts
            )

            # Multiple conflicting banking identity anchors
            # represent a severe consolidation risk.
            if len(banking_identity_conflicts) >= 2:
                risk_score = max(
                    risk_score,
                    90,
                )
                risk_drivers.append(
                    "banking_identity"
                )

            # Routing + last-four conflict is stronger
            # than either supporting field independently.
            if (
                "routing_number"
                in high_conflicts
                and "account_number_last4"
                in medium_conflicts
            ):
                risk_score = max(
                    risk_score,
                    85,
                )

    if (
        normalized_domain
        in {"PATIENT", "CUSTOMER"}
        and _has_invalid_dob_signal(
            signal_packets
        )
    ):
        risk_score = max(
            risk_score,
            75,
        )
        risk_drivers.append(
            "dob_invalid_format"
        )

    normalized_recommended_action = (
        str(
            recommended_action
            or ""
        )
        .strip()
        .upper()
    )

    if normalized_recommended_action in {
        "BLOCK_MERGE",
        "REJECT_MERGE",
    }:
        risk_score += 10

    risk_score = min(
        risk_score,
        100,
    )

    sorted_drivers = (
        sort_risk_drivers(
            risk_drivers
        )
    )

    if risk_score >= 70:
        risk_flag = "HIGH"
    elif risk_score >= 35:
        risk_flag = "MEDIUM"
    else:
        risk_flag = "LOW"

    primary_risk_driver = (
        (
            f"{sorted_drivers[0].upper()}"
            "_CONFLICT"
        )
        if sorted_drivers
        else "NO_MAJOR_CONFLICT"
    )

    return {
        "risk_score": risk_score,
        "risk_flag": risk_flag,
        "primary_risk_driver": (
            primary_risk_driver
        ),
        "high_conflicts": (
            high_conflicts
        ),
        "medium_conflicts": (
            medium_conflicts
        ),
        "risk_drivers": (
            sorted_drivers
        ),
    }
