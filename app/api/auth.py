from __future__ import annotations
import time

import re
import requests
import threading

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

from app.repositories.entitlement_repository import (
    EntitlementRepository,
)
from app.repositories.customer_user_repository import (
    CustomerUserRepository,
)
from app.services.entitlement_service import (
    EntitlementService,
)
from app.services.customer_user_service import (
    CustomerUserService,
)


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

entitlement_repository = EntitlementRepository()

entitlement_service = EntitlementService(
    repository=entitlement_repository,
)

customer_user_repository = CustomerUserRepository()

customer_user_service = CustomerUserService(
    repository=customer_user_repository,
    entitlement_service=entitlement_service,
)

if not GOOGLE_CLIENT_ID:
    raise RuntimeError("GOOGLE_CLIENT_ID is not set")

if not APP_JWT_SECRET:
    raise RuntimeError("APP_JWT_SECRET is not set")

MICROSOFT_CLIENT_ID = (
    os.getenv("MICROSOFT_CLIENT_ID") or ""
).strip()

if not MICROSOFT_CLIENT_ID:
    raise RuntimeError("MICROSOFT_CLIENT_ID is not set")


class GoogleAuthRequest(BaseModel):
    credential: str

class MicrosoftAuthRequest(BaseModel):
    credential: str

class AuthUser(BaseModel):
    email: EmailStr
    name: str | None = None
    picture: str | None = None

    auth_provider: str = "GOOGLE"
    provider_subject_id: str

    # Temporary compatibility for existing Google-specific code.
    google_sub: str | None = None

    email_verified: bool = False

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
        cache_ttl_seconds: int = 300,
    ) -> None:
        self.client = client or bigquery.Client(
            project=PROJECT_ID
        )

        self.cache_ttl_seconds = cache_ttl_seconds

        self._cache: dict[
            str,
            tuple[
                float,
                dict[str, str | None] | None,
            ],
        ] = {}

        self._cache_lock = threading.Lock()

    def resolve_by_email(
        self,
        *,
        email: str,
        force_refresh: bool = False,
    ) -> dict[str, str | None] | None:
        normalized_email = email.strip().lower()

        if not normalized_email:
            return None

        now = time.monotonic()

        cached = None

        if not force_refresh:
            with self._cache_lock:
                cached = self._cache.get(
                    normalized_email
                )

        if cached is not None:
            cached_at, cached_value = cached

            if (
                now - cached_at
                < self.cache_ttl_seconds
            ):
                logger.info(
                    "Tenant identity cache hit. "
                    "email=%s",
                    normalized_email,
                )
                return cached_value

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
            AND customer_user.is_active = TRUE
            AND UPPER(
                COALESCE(
                    customer_user.invitation_status,
                    ''
                )
            ) = 'ACCEPTED'
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
            with self._cache_lock:
                self._cache[normalized_email] = (
                    time.monotonic(),
                    None,
                )

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

        result = {
            "organization_id": organization_id,
            "customer_id": customer_id,
            "user_id": user_id,
        }

        with self._cache_lock:
            self._cache[normalized_email] = (
                time.monotonic(),
                result,
            )

        return result


tenant_identity_resolver = TenantIdentityResolver()

bearer_scheme = HTTPBearer(auto_error=False)


def create_app_jwt(user: AuthUser) -> str:
    now = datetime.now(timezone.utc)

    payload: dict[str, Any] = {
        "iss": APP_JWT_ISSUER,
        "aud": APP_JWT_AUDIENCE,
        "sub": user.provider_subject_id,
        "auth_provider": user.auth_provider,
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

    email = str(
        payload.get("email") or ""
    ).strip().lower()

    provider_subject_id = str(
        payload.get("sub") or ""
    ).strip()

    auth_provider = str(
        payload.get("auth_provider") or "GOOGLE"
    ).strip().upper()

    if not email or not provider_subject_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Token is missing required identity fields"
            ),
        )

    google_sub = (
        provider_subject_id
        if auth_provider == "GOOGLE"
        else None
    )

    return AuthUser(
        email=email,
        name=payload.get("name"),
        picture=payload.get("picture"),
        auth_provider=auth_provider,
        provider_subject_id=provider_subject_id,
        google_sub=google_sub,
        email_verified=bool(
            payload.get("email_verified", False)
        ),
        organization_id=(
            str(
                payload["organization_id"]
            ).strip()
            if payload.get("organization_id")
            else None
        ),
        customer_id=(
            str(
                payload["customer_id"]
            ).strip()
            if payload.get("customer_id")
            else None
        ),
        user_id=(
            str(
                payload["user_id"]
            ).strip()
            if payload.get("user_id")
            else None
        ),
    )

