from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status

from app.api.auth import AuthUser
from app.api.schemas import OrganizationOnboardingRequest
from app.repositories.onboarding_repository import OnboardingRepository


logger = logging.getLogger(__name__)


class OnboardingService:
    TRIAL_LENGTH_DAYS = 14

    TRIAL_PLAN = "TRIAL"
    TRIAL_STATUS = "TRIAL"

    TRIAL_MAX_USERS = 3
    TRIAL_MAX_CONNECTIONS = 2
    TRIAL_MAX_MONTHLY_EXPLANATIONS = 100

    def __init__(
        self,
        repository: OnboardingRepository,
    ) -> None:
        self.repository = repository

    @staticmethod
    def _require_organization_id(
        organization_id: str,
    ) -> str:
        normalized = str(organization_id or "").strip()

        if not normalized:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="A valid organization is required.",
            )

        if not normalized.startswith("org_"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The organization identifier is invalid.",
            )

        return normalized

    @staticmethod
    def _generate_organization_id() -> str:
        return f"org_{uuid.uuid4().hex}"

    @staticmethod
    def _generate_customer_id() -> str:
        return f"cust_{uuid.uuid4().hex}"

    @staticmethod
    def _generate_user_id() -> str:
        return f"user_{uuid.uuid4().hex}"

    @staticmethod
    def _generate_subscription_id() -> str:
        return f"sub_{uuid.uuid4().hex}"

    def get_onboarding_status(
        self,
        current_user: AuthUser,
    ) -> dict[str, Any]:
        """
        Determine whether the authenticated user has completed
        organization and subscription onboarding.
        """
        email = str(current_user.email).strip().lower()

        current_organization_id = (
            str(current_user.organization_id).strip()
            if current_user.organization_id
            else None
        )

        user = self.repository.get_user_by_email(
            email=email,
            organization_id=current_organization_id,
        )

        organization = (
            self.repository.get_organization_by_user_email(
                email=email,
                organization_id=current_organization_id,
            )
        )

        subscription = None

        if (
            current_organization_id
            and organization
            and str(organization.get("organization_id") or "").strip()
            != current_organization_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Onboarding organization does not match the "
                    "authenticated tenant."
                ),
            )

        if organization:
            organization_id = organization.get(
                "organization_id"
            )

            if organization_id:
                subscription = (
                    self.repository.get_active_subscription(
                        organization_id=str(organization_id),
                    )
                )

        user_exists = user is not None
        organization_exists = organization is not None
        subscription_exists = subscription is not None

        onboarding_complete = (
            user_exists
            and organization_exists
            and subscription_exists
        )

        if onboarding_complete:
            current_step = "COMPLETE"
        elif not organization_exists:
            current_step = "WELCOME"
        elif not user_exists:
            current_step = "CREATE_USER"
        elif not subscription_exists:
            current_step = "START_TRIAL"
        else:
            current_step = "WELCOME"

        return {
            "user_exists": user_exists,
            "organization_exists": organization_exists,
            "subscription_exists": subscription_exists,
            "onboarding_complete": onboarding_complete,
            "current_step": current_step,
            "organization_id": (
                organization.get("organization_id")
                if organization
                else None
            ),
            "organization_name": (
                organization.get("organization_name")
                if organization
                else None
            ),
            "user_role": (
                organization.get("user_role")
                if organization
                else user.get("role")
                if user
                else None
            ),
            "subscription_plan": (
                subscription.get("plan_name")
                if subscription
                else None
            ),
            "subscription_status": (
                subscription.get("billing_status")
                if subscription
                else None
            ),
            "trial_end_date": self._timestamp_to_iso(
                subscription.get("trial_end_date")
                if subscription
                else None
            ),
        }

    def create_organization_and_owner(
        self,
        current_user: AuthUser,
        request: OrganizationOnboardingRequest,
    ) -> dict[str, Any]:
        """
        Create the customer's organization, owner user, and trial.

        Credentials and authentication information are taken from
        the authenticated application JWT, not from the request body.
        """
        email = str(current_user.email).strip().lower()

        existing_user = self.repository.get_user_by_email(
            email=email,
            organization_id=None,
        )

        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This user is already associated with an "
                    "organization."
                ),
            )

        existing_organization = (
            self.repository.get_organization_by_user_email(
                email=email,
                organization_id=None,
            )
        )

        if existing_organization:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "An organization workspace already exists "
                    "for this user."
                ),
            )

        organization_name = (
            request.organization_name.strip()
        )

        if not organization_name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Organization name is required.",
            )

        now = datetime.now(timezone.utc)
        trial_end = now + timedelta(
            days=self.TRIAL_LENGTH_DAYS
        )

        organization_id = self._generate_organization_id()
        customer_id = self._generate_customer_id()
        user_id = self._generate_user_id()

        organization_record: dict[str, Any] = {
            "organization_id": organization_id,
            "organization_name": organization_name,
            "company_domain": self._clean_optional_string(
                request.company_domain
            ),
            "subscription_plan": self.TRIAL_PLAN,
            "subscription_status": self.TRIAL_STATUS,
            "trial_start_date": now,
            "trial_end_date": trial_end,
            "max_users": self.TRIAL_MAX_USERS,
            "max_connections": self.TRIAL_MAX_CONNECTIONS,
            "industry": self._clean_optional_string(
                request.industry
            ),
            
            "company_size": self._clean_optional_string(
                request.company_size
            ),

            "country": self._clean_optional_string(
                request.country
            ),
            "timezone": (
                self._clean_optional_string(request.timezone)
                or "America/New_York"
            ),
            "created_by": email,
            "created_at": now,
            "updated_at": now,
            "is_active": True,
        }

        user_record: dict[str, Any] = {
            "user_id": user_id,
            "organization_id": organization_id,
            "email": email,
            "full_name": (
                self._clean_optional_string(
                    current_user.name
                )
                or email
            ),
            "role": "OWNER",
            "auth_provider": "GOOGLE",
            "last_login": now,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }

        try:
            saved_organization = (
                self.repository.create_organization(
                    organization_record
                )
            )

            saved_user = self.repository.create_user(
                user_record
            )

            saved_subscription = self.start_trial(
                organization_id=organization_id,
            )

            # Provision the commercial SaaS tenant records used by
            # authentication, billing, connections, and customer membership.
            # The same customer_id / organization_id / user_id are persisted
            # together so subsequent JWTs can carry authoritative tenant
            # context.
            self.repository.provision_commercial_tenant(
                customer_id=customer_id,
                organization_id=organization_id,
                organization_name=organization_name,
                organization_slug=None,
                user_id=user_id,
                owner_email=email,
                display_name=(
                    self._clean_optional_string(
                        current_user.name
                    )
                    or email
                ),
                plan_code=self.TRIAL_PLAN,
                trial_started_at=now,
                trial_ends_at=trial_end,
                seat_limit=self.TRIAL_MAX_USERS,
                connection_limit=self.TRIAL_MAX_CONNECTIONS,
                monthly_explanation_limit=(
                    self.TRIAL_MAX_MONTHLY_EXPLANATIONS
                ),
                monthly_dq_analysis_limit=None,
                created_by=email,
            )

        except HTTPException:
            raise

        except Exception as exc:
            # Do not log request bodies because later onboarding
            # versions may contain sensitive company information.
            logger.exception(
                "Organization onboarding failed: %s",
                type(exc).__name__,
            )

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "Unable to create the organization workspace."
                ),
            ) from exc

        return {
            "customer_id": customer_id,
            "organization_id": saved_organization[
                "organization_id"
            ],
            "organization_name": saved_organization[
                "organization_name"
            ],
            "user_id": saved_user["user_id"],
            "email": saved_user["email"],
            "role": saved_user["role"],
            "subscription_id": saved_subscription[
                "subscription_id"
            ],
            "subscription_plan": saved_subscription[
                "plan_name"
            ],
            "subscription_status": saved_subscription[
                "billing_status"
            ],
            "trial_start_date": now.isoformat(),
            "trial_end_date": self._timestamp_to_iso(
                saved_subscription.get("trial_end_date")
            ),
            "max_users": saved_subscription.get(
                "max_users"
            ),
            "max_connections": saved_subscription.get(
                "max_connections"
            ),
            "max_monthly_explanations": (
                saved_subscription.get(
                    "max_monthly_explanations"
                )
            ),
            "onboarding_complete": True,
            "current_step": "COMPLETE",
        }

    def start_trial(
        self,
        organization_id: str,
    ) -> dict[str, Any]:
        """
        Create the organization's trial subscription.

        This method is idempotent: if an active subscription already
        exists, that subscription is returned instead of creating a
        duplicate.
        """
        normalized_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        existing_subscription = (
            self.repository.get_active_subscription(
                organization_id=normalized_organization_id,
            )
        )

        if existing_subscription:
            return existing_subscription

        now = datetime.now(timezone.utc)
        trial_end = now + timedelta(
            days=self.TRIAL_LENGTH_DAYS
        )

        subscription_record: dict[str, Any] = {
            "subscription_id": (
                self._generate_subscription_id()
            ),
            "organization_id": normalized_organization_id,
            "plan_name": self.TRIAL_PLAN,
            "billing_status": self.TRIAL_STATUS,
            "stripe_customer_id": None,
            "stripe_subscription_id": None,
            "trial_end_date": trial_end,
            "renewal_date": None,
            "max_users": self.TRIAL_MAX_USERS,
            "max_connections": (
                self.TRIAL_MAX_CONNECTIONS
            ),
            "max_monthly_explanations": (
                self.TRIAL_MAX_MONTHLY_EXPLANATIONS
            ),
            "created_at": now,
            "updated_at": now,
        }

        try:
            return self.repository.create_subscription(
                subscription_record
            )

        except Exception as exc:
            logger.exception(
                "Trial creation failed: %s",
                type(exc).__name__,
            )

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unable to start the enterprise trial.",
            ) from exc

    @staticmethod
    def _clean_optional_string(
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        cleaned = value.strip()

        return cleaned or None

    @staticmethod
    def _timestamp_to_iso(
        value: Any,
    ) -> str | None:
        if value is None:
            return None

        if isinstance(value, datetime):
            return value.isoformat()

        return str(value)