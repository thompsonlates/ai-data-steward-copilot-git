from __future__ import annotations

from fastapi import HTTPException, status

from app.api.onboarding_progress_schemas import (
    OnboardingProgressResponse,
    OnboardingProgressStep,
)
from app.repositories.onboarding_progress_repository import (
    OnboardingProgressRepository,
)


class OnboardingProgressService:
    def __init__(
        self,
        *,
        repository: OnboardingProgressRepository,
    ) -> None:
        self.repository = repository

    def get_progress(
        self,
        *,
        current_user_email: str,
        organization_id: str,
    ) -> OnboardingProgressResponse:
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

        signals = self.repository.get_progress_signals(
            current_user_email=current_user_email,
            organization_id=normalized_organization_id,
        )

        if signals is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    "Onboarding progress was not found for "
                    "the authenticated organization."
                ),
            )

        steps = [
            OnboardingProgressStep(
                step_id="SUBSCRIPTION_ACTIVATED",
                title="Trial workspace activated",
                description=(
                    "Your AI Data Steward Copilot workspace "
                    "and trial access are active."
                ),
                completed=bool(
                    signals.get("subscription_active")
                ),
            ),
            OnboardingProgressStep(
                step_id="PLATFORM_CONNECTED",
                title="Connect your first platform",
                description=(
                    "Connect BigQuery, Databricks, Snowflake, "
                    "or another enterprise platform."
                ),
                completed=bool(
                    signals.get("platform_connected")
                ),
                destination="connections",
            ),
            OnboardingProgressStep(
                step_id="FIRST_EXPLANATION",
                title="Run your first AI Match Explanation",
                description=(
                    "Compare two records and generate an "
                    "explainable AI decision."
                ),
                completed=bool(
                    signals.get("explanation_completed")
                ),
                destination="explain",
            ),
            OnboardingProgressStep(
                step_id="GOVERNANCE_CONFIGURED",
                title="Create your first governance policy",
                description=(
                    "Create an AI governance policy to begin "
                    "monitoring policy, quality, and compliance."
                ),
                completed=bool(
                    signals.get("governance_configured")
                ),
                destination="governance",
            ),
        ]

        completed_steps = sum(
            1 for step in steps if step.completed
        )

        total_steps = len(steps)

        progress_percentage = (
            round(
                completed_steps
                / total_steps
                * 100
            )
            if total_steps > 0
            else 0
        )

        return OnboardingProgressResponse(
            organization_id=str(
                signals["organization_id"]
            ),
            completed_steps=completed_steps,
            total_steps=total_steps,
            progress_percentage=progress_percentage,
            onboarding_complete=(
                completed_steps == total_steps
            ),
            steps=steps,
        )