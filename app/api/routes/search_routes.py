"""Behavior-preserving route extraction from the former monolithic routes.py."""

from app.api.route_dependencies import *
from app.api.route_dependencies import (
    _get_cached_dq_rule_suggestions,
    _set_cached_dq_rule_suggestions,
    _get_onedrive_connector,
)

router = APIRouter()


@router.get(
    "/records/search",
    response_model=RecordSearchResponse,
)
async def search_records(
    domain: str,
    q: str,
    current_user: AuthUser = Depends(
        get_current_tenant_user
    ),
):
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    rows = record_search_service.search_records(
        domain=domain,
        search_text=q,
        organization_id=organization_id,
    )

    return {
        "organization_id": organization_id,
        "results": [
            {
                "organization_id": organization_id,
                "record_id": row["record_id"],
                "mdm_id": row["mdm_id"],
                "domain": row["domain"],
                "display_name": row["display_name"],
                "source_system": row["source_system"],
                "golden_record_flag": row[
                    "golden_record_flag"
                ],
                "human_id": row.get("human_id"),
                "phone_number": row.get(
                    "phone_number"
                ),
                "record": {
                    "member_id": row.get(
                        "member_id"
                    ),
                    "patient_id": row.get(
                        "patient_id"
                    ),
                    "provider_id": row.get(
                        "provider_id"
                    ),
                    "supplier_id": row.get(
                        "supplier_id"
                    ),
                    "product_id": row.get(
                        "product_id"
                    ),

                    # Supplier fields
                    "supplier_name": row.get(
                        "supplier_name"
                    ),
                    "supplier_name_line_1": (
                        row.get(
                            "supplier_name_line_1"
                        )
                        or row.get(
                            "supplier_name"
                        )
                    ),
                    "supplier_name_line_2": (
                        row.get(
                            "supplier_name_line_2"
                        )
                    ),
                    "contact_email": (
                        row.get(
                            "contact_email"
                        )
                        or row.get("email")
                    ),
                    "supplier_address": (
                        row.get(
                            "supplier_address"
                        )
                        or row.get("address")
                    ),

                    "first_name": row.get(
                        "first_name"
                    ),
                    "last_name": row.get(
                        "last_name"
                    ),
                    "email": row.get("email"),
                    "address": row.get(
                        "address"
                    ),
                    "dob": row.get("dob"),
                    "npi": row.get("npi"),
                    "phone_number": row.get(
                        "phone_number"
                    ),
                    "specialty": row.get(
                        "specialty"
                    ),
                    "tax_id": row.get(
                        "tax_id"
                    ),
                    "gtin": row.get("gtin"),
                    "sku": row.get("sku"),
                    "product_name": row.get(
                        "product_name"
                    ),
                    "product_variant": row.get(
                        "product_variant"
                    ),
                    "effective_lot_date": (
                        row.get(
                            "effective_lot_date"
                        )
                    ),
                    "item_category": row.get(
                        "item_category"
                    ),
                    "source_system": row.get(
                        "source_system"
                    ),
                },
            }
            for row in rows
        ],
    }
