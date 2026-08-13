from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from app.repositories.entitlement_repository import (
    EntitlementRepository,
)


class EntitlementService:
    def __init__(
        self,
        *,
        repository: EntitlementRepository,
    ) -> None:
        self.repository = repository

    def require_available_seat(
        self,
        *,
        organization_id: str,
        email: str,
    ) -> None:
        normalized_email = str(
            email or ""
        ).strip().lower()

        if not normalized_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="email is required for seat assignment.",
            )

        plan_code = (
            self.repository.get_current_plan_for_organization(
                organization_id=organization_id,
            )
        )

        if not plan_code:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "No active subscription plan was found "
                    "for the authenticated organization."
                ),
            )

        plan = self.repository.get_plan_entitlement(
            plan_code=plan_code,
        )

        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Plan {plan_code} does not have an "
                    "active entitlement configuration."
                ),
            )

        # Idempotent: an existing active/invited user
        # should not consume another seat.
        existing_user = (
            self.repository.get_customer_user_by_email(
                organization_id=organization_id,
                email=normalized_email,
            )
        )

        if existing_user is not None:
            is_active = bool(
                existing_user.get("is_active")
            )

            invitation_status = str(
                existing_user.get(
                    "invitation_status"
                )
                or ""
            ).strip().upper()

            if (
                is_active
                or invitation_status
                in {"PENDING", "INVITED"}
            ):
                return

        max_users = int(
            plan.get("max_users") or 0
        )

        occupied_seats = (
            self.repository.count_occupied_seats(
                organization_id=organization_id,
            )
        )

        if occupied_seats >= max_users:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"{plan_code} includes "
                    f"{max_users} user seat"
                    f"{'' if max_users == 1 else 's'}. "
                    "Upgrade the subscription to add "
                    "additional users."
                ),
            )

    def grant_domain_entitlement(
        self,
        *,
        organization_id: str,
        domain: str,
        granted_by: str,
    ) -> dict[str, Any]:
        normalized_domain = str(
            domain or ""
        ).strip().upper()

        plan_code = (
            self.repository.get_current_plan_for_organization(
                organization_id=organization_id,
            )
        )

        if not plan_code:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "No active subscription plan was found "
                    "for the authenticated organization."
                ),
            )

        normalized_plan_code = str(
            plan_code
        ).strip().upper()

        plan = self.repository.get_plan_entitlement(
            plan_code=normalized_plan_code,
        )

        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Plan {normalized_plan_code} "
                    "does not have an active entitlement "
                    "configuration."
                ),
            )

        # Idempotent: an already-enabled domain does not
        # consume another plan slot.
        existing = self.repository.get_domain_entitlement(
            organization_id=organization_id,
            domain=normalized_domain,
        )

        if existing is not None:
            return existing

        all_domains_enabled = bool(
            plan.get("all_domains_enabled")
        )

        if not all_domains_enabled:
            max_domains = int(
                plan.get("max_domains") or 0
            )

            enabled_count = (
                self.repository.count_enabled_domains(
                    organization_id=organization_id,
                )
            )

            if enabled_count >= max_domains:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"{normalized_plan_code} includes "
                        f"{max_domains} enabled domain"
                        f"{'' if max_domains == 1 else 's'}. "
                        "Upgrade the subscription to enable "
                        "additional domains."
                    ),
                )

        return self.repository.grant_domain_entitlement(
            organization_id=organization_id,
            domain=normalized_domain,
            plan_code=normalized_plan_code,
            granted_by=granted_by,
            entitlement_source="PLAN",
        )

    def require_domain_entitlement(
        self,
        *,
        organization_id: str,
        domain: str,
    ) -> None:
        normalized_domain = str(
            domain or ""
        ).strip().upper()

        try:
            entitlement = (
                self.repository.get_domain_entitlement(
                    organization_id=organization_id,
                    domain=normalized_domain,
                )
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

        # Explicit organization entitlement wins.
        if entitlement is not None:
            return

        # No explicit entitlement. Check whether the current
        # subscription grants every domain automatically.
        plan_code = (
            self.repository.get_current_plan_for_organization(
                organization_id=organization_id,
            )
        )

        if plan_code:
            plan = self.repository.get_plan_entitlement(
                plan_code=plan_code,
            )

            if (
                plan is not None
                and bool(plan.get("all_domains_enabled"))
            ):
                return

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Domain {normalized_domain} is not "
                "enabled for the authenticated "
                "organization's subscription."
            ),
        )