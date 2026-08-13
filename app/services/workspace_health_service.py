from __future__ import annotations

from fastapi import HTTPException, status

from app.api.workspace_health_schemas import (
    WorkspaceHealthResponse,
)
from app.repositories.workspace_health_repository import (
    WorkspaceHealthRepository,
)


class WorkspaceHealthService:
    def __init__(
        self,
        *,
        repository: WorkspaceHealthRepository,
    ) -> None:
        self.repository = repository

    def get_workspace_health(
        self,
        *,
        current_user_email: str,
        organization_id: str,
    ) -> WorkspaceHealthResponse:
        normalized_organization_id = str(
            organization_id or ""
        ).strip()

        if not normalized_organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Authenticated organization context "
                    "is required."
                ),
            )

        metrics = self.repository.get_workspace_health(
            current_user_email=current_user_email,
            organization_id=normalized_organization_id,
        )

        if metrics is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workspace health record was not found.",
            )

        resolved_organization_id = str(
            metrics.get("organization_id") or ""
        ).strip()

        if (
            resolved_organization_id
            != normalized_organization_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Workspace health does not belong to "
                    "the authenticated organization."
                ),
            )

        subscription_active = bool(
            metrics.get("subscription_active")
        )

        active_platforms = int(
            metrics.get("active_platforms") or 0
        )

        total_ai_explanations = int(
            metrics.get("total_ai_explanations") or 0
        )

        readiness_value = metrics.get(
            "average_automation_readiness"
        )

        average_automation_readiness = (
            float(readiness_value)
            if readiness_value is not None
            else None
        )

        configured_governance_policies = int(
            metrics.get("configured_governance_policies")
            or 0
        )

        active_governance_policies = int(
            metrics.get("active_governance_policies")
            or 0
        )

        ai_explanations_today = int(
            metrics.get("ai_explanations_today") or 0
        )

        if active_governance_policies > 0:
            governance_health = "HEALTHY"
        elif configured_governance_policies > 0:
            governance_health = "NEEDS_ATTENTION"
        else:
            governance_health = "NOT_CONFIGURED"

        # Five equally weighted workspace-health categories.
        health_score = 0

        if subscription_active:
            health_score += 20

        if active_platforms > 0:
            health_score += 20

        if total_ai_explanations > 0:
            health_score += 20

        if configured_governance_policies > 0:
            health_score += 20

        if (
            average_automation_readiness is not None
            and average_automation_readiness >= 70
        ):
            health_score += 20
        elif average_automation_readiness is not None:
            health_score += 10

        if health_score >= 90:
            overall_health = "EXCELLENT"
        elif health_score >= 70:
            overall_health = "GOOD"
        elif health_score >= 40:
            overall_health = "NEEDS_ATTENTION"
        else:
            overall_health = "CRITICAL"

        return WorkspaceHealthResponse(
            organization_id=resolved_organization_id,
            organization_name=str(
                metrics.get("organization_name")
                or "Enterprise Workspace"
            ),
            plan_code=str(
                metrics.get("plan_code")
                or "FREE_TRIAL"
            ),
            subscription_active=subscription_active,
            active_platforms=active_platforms,
            total_connection_records=int(
                metrics.get("total_connection_records")
                or 0
            ),
            total_ai_explanations=total_ai_explanations,
            average_automation_readiness=(
                average_automation_readiness
            ),
            active_governance_policies=(
                active_governance_policies
            ),

            ai_explanations_today=ai_explanations_today,
            governance_health=governance_health,
            overall_health_score=health_score,
            overall_health=overall_health,
            configured_governance_policies=(
                configured_governance_policies
            ),
        )