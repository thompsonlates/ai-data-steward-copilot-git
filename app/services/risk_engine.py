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
}

RISK_DRIVER_RANK = {
    "member_id": 1,
    "patient_id": 2,
    "human_id": 3,
    "provider_id": 4,
    "supplier_id": 5,
    "product_id": 6,
    "npi": 7,
    "gtin": 8,
    "tax_id": 9,
    "dob": 10,
    "dob_invalid_format": 11,
    "name": 20,
    "product_variant": 25,
    "item_category": 26,
    "product_name": 27,
    "sku": 28,
    "address": 30,
    "email": 40,
    "phone_number": 45,
    "specialty": 50,
    "source_system": 60,
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
