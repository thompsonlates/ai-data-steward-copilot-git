from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth.transport import requests as google_requests
from google.cloud import bigquery
from google.oauth2 import id_token
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr


BASE_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_CLIENT_ID = (os.getenv("GOOGLE_CLIENT_ID") or "").strip()
APP_JWT_SECRET = (os.getenv("APP_JWT_SECRET") or "").strip()
APP_JWT_ALGORITHM = (
    os.getenv("APP_JWT_ALGORITHM", "HS256").strip()
)
APP_JWT_EXPIRE_MINUTES = int(
    os.getenv(
        "APP_JWT_ACCESS_TOKEN_EXPIRE_MINUTES",
        "30",
    )
)

APP_JWT_ISSUER = (
    os.getenv(
        "APP_JWT_ISSUER",
        "ai-data-steward-copilot",
    ).strip()
)
APP_JWT_AUDIENCE = (
    os.getenv(
        "APP_JWT_AUDIENCE",
        "ai-data-steward-copilot-api",
    ).strip()
)

PROJECT_ID = (
    os.getenv("GOOGLE_CLOUD_PROJECT")
    or os.getenv("PROJECT_ID")
    or "api-project-503305938314"
)

DATASET_ID = (
    os.getenv("BIGQUERY_DATASET")
    or os.getenv("BQ_DATASET")
    or "ai_data_steward_mvp"
)

CUSTOMERS_TABLE = (
    f"{PROJECT_ID}.{DATASET_ID}.CUSTOMERS"
)

CUSTOMER_USERS_TABLE = (
    f"{PROJECT_ID}.{DATASET_ID}.CUSTOMER_USERS"
)

AI_USERS_TABLE = (
    f"{PROJECT_ID}.{DATASET_ID}.AI_USERS"
)

if not GOOGLE_CLIENT_ID:
    raise RuntimeError("GOOGLE_CLIENT_ID is not set")

if not APP_JWT_SECRET:
    raise RuntimeError("APP_JWT_SECRET is not set")


class GoogleAuthRequest(BaseModel):
    credential: str


class AuthUser(BaseModel):
    email: EmailStr
    name: str | None = None
    picture: str | None = None
    google_sub: str
    email_verified: bool = False

    # Tenant identity resolved by the backend.
    organization_id: str | None = None
    customer_id: str | None = None
    user_id: str | None = None


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: AuthUser


