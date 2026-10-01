"""Behavior-preserving route extraction from the former monolithic routes.py."""

from app.api.route_dependencies import *
from app.api.route_dependencies import (
    _get_cached_dq_rule_suggestions,
    _set_cached_dq_rule_suggestions,
    _get_onedrive_connector,
)

from app.api.schemas import EnterpriseConnectionCapabilitiesUpdate

router = APIRouter()


@router.post(
    "/connections/custom-request",
    response_model=CustomConnectionRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_custom_connection_request(
    request: CustomConnectionRequestCreate,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
) -> CustomConnectionRequestResponse:

    organization_id = (
        require_current_organization_id(current_user)
    )

    if not current_user.customer_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "A valid customer membership is required "
                "to request a custom connection."
            ),
        )

    return custom_connection_service.create_request(
        request=request,
        organization_id=organization_id,
        customer_id=current_user.customer_id,
        requested_by=str(current_user.email),
        contact_email=str(current_user.email),
    )


@router.post(
    "/connections",
    response_model=EnterpriseConnectionResponse,
)
def create_enterprise_connection(
    request: EnterpriseConnectionCreate,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
) -> EnterpriseConnectionResponse:

    if not current_user.customer_id:
        raise HTTPException(
            status_code=403,
            detail=(
                "A valid customer membership is required "
                "to create an enterprise connection."
            ),
        )

    return connection_service.create_connection(
        request=request,
        organization_id=require_current_organization_id(
            current_user
        ),
        customer_id=current_user.customer_id,
        created_by=str(current_user.email),
    )


@router.get(
    "/connections",
    response_model=EnterpriseConnectionListResponse,
    status_code=status.HTTP_200_OK,
)
def list_enterprise_connections(
    page: int = Query(
        default=1,
        ge=1,
        description="Page number beginning at 1",
    ),
    page_size: int = Query(
        default=25,
        ge=1,
        le=100,
        description="Number of connections returned per page",
    ),
    current_user: AuthUser = Depends(get_current_tenant_user),
    
) -> EnterpriseConnectionListResponse:

    print(
            "CURRENT USER TENANT:",
            repr(current_user.organization_id),
            flush=True,
        )
    return connection_service.list_connections(
        page=page,
        page_size=page_size,
        organization_id=require_current_organization_id(current_user),
        
    )


@router.get(
    "/connections/{connection_id}",
    response_model=EnterpriseConnectionResponse,
    status_code=status.HTTP_200_OK,
)
def get_enterprise_connection(
    connection_id: str,
    current_user: AuthUser = Depends(get_current_tenant_user),
) -> EnterpriseConnectionResponse:
    return connection_service.get_connection(
        connection_id=connection_id,
        organization_id=require_current_organization_id(current_user),
    )

@router.patch(
    "/connections/{connection_id}/capabilities",
    response_model=EnterpriseConnectionResponse,
    status_code=status.HTTP_200_OK,
)
def update_enterprise_connection_capabilities(
    connection_id: str,
    request: EnterpriseConnectionCapabilitiesUpdate,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
) -> EnterpriseConnectionResponse:
    return connection_service.update_connection_capabilities(
        connection_id=connection_id,
        organization_id=require_current_organization_id(
            current_user
        ),
        connection_capabilities=request.connection_capabilities,
    )

@router.delete(
    "/connections/{connection_id}",
    status_code=status.HTTP_200_OK,
)
def delete_enterprise_connection(
    connection_id: str,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
) -> dict[str, object]:
    connection_service.delete_connection(
        connection_id=connection_id,
        organization_id=require_current_organization_id(
            current_user
        ),
    )

    return {
        "success": True,
        "connection_id": connection_id,
        "message": "Enterprise connection disconnected successfully.",
    }


@router.post(
    "/connections/{connection_id}/test",
    response_model=ConnectionTestResponse,
    status_code=status.HTTP_200_OK,
)
def test_enterprise_connection(
    connection_id: str,
    current_user: AuthUser = Depends(require_active_product_access),
) -> ConnectionTestResponse:
    return connection_service.test_connection(
        connection_id=connection_id,
        organization_id=require_current_organization_id(current_user),
        tested_by=str(current_user.email),
    )
