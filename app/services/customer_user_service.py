from __future__ import annotations

from fastapi import HTTPException, status

from app.repositories.customer_user_repository import (
    CustomerUserRepository,
)
from app.services.entitlement_service import (
    EntitlementService,
)


class CustomerUserService:
    def __init__(
        self,
        *,
        repository: CustomerUserRepository,
        entitlement_service: EntitlementService,
    ) -> None:
        self.repository = repository
        self.entitlement_service = entitlement_service

    def invite_user(
        self,
        *,
        organization_id: str,
        email: str,
        display_name: str | None,
        organization_role: str,
        billing_role: str,
        invited_by: str,
    ):
        self.entitlement_service.require_available_seat(
            organization_id=organization_id,
            email=email,
        )

        customer_id = (
            self.repository.get_customer_id_for_organization(
                organization_id=organization_id,
            )
        )

        if not customer_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    "No active customer record was found "
                    "for the authenticated organization."
                ),
            )

        return self.repository.create_invitation(
            organization_id=organization_id,
            customer_id=customer_id,
            email=email,
            display_name=display_name,
            organization_role=organization_role,
            billing_role=billing_role,
            created_by=invited_by,
        )