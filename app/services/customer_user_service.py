from __future__ import annotations

from fastapi import HTTPException, status

from app.repositories.customer_user_repository import (
    CustomerUserRepository,
)
from app.services.entitlement_service import (
    EntitlementService,
)

import uuid

import logging

from app.services.email_service import EmailService

logger = logging.getLogger(__name__)


class CustomerUserService:
    def __init__(
        self,
        *,
        repository: CustomerUserRepository,
        entitlement_service: EntitlementService,
        email_service: EmailService | None = None,
    ) -> None:
        self.repository = repository
        self.entitlement_service = entitlement_service
        self.email_service = email_service

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

        invitation = self.repository.create_invitation(
            organization_id=organization_id,
            customer_id=customer_id,
            email=email,
            display_name=display_name,
            organization_role=organization_role,
            billing_role=billing_role,
            created_by=invited_by,
)

        if self.email_service is not None:
            try:
                self.email_service.send_user_invitation(
                    to_email=email,
                    invite_sender_name=invited_by,
                    invite_sender_organization_name=(
                        self.repository
                        .get_organization_name_for_organization(
                            organization_id=organization_id,
                        )
                        or "Your organization"
                    ),
                    action_url=self.email_service.app_url,
                )
            except Exception as email_exc:
                logger.exception(
                    "User invitation email failed after "
                    "invitation creation: %s",
                    type(email_exc).__name__,
                )
                raise

            return invitation

    def activate_pending_invitation(
        self,
        *,
        email: str,
        full_name: str | None,
        auth_provider: str,
    ) -> dict | None:
        normalized_email = str(
            email or ""
        ).strip().lower()

        if not normalized_email:
            return None

        # ---------------------------------------------------------
        # 1. Existing active users do NOT need invitation activation
        # ---------------------------------------------------------
        existing_user = (
            self.repository.get_active_user_by_email(
                email=normalized_email,
            )
        )

        if existing_user is not None:
            logger.info(
                "Skipping invitation activation because user "
                "is already active. email=%s organization_id=%s",
                normalized_email,
                existing_user.get("organization_id"),
            )

            return existing_user

        # ---------------------------------------------------------
        # 2. New users may have a pending invitation
        # ---------------------------------------------------------
        invitation = (
            self.repository.get_pending_invitation_by_email(
                email=normalized_email,
            )
        )

        if invitation is None:
            logger.info(
                "No pending invitation found for email=%s",
                normalized_email,
            )
            return None

        organization_id = str(
            invitation.get("organization_id")
            or ""
        ).strip()

        if not organization_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Pending invitation does not have "
                    "a valid organization."
                ),
            )

        # ---------------------------------------------------------
        # 3. Confirm tenant has capacity before activation
        # ---------------------------------------------------------
        self.entitlement_service.require_available_seat(
            organization_id=organization_id,
            email=normalized_email,
        )

        user_id = f"user_{uuid.uuid4().hex[:24]}"

        organization_role = str(
            invitation.get("organization_role")
            or "STEWARD"
        ).strip().upper()

        # ---------------------------------------------------------
        # 4. Convert invitation into active tenant membership
        # ---------------------------------------------------------
        activated_user = (
            self.repository.activate_invitation(
                customer_user_id=str(
                    invitation["customer_user_id"]
                ),
                organization_id=organization_id,
                user_id=user_id,
                email=normalized_email,
                full_name=full_name,
                organization_role=organization_role,
                auth_provider=str(
                    auth_provider or ""
                ).strip().upper(),
            )
        )

        logger.info(
            "Invitation activated successfully. "
            "email=%s organization_id=%s user_id=%s",
            normalized_email,
            organization_id,
            user_id,
        )

        return activated_user