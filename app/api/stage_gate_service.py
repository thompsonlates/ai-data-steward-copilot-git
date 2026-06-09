from fastapi import APIRouter

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
):

    return service.validate(req)