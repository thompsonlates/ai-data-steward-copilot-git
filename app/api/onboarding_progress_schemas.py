from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator


class OnboardingProgressStep(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        str_strip_whitespace=True,
    )

    step_id: str
    title: str
    description: str
    completed: bool
    destination: str | None = None


class OnboardingProgressResponse(BaseModel):
    """
    Tenant-scoped onboarding progress response.

    Tenant filtering is enforced in the route/service/repository layers.
    This schema validates that tenant identity is present and follows the
    application's organization ID convention.
    """

    model_config = ConfigDict(
        extra="ignore",
        str_strip_whitespace=True,
    )

    organization_id: str
    completed_steps: int
    total_steps: int
    progress_percentage: int
    onboarding_complete: bool
    steps: list[OnboardingProgressStep]

    @field_validator("organization_id")
    @classmethod
    def _validate_organization_id(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError(
                "organization_id is required."
            )

        if not normalized.startswith("org_"):
            raise ValueError(
                "organization_id must use the org_ identifier standard."
            )

        return normalized

    @field_validator(
        "completed_steps",
        "total_steps",
    )
    @classmethod
    def _validate_non_negative_counts(
        cls,
        value: int,
    ) -> int:
        if value < 0:
            raise ValueError(
                "Onboarding step counts cannot be negative."
            )

        return value

    @field_validator("progress_percentage")
    @classmethod
    def _validate_progress_percentage(
        cls,
        value: int,
    ) -> int:
        if value < 0 or value > 100:
            raise ValueError(
                "progress_percentage must be between 0 and 100."
            )

        return value
