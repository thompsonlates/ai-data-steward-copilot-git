from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.auth import (
    AuthUser,
    get_current_tenant_user,
)
from app.api.workspace_health_schemas import (
    WorkspaceHealthResponse,
)
from app.repositories.workspace_health_repository import (
    WorkspaceHealthRepository,
)
from app.services.workspace_health_service import (
    WorkspaceHealthService,
)


router = APIRouter(
    prefix="/v1/workspace-health",
    tags=["Workspace Health"],
)

repository = WorkspaceHealthRepository()

service = WorkspaceHealthService(
    repository=repository,
)


@router.get(
    "",
    response_model=WorkspaceHealthResponse,
)
def get_workspace_health(
    current_user: AuthUser = Depends(
        get_current_tenant_user
    ),
) -> WorkspaceHealthResponse:
    """
    Return workspace health for the authenticated organization.

    Tenant identity is derived from the authenticated user and passed into
    the service layer. The client cannot choose or override organization_id.
    """
    return service.get_workspace_health(
        current_user_email=str(current_user.email),
        organization_id=str(current_user.organization_id),
    )
