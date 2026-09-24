"""Behavior-preserving route extraction from the former monolithic routes.py."""

from app.api.route_dependencies import *
from app.api.route_dependencies import (
    _get_cached_dq_rule_suggestions,
    _set_cached_dq_rule_suggestions,
    _get_onedrive_connector,
)

router = APIRouter()


@router.get("/policy/config", response_model=PolicyConfigResponse)
def get_policy_config(
    domain: str = Query(...),
    policy_version: Optional[str] = Query(None),
    current_user: AuthUser = Depends(get_current_tenant_user),
):
    try:
        result = policy_engine.get_policy_config_bundle(
            domain=domain,
            policy_version=policy_version,
            organization_id=require_current_organization_id(current_user),
        )
        return PolicyConfigResponse(**result)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load policy config: {str(e)}",
        )


@router.post(
    "/policy/config/draft",
    response_model=PolicyDraftResponse,
)
def save_policy_config_draft(
    payload: PolicyDraftRequest,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
):
    try:
        policy_payload = payload.model_dump()

        organization_id = (
            require_current_organization_id(
                current_user
            )
        )

        entitlement_service.require_domain_entitlement(
            organization_id=organization_id,
            domain=payload.domain,
        )

        policy_payload["created_by"] = (
            current_user.email
        )
        policy_payload["updated_by"] = (
            current_user.email
        )

        result = policy_engine.save_policy_draft(
            policy_payload,
            organization_id=organization_id,
        )

        return PolicyDraftResponse(**result)

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Failed to save policy draft: "
                f"{str(exc)}"
            ),
        ) from exc


@router.post(
    "/policy/config/publish",
    response_model=PolicyPublishResponse,
)
def publish_policy_config(
    payload: PolicyPublishRequest,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
):
    try:
        organization_id = (
            require_current_organization_id(
                current_user
            )
        )

        entitlement_service.require_domain_entitlement(
            organization_id=organization_id,
            domain=payload.domain,
        )

        result = policy_engine.publish_policy_version(
            domain=payload.domain,
            policy_version=payload.policy_version,
            organization_id=organization_id,
            published_by=str(current_user.email),
        )

        return PolicyPublishResponse(**result)

    except HTTPException:
        raise

    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Failed to publish policy config: "
                f"{str(exc)}"
            ),
        ) from exc