class TenantIdentityResolver:
    """
    Resolve SaaS tenant membership from backend-controlled BigQuery data.

    Browser/client payloads are never trusted to establish organization_id.
    """

    def __init__(
        self,
        *,
        client: bigquery.Client | None = None,
    ) -> None:
        self.client = client or bigquery.Client(
            project=PROJECT_ID
        )

    def resolve_by_email(
        self,
        *,
        email: str,
    ) -> dict[str, str | None] | None:
        normalized_email = email.strip().lower()

        if not normalized_email:
            return None

        sql = f"""
        WITH customer_owner AS (
          SELECT
            customer_id,
            organization_id,
            owner_user_id AS user_id,
            1 AS tenant_priority
          FROM `{CUSTOMERS_TABLE}`
          WHERE LOWER(owner_email) = @email
            AND organization_id IS NOT NULL
            AND is_active = TRUE
          QUALIFY ROW_NUMBER() OVER (
            ORDER BY updated_at DESC, created_at DESC
          ) = 1
        ),

        ai_user_membership AS (
            SELECT
                COALESCE(
                customer.customer_id,
                customer_user.customer_id
                ) AS customer_id,
                ai_user.organization_id,
                ai_user.user_id,
                2 AS tenant_priority
            FROM `{AI_USERS_TABLE}` AS ai_user

            LEFT JOIN `{CUSTOMERS_TABLE}` AS customer
                ON customer.organization_id = ai_user.organization_id
            AND customer.is_active = TRUE

            LEFT JOIN `{CUSTOMER_USERS_TABLE}` AS customer_user
                ON customer_user.organization_id = ai_user.organization_id
            AND customer_user.user_id = ai_user.user_id
            AND LOWER(customer_user.email) = @email

            WHERE LOWER(ai_user.email) = @email
                AND ai_user.organization_id IS NOT NULL
                AND ai_user.is_active = TRUE

            QUALIFY ROW_NUMBER() OVER (
                ORDER BY
                ai_user.updated_at DESC,
                ai_user.created_at DESC
            ) = 1
),

        legacy_customer_user AS (
          SELECT
            customer_user.customer_id,
            customer_user.organization_id,
            customer_user.user_id,
            3 AS tenant_priority
          FROM `{CUSTOMER_USERS_TABLE}` AS customer_user
          WHERE LOWER(customer_user.email) = @email
            AND customer_user.organization_id IS NOT NULL
          QUALIFY ROW_NUMBER() OVER (
            ORDER BY customer_user.created_at DESC
          ) = 1
        ),

        candidates AS (
          SELECT * FROM customer_owner
          UNION ALL
          SELECT * FROM ai_user_membership
          UNION ALL
          SELECT * FROM legacy_customer_user
        )

        SELECT
            customer_id,
            organization_id,
            user_id
            FROM candidates
            WHERE organization_id IS NOT NULL
            ORDER BY
            CASE
                WHEN customer_id IS NOT NULL
                AND user_id IS NOT NULL THEN 0
                WHEN customer_id IS NOT NULL THEN 1
                ELSE 2
            END,
            tenant_priority
            LIMIT 1
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "email",
                    "STRING",
                    normalized_email,
                )
            ]
        )

        rows = list(
            self.client.query(
                sql,
                job_config=job_config,
            ).result()
        )

        if not rows:
            return None

        row = rows[0]

        organization_id = (
            str(row["organization_id"]).strip()
            if row["organization_id"] is not None
            else None
        )
        customer_id = (
            str(row["customer_id"]).strip()
            if row["customer_id"] is not None
            else None
        )
        user_id = (
            str(row["user_id"]).strip()
            if row["user_id"] is not None
            else None
        )

        if not organization_id:
            return None

        if not organization_id.startswith("org_"):
            logger.error(
                "Tenant resolver returned an invalid organization_id "
                "for email=%s",
                normalized_email,
            )
            return None

        return {
            "organization_id": organization_id,
            "customer_id": customer_id,
            "user_id": user_id,
        }


tenant_identity_resolver = TenantIdentityResolver()

bearer_scheme = HTTPBearer(auto_error=False)


def create_app_jwt(user: AuthUser) -> str:
    now = datetime.now(timezone.utc)

    payload: dict[str, Any] = {
        "iss": APP_JWT_ISSUER,
        "aud": APP_JWT_AUDIENCE,
        "sub": user.google_sub,
        "email": str(user.email).strip().lower(),
        "name": user.name,
        "picture": user.picture,
        "email_verified": user.email_verified,
        "organization_id": user.organization_id,
        "customer_id": user.customer_id,
        "user_id": user.user_id,
        "iat": int(now.timestamp()),
        "exp": int(
            (
                now
                + timedelta(
                    minutes=APP_JWT_EXPIRE_MINUTES
                )
            ).timestamp()
        ),
    }

    logger.debug(
        "JWT tenant claims prepared. organization_id=%s "
        "customer_id=%s user_id=%s",
        user.organization_id,
        user.customer_id,
        user.user_id,
    )

    return jwt.encode(
        payload,
        APP_JWT_SECRET,
        algorithm=APP_JWT_ALGORITHM,
    )


def _decode_app_jwt(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            APP_JWT_SECRET,
            algorithms=[APP_JWT_ALGORITHM],
            audience=APP_JWT_AUDIENCE,
            issuer=APP_JWT_ISSUER,
        )
    except JWTError as exc:
        logger.warning(
            "Application JWT validation failed: %s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
        ) from exc


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(
        bearer_scheme
    ),
) -> AuthUser:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required",
        )

    token = credentials.credentials.strip()

    payload = _decode_app_jwt(token)

    email = str(payload.get("email") or "").strip().lower()
    google_sub = str(payload.get("sub") or "").strip()

    if not email or not google_sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Token is missing required identity fields"
            ),
        )

    return AuthUser(
        email=email,
        name=payload.get("name"),
        picture=payload.get("picture"),
        google_sub=google_sub,
        email_verified=bool(
            payload.get("email_verified", False)
        ),
        organization_id=(
            str(payload["organization_id"]).strip()
            if payload.get("organization_id")
            else None
        ),
        customer_id=(
            str(payload["customer_id"]).strip()
            if payload.get("customer_id")
            else None
        ),
        user_id=(
            str(payload["user_id"]).strip()
            if payload.get("user_id")
            else None
        ),
    )


def get_current_tenant_user(
    current_user: AuthUser = Depends(get_current_user),
) -> AuthUser:
    """
    Dependency for organization-protected endpoints.

    Requires authenticated organization membership.
    Billing/customer context is validated separately by
    billing-specific service logic when required.
    """

    if not current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "A valid organization membership is required."
            ),
        )

    return current_user

@router.post(
    "/google",
    response_model=AuthResponse,
)
def google_login(
    payload: GoogleAuthRequest,
) -> AuthResponse:
    try:
        info = id_token.verify_oauth2_token(
            payload.credential,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=10,
        )
    except Exception as exc:
        logger.warning(
            "Google credential verification failed: %s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google credential",
        ) from exc

    email = str(info.get("email") or "").strip().lower()
    sub = str(info.get("sub") or "").strip()
    email_verified = bool(
        info.get("email_verified", False)
    )

    if not email or not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Missing required Google identity fields"
            ),
        )

    if not email_verified:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Google email address is not verified"
            ),
        )

    tenant_identity = (
        tenant_identity_resolver.resolve_by_email(
            email=email
        )
    )

    logger.info(
        "Resolved login tenant context. "
        "email=%s organization_id=%s customer_id=%s user_id=%s",
        email,
        (
            tenant_identity.get("organization_id")
            if tenant_identity
            else None
        ),
        (
            tenant_identity.get("customer_id")
            if tenant_identity
            else None
        ),
        (
            tenant_identity.get("user_id")
            if tenant_identity
            else None
        ),
    )

    user = AuthUser(
        email=email,
        name=info.get("name"),
        picture=info.get("picture"),
        google_sub=sub,
        email_verified=True,
        organization_id=(
            tenant_identity.get("organization_id")
            if tenant_identity
            else None
        ),
        customer_id=(
            tenant_identity.get("customer_id")
            if tenant_identity
            else None
        ),
        user_id=(
            tenant_identity.get("user_id")
            if tenant_identity
            else None
        ),
    )
    logger.info(
        "Issuing app JWT. email=%s organization_id=%s "
        "customer_id=%s user_id=%s",
        str(user.email),
        user.organization_id,
        user.customer_id,
        user.user_id,
    )

    app_token = create_app_jwt(user)

    return AuthResponse(
        access_token=app_token,
        expires_in=(
            APP_JWT_EXPIRE_MINUTES * 60
        ),
        user=user,
    )


@router.post(
    "/refresh-context",
    response_model=AuthResponse,
)
def refresh_auth_context(
    current_user: AuthUser = Depends(get_current_user),
) -> AuthResponse:
    """
    Re-resolve tenant membership after onboarding and issue a fresh app JWT.

    This allows a user who originally authenticated before an organization
    existed to transition into the newly created tenant without signing out.
    """
    tenant_identity = tenant_identity_resolver.resolve_by_email(
        email=str(current_user.email)
    )

    if tenant_identity is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "No active organization membership was found "
                "for the authenticated user."
            ),
        )

    refreshed_user = AuthUser(
        email=current_user.email,
        name=current_user.name,
        picture=current_user.picture,
        google_sub=current_user.google_sub,
        email_verified=current_user.email_verified,
        organization_id=tenant_identity.get("organization_id"),
        customer_id=tenant_identity.get("customer_id"),
        user_id=tenant_identity.get("user_id"),
    )

    app_token = create_app_jwt(refreshed_user)

    logger.info(
        "Refreshed tenant context. "
        "email=%s organization_id=%s customer_id=%s user_id=%s",
        str(refreshed_user.email),
        refreshed_user.organization_id,
        refreshed_user.customer_id,
        refreshed_user.user_id,
    )

    return AuthResponse(
        access_token=app_token,
        expires_in=APP_JWT_EXPIRE_MINUTES * 60,
        user=refreshed_user,
    )
