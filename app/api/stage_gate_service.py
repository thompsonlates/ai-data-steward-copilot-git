from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.auth import (
    AuthUser,
    get_current_tenant_user,
)
from app.api.schemas.stage_gate import (
    StageGateRequest,
    StageGateResponse,
)
from app.services.stage_gate_service import (
    StageGateService,
)


router = APIRouter()

service = StageGateService()


@router.post(
    "/validate",
    response_model=StageGateResponse,
)
async def validate_stage_gate(
    req: StageGateRequest,
    current_user: AuthUser = Depends(
        get_current_tenant_user
    ),
) -> StageGateResponse:
    """
    Validate a stage-gate request for the authenticated organization.

    Tenant identity is derived from the authenticated user and is never
    accepted from the request payload.
    """
    return service.validate(
        req,
        organization_id=str(current_user.organization_id),
    )