def verify_microsoft_id_token(
    credential: str,
) -> dict[str, Any]:
    try:
        logger.warning(
            "Microsoft credential received. length=%s segments=%s",
            len(credential or ""),
            (credential or "").count(".") + 1,
        )
        unverified = jwt.get_unverified_claims(
            credential
        )
        logger.info(
                        "Microsoft ID token claims: "
                        "aud=%s iss=%s tid=%s sub=%s ver=%s",
                        unverified.get("aud"),
                        unverified.get("iss"),
                        unverified.get("tid"),
                        unverified.get("sub"),
                        unverified.get("ver"),
        )

        tenant_id = str(
            unverified.get("tid") or ""
        ).strip()


        if not re.fullmatch(
            r"[0-9a-fA-F-]{36}",
            tenant_id,
        ):
            raise ValueError(
                "Microsoft token tenant ID is invalid."
            )

        metadata_url = (
            "https://login.microsoftonline.com/"
            f"{tenant_id}/v2.0/"
            ".well-known/openid-configuration"
        )

        metadata_response = requests.get(
            metadata_url,
            timeout=10,
        )
        metadata_response.raise_for_status()

        metadata = metadata_response.json()

        issuer = str(
            metadata.get("issuer") or ""
        ).strip()

        jwks_uri = str(
            metadata.get("jwks_uri") or ""
        ).strip()

        if not issuer or not jwks_uri:
            raise ValueError(
                "Microsoft OpenID metadata is incomplete."
            )

        jwks_response = requests.get(
            jwks_uri,
            timeout=10,
        )
        jwks_response.raise_for_status()

        jwks = jwks_response.json()

        header = jwt.get_unverified_header(
            credential
        )

        kid = str(
            header.get("kid") or ""
        ).strip()

        signing_key = next(
            (
                key
                for key in jwks.get("keys", [])
                if str(key.get("kid") or "")
                == kid
            ),
            None,
        )

        if signing_key is None:
            raise ValueError(
                "Microsoft token signing key "
                "was not found."
            )

        claims = jwt.decode(
            credential,
            signing_key,
            algorithms=["RS256"],
            audience=MICROSOFT_CLIENT_ID,
            issuer=issuer,
        )

        token_tenant_id = str(
            claims.get("tid") or ""
        ).strip()

        if token_tenant_id != tenant_id:
            raise ValueError(
                "Microsoft token tenant mismatch."
            )

        return claims

    except Exception as exc:
        logger.warning(
            "Microsoft credential verification failed: %s: %s",
            type(exc).__name__,
            str(exc),
        )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Microsoft credential",
        ) from exc


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

    login_started = time.perf_counter()
    google_started = time.perf_counter()

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

    logger.warning(
        "AUTH TIMING google_verify duration_ms=%.1f",
        (
            time.perf_counter()
            - google_started
        ) * 1000,
    )

    email = str(
        info.get("email") or ""
    ).strip().lower()

    sub = str(
        info.get("sub") or ""
    ).strip()

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

    tenant_started = time.perf_counter()

    tenant_identity = (
        tenant_identity_resolver.resolve_by_email(
            email=email
        )
    )

    logger.warning(
        "AUTH TIMING initial_tenant_resolver "
        "email=%s duration_ms=%.1f found=%s",
        email,
        (
            time.perf_counter()
            - tenant_started
        ) * 1000,
        tenant_identity is not None,
    )

        # Existing users already have a tenant membership.
    # Only check for an invitation when no membership exists.
    if tenant_identity is None:
        invitation_started = time.perf_counter()

        logger.info(
            "No tenant membership found. "
            "Checking pending invitation. email=%s",
            email,
        )

        try:
            activation = (
                customer_user_service
                .activate_pending_invitation(
                    email=email,
                    full_name=info.get("name"),
                    auth_provider="GOOGLE",
                )
            )

            logger.warning(
                "AUTH TIMING invitation_activation "
                "email=%s duration_ms=%.1f activated=%s",
                email,
                (
                    time.perf_counter()
                    - invitation_started
                ) * 1000,
                activation is not None,
            )

        except HTTPException as exc:
            logger.error(
                "INVITATION ACTIVATION HTTP ERROR "
                "email=%s status=%s detail=%s",
                email,
                exc.status_code,
                exc.detail,
            )
            raise

        except Exception as exc:
            logger.exception(
                "INVITATION ACTIVATION ERROR "
                "email=%s type=%s error=%s",
                email,
                type(exc).__name__,
                str(exc),
            )
            raise

        # Re-resolve only after the invitation activation attempt.
        tenant_started = time.perf_counter()

        tenant_identity = (
            tenant_identity_resolver.resolve_by_email(
                email=email,
                force_refresh=True,
    )
)

        logger.warning(
            "AUTH TIMING post_invite_tenant_resolver "
            "email=%s duration_ms=%.1f found=%s",
            email,
            (
                time.perf_counter()
                - tenant_started
            ) * 1000,
            tenant_identity is not None,
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
        auth_provider="GOOGLE",
        provider_subject_id=sub,
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
    "/microsoft",
    response_model=AuthResponse,
)
def microsoft_login(
    payload: MicrosoftAuthRequest,
) -> AuthResponse:
    info = verify_microsoft_id_token(
        payload.credential
    )

    email = str(
        info.get("email")
        or info.get("preferred_username")
        or ""
    ).strip().lower()

    subject_id = str(
        info.get("sub") or ""
    ).strip()

    if not email or not subject_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Missing required Microsoft "
                "identity fields"
            ),
        )

    customer_user_service.activate_pending_invitation(
            email=email,
            full_name=info.get("name"),
            auth_provider="MICROSOFT",
)

    tenant_identity = (
            tenant_identity_resolver.resolve_by_email(
                email=email,
                force_refresh=True,
    )
)

    logger.info(
        "Resolved Microsoft login tenant context. "
        "email=%s organization_id=%s "
        "customer_id=%s user_id=%s",
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
        picture=None,
        auth_provider="MICROSOFT",
        provider_subject_id=subject_id,
        google_sub=None,
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
    started = time.perf_counter()

    email = str(current_user.email).strip().lower()

    tenant_identity = tenant_identity_resolver.resolve_by_email(
            email=email,
            force_refresh=True,
        )

    logger.warning(
        "AUTH TIMING tenant_resolver email=%s duration_ms=%.1f",
        email,
        (time.perf_counter() - started) * 1000,
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
    auth_provider=current_user.auth_provider,
    provider_subject_id=(
        current_user.provider_subject_id
    ),
    google_sub=current_user.google_sub,
    email_verified=current_user.email_verified,
    organization_id=(
        tenant_identity.get("organization_id")
    ),
    customer_id=(
        tenant_identity.get("customer_id")
    ),
    user_id=(
        tenant_identity.get("user_id")
    ),
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
