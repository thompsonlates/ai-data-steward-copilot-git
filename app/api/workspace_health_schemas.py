from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


HealthStatus = Literal[
    "EXCELLENT",
    "GOOD",
    "NEEDS_ATTENTION",
    "CRITICAL",
]

GovernanceHealth = Literal[
    "HEALTHY",
    "NEEDS_ATTENTION",
    "NOT_CONFIGURED",
]


class WorkspaceHealthResponse(BaseModel):
    """
    Tenant-scoped workspace health response.

    Tenant filtering is enforced in the authenticated route/service/
    repository layers. This schema validates that the response retains a
    valid organization identity and sane health metrics.
    """

    model_config = ConfigDict(
        extra="ignore",
        str_strip_whitespace=True,
    )

    organization_id: str
    organization_name: str

    plan_code: str
    subscription_active: bool

    configured_governance_policies: int

    active_platforms: int
    total_connection_records: int

    total_ai_explanations: int
    ai_explanations_today: int
    average_automation_readiness: float | None = None

    active_governance_policies: int
    governance_health: GovernanceHealth

    overall_health_score: int
    overall_health: HealthStatus

    @field_validator(
        "organization_id",
        "organization_name",
        "plan_code",
    )
    @classmethod
    def _require_non_empty_identity_fields(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError(
                "Workspace tenant identity fields cannot be empty."
            )

        return normalized

    @field_validator("organization_id")
    @classmethod
    def _validate_organization_id(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()

        if not normalized.startswith("org_"):
            raise ValueError(
                "organization_id must use the org_ identifier standard."
            )

        return normalized

    @field_validator(
        "configured_governance_policies",
        "active_platforms",
        "total_connection_records",
        "total_ai_explanations",
        "active_governance_policies",
    )
    @classmethod
    def _validate_non_negative_counts(
        cls,
        value: int,
    ) -> int:
        if value < 0:
            raise ValueError(
                "Workspace health counts cannot be negative."
            )

        return value

    @field_validator("average_automation_readiness")
    @classmethod
    def _validate_average_automation_readiness(
        cls,
        value: float | None,
    ) -> float | None:
        if value is None:
            return None

        if value < 0 or value > 100:
            raise ValueError(
                "average_automation_readiness must be between 0 and 100."
            )

        return value

    @field_validator("overall_health_score")
    @classmethod
    def _validate_overall_health_score(
        cls,
        value: int,
    ) -> int:
        if value < 0 or value > 100:
            raise ValueError(
                "overall_health_score must be between 0 and 100."
            )

        return value

  