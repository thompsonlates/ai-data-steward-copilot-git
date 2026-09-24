from __future__ import annotations

from typing import Any
from datetime import datetime, timezone

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

        self.require_active_product_access(
            organization_id=organization_id,
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

            # Existing active/accepted membership is idempotent.
            # A pending invitation does NOT yet occupy a seat,
            # so activation must still pass the seat-limit check.
            if (
                is_active
                and invitation_status == "ACCEPTED"
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

    def require_active_product_access(
        self,
        *,
        organization_id: str,
    ) -> dict[str, Any]:
        normalized_organization_id = str(
            organization_id or ""
        ).strip()

        if not normalized_organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "A valid organization membership "
                    "is required."
                ),
            )

        subscription = (
            self.repository
            .get_current_subscription_for_organization(
                organization_id=(
                    normalized_organization_id
                ),
            )
        )

        if subscription is None:
            raise HTTPException(
                status_code=(
                    status.HTTP_402_PAYMENT_REQUIRED
                ),
                detail={
                    "code": "SUBSCRIPTION_REQUIRED",
                    "subscription_status": "NONE",
                    "trial_expired": False,
                    "message": (
                        "An active subscription or "
                        "trial is required to use "
                        "AI Data Steward Copilot."
                    ),
                },
            )

        subscription_status = str(
            subscription.get(
                "subscription_status"
            )
            or ""
        ).strip().upper()

        now = datetime.now(
            timezone.utc
        )

        # ----------------------------------------
        # Active paid subscription
        # ----------------------------------------

        if subscription_status == "ACTIVE":
            ended_at = subscription.get(
                "ended_at"
            )

            if ended_at is None:
                return subscription

            if ended_at.tzinfo is None:
                ended_at = ended_at.replace(
                    tzinfo=timezone.utc
                )

            if now < ended_at:
                return subscription

        # ----------------------------------------
        # Trial subscription
        # ----------------------------------------

        if subscription_status in {
            "TRIAL",
            "TRIALING",
        }:
            trial_ends_at = subscription.get(
                "trial_ends_at"
            )

            trial_expired_at = (
                subscription.get(
                    "trial_expired_at"
                )
            )

            # Explicit expiration always wins.
            if trial_expired_at is not None:
                raise HTTPException(
                    status_code=(
                        status.HTTP_402_PAYMENT_REQUIRED
                    ),
                    detail={
                        "code": "TRIAL_EXPIRED",
                        "subscription_status":
                            subscription_status,
                        "trial_expired": True,
                        "trial_ends_at": (
                            trial_ends_at.isoformat()
                            if trial_ends_at
                            else None
                        ),
                        "message": (
                            "Your 14-day trial has "
                            "expired. Activate your "
                            "subscription to continue "
                            "using AI Data Steward "
                            "Copilot."
                        ),
                    },
                )

            if trial_ends_at is None:
                raise HTTPException(
                    status_code=(
                        status.HTTP_402_PAYMENT_REQUIRED
                    ),
                    detail={
                        "code":
                            "TRIAL_CONFIGURATION_ERROR",
                        "subscription_status":
                            subscription_status,
                        "trial_expired": False,
                        "message": (
                            "Trial access could not "
                            "be verified."
                        ),
                    },
                )

            if trial_ends_at.tzinfo is None:
                trial_ends_at = (
                    trial_ends_at.replace(
                        tzinfo=timezone.utc
                    )
                )

            if now < trial_ends_at:
                return subscription

            raise HTTPException(
                status_code=(
                    status.HTTP_402_PAYMENT_REQUIRED
                ),
                detail={
                    "code": "TRIAL_EXPIRED",
                    "subscription_status":
                        subscription_status,
                    "trial_expired": True,
                    "trial_ends_at":
                        trial_ends_at.isoformat(),
                    "message": (
                        "Your 14-day trial has expired. "
                        "Activate your subscription "
                        "to continue using AI Data "
                        "Steward Copilot."
                    ),
                },
            )

        # ----------------------------------------
        # Inactive paid states
        # ----------------------------------------

        if subscription_status in {
            "PAST_DUE",
            "UNPAID",
            "CANCELED",
            "CANCELLED",
            "INCOMPLETE",
            "INCOMPLETE_EXPIRED",
            "EXPIRED",
        }:
            raise HTTPException(
                status_code=(
                    status.HTTP_402_PAYMENT_REQUIRED
                ),
                detail={
                    "code":
                        "SUBSCRIPTION_INACTIVE",
                    "subscription_status":
                        subscription_status,
                    "trial_expired": False,
                    "message": (
                        "Your subscription is not "
                        "active. Please update billing "
                        "to continue."
                    ),
                },
            )

        raise HTTPException(
            status_code=(
                status.HTTP_402_PAYMENT_REQUIRED
            ),
            detail={
                "code": "SUBSCRIPTION_REQUIRED",
                "subscription_status": (
                    subscription_status
                    or "UNKNOWN"
                ),
                "trial_expired": False,
                "message": (
                    "An active subscription or "
                    "trial is required to use "
                    "AI Data Steward Copilot."
                ),
            },
        )

    def get_governed_execution_entitlements(
        self,
        *,
        organization_id: str,
    ) -> dict[str, bool]:
        """
        Return governed-remediation entitlements for the
        authenticated organization.

        Active product access is required before feature
        entitlements are evaluated.
        """
        subscription = self.require_active_product_access(
            organization_id=organization_id,
        )

        return {
            "steward_intelligence_enabled": self._as_bool(
                subscription.get(
                    "steward_intelligence_enabled"
                )
            ),
            "governed_regex_execution_enabled": self._as_bool(
                subscription.get(
                    "governed_regex_execution_enabled"
                )
            ),
            "governed_sql_execution_enabled": self._as_bool(
                subscription.get(
                    "governed_sql_execution_enabled"
                )
            ),
            "policy_auto_execution_enabled": self._as_bool(
                subscription.get(
                    "policy_auto_execution_enabled"
                )
            ),
        }

    def require_steward_intelligence(
        self,
        *,
        organization_id: str,
    ) -> None:
        entitlements = self.get_governed_execution_entitlements(
            organization_id=organization_id,
        )

        if not entitlements["steward_intelligence_enabled"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "STEWARD_INTELLIGENCE_REQUIRED",
                    "message": (
                        "Steward Intelligence is required for "
                        "governed remediation execution."
                    ),
                },
            )

    def require_governed_regex_execution(
        self,
        *,
        organization_id: str,
    ) -> None:
        """
        Require Steward Intelligence plus the explicit governed
        REGEX execution entitlement.
        """
        entitlements = self.get_governed_execution_entitlements(
            organization_id=organization_id,
        )

        if not entitlements["steward_intelligence_enabled"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "STEWARD_INTELLIGENCE_REQUIRED",
                    "message": (
                        "Steward Intelligence is required for "
                        "governed remediation execution."
                    ),
                },
            )

        if not entitlements[
            "governed_regex_execution_enabled"
        ]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "GOVERNED_REGEX_EXECUTION_REQUIRED",
                    "message": (
                        "Your current subscription includes "
                        "AI-generated REGEX remediation for "
                        "manual execution. Governed REGEX "
                        "execution requires an upgraded "
                        "Steward Intelligence entitlement."
                    ),
                },
            )

    def require_governed_sql_execution(
        self,
        *,
        organization_id: str,
    ) -> None:
        """
        Require Steward Intelligence plus the explicit governed
        SQL execution entitlement.
        """
        entitlements = self.get_governed_execution_entitlements(
            organization_id=organization_id,
        )

        if not entitlements["steward_intelligence_enabled"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "STEWARD_INTELLIGENCE_REQUIRED",
                    "message": (
                        "Steward Intelligence is required for "
                        "governed remediation execution."
                    ),
                },
            )

        if not entitlements[
            "governed_sql_execution_enabled"
        ]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "GOVERNED_SQL_EXECUTION_REQUIRED",
                    "message": (
                        "Your current subscription includes "
                        "AI-generated SQL remediation for "
                        "manual execution. Governed SQL "
                        "execution requires an upgraded "
                        "Steward Intelligence entitlement."
                    ),
                },
            )

    def require_policy_auto_execution(
        self,
        *,
        organization_id: str,
    ) -> None:
        """
        Require the highest automation entitlement.

        This is intentionally separate from steward-approved
        governed execution.
        """
        entitlements = self.get_governed_execution_entitlements(
            organization_id=organization_id,
        )

        if not entitlements["steward_intelligence_enabled"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "STEWARD_INTELLIGENCE_REQUIRED",
                    "message": (
                        "Steward Intelligence is required for "
                        "policy-controlled automated execution."
                    ),
                },
            )

        if not entitlements[
            "policy_auto_execution_enabled"
        ]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "POLICY_AUTO_EXECUTION_REQUIRED",
                    "message": (
                        "Policy-controlled automated remediation "
                        "is not enabled for this subscription."
                    ),
                },
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

        self.require_active_product_access(
            organization_id=organization_id,
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

        self.require_active_product_access(
            organization_id=organization_id,
        )

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

        if entitlement is not None:
            return

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

    def list_enabled_domains(
        self,
        *,
        organization_id: str,
    ) -> list[str]:
        rows = self.repository.list_enabled_domains(
            organization_id=organization_id,
        )

        return [
            str(row.get("domain") or "")
            .strip()
            .upper()
            for row in rows
            if str(row.get("domain") or "").strip()
        ]

    @staticmethod
    def _as_bool(
        value: Any,
        *,
        default: bool = False,
    ) -> bool:
        if value is None:
            return default

        if isinstance(value, bool):
            return value

        if isinstance(value, (int, float)):
            return value != 0

        if isinstance(value, str):
            normalized = value.strip().lower()

            if normalized in {"true", "1", "yes", "y"}:
                return True

            if normalized in {"false", "0", "no", "n", ""}:
                return False

        return default
