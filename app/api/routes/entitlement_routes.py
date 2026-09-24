"""Behavior-preserving route extraction from the former monolithic routes.py."""

from app.api.route_dependencies import *
from app.api.route_dependencies import (
    _get_cached_dq_rule_suggestions,
    _set_cached_dq_rule_suggestions,
    _get_onedrive_connector,
)

router = APIRouter()


@router.get("/entitlements/domains")
def get_enabled_domains(
    current_user: AuthUser = Depends(
        get_current_tenant_user
    ),
):
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    enabled_domains = (
        entitlement_service.list_enabled_domains(
            organization_id=organization_id,
        )
    )

    return {
        "enabled_domains": enabled_domains,
    }


@router.post(
    "/entitlements/domains/{domain}",
    response_model=DomainEntitlementResponse,
)
def grant_domain_entitlement(
    domain: str,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
) -> DomainEntitlementResponse:
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    result = (
        entitlement_service.grant_domain_entitlement(
            organization_id=organization_id,
            domain=domain,
            granted_by=str(current_user.email),
        )
    )

    return DomainEntitlementResponse(
        **result
    )
