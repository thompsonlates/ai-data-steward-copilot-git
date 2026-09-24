from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import jwt
from fastapi import HTTPException, status
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from google_auth_oauthlib.flow import Flow

from app.repositories.connection_repository import ConnectionRepository


BIGQUERY_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/bigquery",
    "https://www.googleapis.com/auth/spreadsheets",
]

GOOGLE_SHEETS_WRITE_SCOPE = (
    "https://www.googleapis.com/auth/spreadsheets"
)

@dataclass(frozen=True)
class GoogleOAuthStartResult:
    authorization_url: str
    connection_id: str


@dataclass(frozen=True)
class GoogleOAuthCallbackResult:
    connection_id: str
    authorized_email: str | None
    return_url: str


class GoogleOAuthService:
    STATE_ALGORITHM = "HS256"
    STATE_TTL_MINUTES = 10

    def __init__(
        self,
        *,
        repository: ConnectionRepository,
        secret_manager: Any,
        client_id: str | None = None,
        client_secret: str | None = None,
        redirect_uri: str | None = None,
        state_secret: str | None = None,
        allowed_return_origins: set[str] | None = None,
    ) -> None:
        self.repository = repository
        self.secret_manager = secret_manager

        self.client_id = (
            client_id
            or os.getenv("GOOGLE_OAUTH_CLIENT_ID")
            or ""
        )

        self.client_secret = (
            client_secret
            or os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
            or ""
        )

        self.redirect_uri = (
            redirect_uri
            or os.getenv("GOOGLE_OAUTH_REDIRECT_URI")
            or ""
        )
        

        self.state_secret = (
            state_secret
            or os.getenv("GOOGLE_OAUTH_STATE_SECRET")
            or ""
        )

        configured_origins = (
            os.getenv("GOOGLE_OAUTH_ALLOWED_RETURN_ORIGINS")
            or "http://localhost:5173,http://127.0.0.1:5173"
        )

        self.allowed_return_origins = (
            allowed_return_origins
            or {
                origin.strip().rstrip("/")
                for origin in configured_origins.split(",")
                if origin.strip()
            }
        )

        self._validate_configuration()

    @staticmethod
    def _require_organization_id(
        organization_id: str,
    ) -> str:
        normalized = str(organization_id or "").strip()

        if not normalized:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authenticated organization ID is missing.",
            )

        return normalized

    def start_authorization(
        self,
        *,
        connection_id: str,
        current_user_email: str,
        organization_id: str,
        project_id: str,
        return_url: str,
    ) -> GoogleOAuthStartResult:
        normalized_connection_id = connection_id.strip()
        normalized_email = current_user_email.strip().lower()
        effective_organization_id = self._require_organization_id(
            organization_id
        )
        normalized_project_id = project_id.strip()

        if not normalized_connection_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Connection ID is required.",
            )

        if not normalized_email:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authenticated user email is missing.",
            )

        if not normalized_project_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Google Cloud project ID is required.",
            )

        safe_return_url = self._validate_return_url(return_url)

        connection = self.repository.get_connection_for_test(
            connection_id=normalized_connection_id,
            organization_id=effective_organization_id,
        )

        if connection is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pending BigQuery connection was not found.",
            )

        self._validate_pending_connection(connection)

        signed_state = self._create_signed_state(
            connection_id=normalized_connection_id,
            organization_id=effective_organization_id,
            created_by=normalized_email,
            project_id=normalized_project_id,
            return_url=safe_return_url,
        )

        flow = self._create_flow(state=signed_state)

        authorization_url, _ = flow.authorization_url(
            access_type="offline",
            prompt="consent select_account",
            login_hint=normalized_email,

        )

        print(
            "Google OAuth authorization URL generated:",
            {
                "connection_id": normalized_connection_id,
                "organization_id": effective_organization_id,
                "pkce_enabled": bool(flow.code_verifier),
            },
        )

        return GoogleOAuthStartResult(
            authorization_url=authorization_url,
            connection_id=normalized_connection_id,
        )

    def complete_authorization(
        self,
        *,
        code: str | None,
        state_token: str | None,
        oauth_error: str | None = None,
        oauth_error_description: str | None = None,
    ) -> GoogleOAuthCallbackResult:
        if oauth_error:
            message = oauth_error_description or oauth_error

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Google authorization failed: {message}",
            )

        if not code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Google authorization code is missing.",
            )

        if not state_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Google OAuth state is missing.",
            )

        state_payload = self._decode_signed_state(state_token)

        connection_id = str(
            state_payload.get("connection_id") or ""
        ).strip()

        created_by = str(
            state_payload.get("created_by") or ""
        ).strip().lower()

        organization_id = self._require_organization_id(
            str(state_payload.get("organization_id") or "")
        )

        project_id = str(
            state_payload.get("project_id") or ""
        ).strip()

        return_url = self._validate_return_url(
            str(state_payload.get("return_url") or "")
        )

        if (
            not connection_id
            or not created_by
            or not organization_id
            or not project_id
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Google OAuth state is incomplete.",
            )

        connection = self.repository.get_connection_for_test(
            connection_id=connection_id,
            organization_id=organization_id,
        )

        if connection is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pending BigQuery connection was not found.",
            )

        self._validate_pending_connection(connection)

        flow = self._create_flow(state=state_token)

        try:
            flow.fetch_token(code=code)
        except Exception as exc:
            print(
                "Google OAuth token exchange failed:",
                type(exc).__name__,
                str(exc),
            )

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unable to exchange the Google authorization code.",
            ) from exc

        oauth_credentials = flow.credentials

        if not oauth_credentials.token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Google did not return an access token.",
            )

        granted_scopes = {
            str(scope).strip()
            for scope in (
                oauth_credentials.scopes
                or []
            )
            if str(scope).strip()
        }

        if (
            GOOGLE_SHEETS_WRITE_SCOPE
            not in granted_scopes
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Google authorization did not grant the required "
                    "Google Sheets write scope. Reconnect Google and "
                    "approve the requested Sheets access."
                ),
            )

        authorized_email = self._get_authorized_email(
            oauth_credentials.id_token
        )

        credential_payload = {
            "authentication_type": "GOOGLE_OAUTH",
            "access_token": oauth_credentials.token,
            "refresh_token": oauth_credentials.refresh_token,
            "token_uri": oauth_credentials.token_uri,
            "client_id": oauth_credentials.client_id,
            "client_secret": oauth_credentials.client_secret,
            "scopes": list(oauth_credentials.scopes or []),
            "google_sheets_write_enabled": True,
            "id_token": oauth_credentials.id_token,
            "authorized_email": authorized_email,
        }

        secret_id = self.secret_manager.build_secret_id(
            connection_id=connection_id,
            environment=str(
                connection.get("environment") or "PRODUCTION"
            ),
        )

        credential_reference = (
            self.secret_manager.create_or_update_secret(
                secret_id=secret_id,
                credential_payload=credential_payload,
            )
        )

        try:
            existing_secret = (
                self.secret_manager.access_secret(
                    credential_reference
                )
                or {}
            )
        except Exception as exc:
            print(
                "OAuth secret retrieval failed:",
                type(exc).__name__,
                str(exc),
            )

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unable to retrieve pending connection credentials.",
            ) from exc

        expiry = oauth_credentials.expiry

        if expiry is not None and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)

        updated_credentials: dict[str, Any] = {
            **existing_secret,
            "authentication_type": "GOOGLE_OAUTH",
            "access_token": oauth_credentials.token,
            "refresh_token": (
                oauth_credentials.refresh_token
                or existing_secret.get("refresh_token")
            ),
            "token_url": (
                oauth_credentials.token_uri
                or "https://oauth2.googleapis.com/token"
            ),
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scopes": list(
                oauth_credentials.scopes
                or BIGQUERY_SCOPES
            ),
            "google_sheets_write_enabled": True,
            "project_id": project_id,
            "authorized_email": authorized_email,
            "token_expiry": (
                expiry.isoformat()
                if expiry is not None
                else None
            ),
            "oauth_connected_at": (
                datetime.now(timezone.utc).isoformat()
            ),
        }

        if not updated_credentials.get("refresh_token"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Google did not return a refresh token. "
                    "Revoke the application's prior Google access "
                    "and authorize again."
                ),
            )

        try:
            self._update_secret(
                credential_reference=credential_reference,
                payload=updated_credentials,
            )
        except Exception as exc:
            print(
                "OAuth secret update failed:",
                type(exc).__name__,
                str(exc),
            )

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unable to store Google OAuth credentials.",
            ) from exc

        self.repository.complete_google_oauth_connection(
            connection_id=connection_id,
            organization_id=organization_id,
            created_by=created_by,
            credential_reference=credential_reference,
            updated_at=datetime.now(timezone.utc),
        )

        completed_return_url = self._append_query_parameters(
            return_url,
            {
                "google_oauth": "success",
                "connection_id": connection_id,
                "vendor": "bigquery",
            },
        )

        return GoogleOAuthCallbackResult(
            connection_id=connection_id,
            authorized_email=authorized_email,
            return_url=completed_return_url,
        )

    def build_failure_return_url(
        self,
        *,
        state_token: str | None,
        message: str,
    ) -> str:
        fallback_url = next(
            iter(self.allowed_return_origins),
            "http://localhost:5173",
        )

        if not state_token:
            return self._append_query_parameters(
                fallback_url,
                {
                    "google_oauth": "failed",
                    "message": message,
                },
            )

        try:
            payload = self._decode_signed_state(state_token)

            return_url = self._validate_return_url(
                str(payload.get("return_url") or "")
            )
        except HTTPException:
            return_url = fallback_url

        return self._append_query_parameters(
            return_url,
            {
                "google_oauth": "failed",
                "message": message,
            },
        )

    def _create_flow(
        self,
        *,
        state: str | None = None,
    ) -> Flow:
        client_config = {
            "web": {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "auth_uri": (
                    "https://accounts.google.com/o/oauth2/auth"
                ),
                "token_uri": (
                    "https://oauth2.googleapis.com/token"
                ),
                "redirect_uris": [self.redirect_uri],
            }
        }

        flow = Flow.from_client_config(
            client_config,
            scopes=BIGQUERY_SCOPES,
            state=state,
            autogenerate_code_verifier=False,
        )

        flow.redirect_uri = self.redirect_uri

        return flow

    def _create_signed_state(
        self,
        *,
        connection_id: str,
        organization_id: str,
        created_by: str,
        project_id: str,
        return_url: str,
    ) -> str:
        now = datetime.now(timezone.utc)

        payload = {
            "iss": "ai-data-steward-copilot",
            "aud": "google-oauth-callback",
            "iat": now,
            "exp": now + timedelta(
                minutes=self.STATE_TTL_MINUTES
            ),
            "jti": uuid.uuid4().hex,
            "connection_id": connection_id,
            "organization_id": organization_id,
            "created_by": created_by,
            "project_id": project_id,
            "return_url": return_url,
        }

        return jwt.encode(
            payload,
            self.state_secret,
            algorithm=self.STATE_ALGORITHM,
        )

    def _decode_signed_state(
        self,
        state_token: str,
    ) -> dict[str, Any]:
        try:
            return jwt.decode(
                state_token,
                self.state_secret,
                algorithms=[self.STATE_ALGORITHM],
                audience="google-oauth-callback",
                issuer="ai-data-steward-copilot",
            )
        except jwt.ExpiredSignatureError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Google authorization expired. "
                    "Please start again."
                ),
            ) from exc
        except jwt.InvalidTokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Google OAuth state is invalid.",
            ) from exc

    def _validate_pending_connection(
        self,
        connection: dict[str, Any],
    ) -> None:
        vendor = str(
            connection.get("vendor") or ""
        ).strip().upper()

        authentication_type = str(
            connection.get("authentication_type") or ""
        ).strip().upper()

        if vendor not in {
            "BIGQUERY",
            "GOOGLE_BIGQUERY",
        }:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Google OAuth can only be used with "
                    "a BigQuery connection."
                ),
            )

        if authentication_type not in {
            "GOOGLE_OAUTH",
            "OAUTH",
            "OAUTH2",
            "U2M",
            "USER_OAUTH",
        }:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "The pending connection is not configured "
                    "for Google user authorization."
                ),
            )

        if not connection.get("is_active", False):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The pending connection is inactive.",
            )

    def _validate_return_url(
        self,
        return_url: str,
    ) -> str:
        normalized = return_url.strip()

        if not normalized:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="OAuth return URL is required.",
            )

        parsed = urlparse(normalized)

        if parsed.scheme not in {"http", "https"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="OAuth return URL has an invalid scheme.",
            )

        origin = (
            f"{parsed.scheme}://{parsed.netloc}"
        ).rstrip("/")

        if origin not in self.allowed_return_origins:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="OAuth return URL is not allowed.",
            )

        return normalized

    def _get_authorized_email(
        self,
        encoded_id_token: str | None,
    ) -> str | None:
        if not encoded_id_token:
            return None

        try:
            claims = id_token.verify_oauth2_token(
                encoded_id_token,
                google_requests.Request(),
                self.client_id,
            )
        except Exception as exc:
            print(
                "Google ID token verification failed:",
                type(exc).__name__,
                str(exc),
            )
            return None

        email = claims.get("email")

        return (
            str(email).strip().lower()
            if email
            else None
        )

    def _update_secret(
        self,
        *,
        credential_reference: str,
        payload: dict[str, Any],
    ) -> None:
        secret_version_name = (
            self.secret_manager._reference_to_resource_name(
                credential_reference
            )
        )

        secret_name = secret_version_name.rsplit(
            "/versions/",
            1,
        )[0]

        secret_id = secret_name.rsplit("/", 1)[-1]

        self.secret_manager.create_or_update_secret(
            secret_id=secret_id,
            credential_payload=payload,
        )

    @staticmethod
    def _append_query_parameters(
        url: str,
        parameters: dict[str, str],
    ) -> str:
        parsed = urlparse(url)

        query = dict(parse_qsl(parsed.query))
        query.update(parameters)

        return urlunparse(
            parsed._replace(
                query=urlencode(query)
            )
        )

    def _validate_configuration(self) -> None:
        missing: list[str] = []

        if not self.client_id:
            missing.append("GOOGLE_OAUTH_CLIENT_ID")

        if not self.client_secret:
            missing.append("GOOGLE_OAUTH_CLIENT_SECRET")

        if not self.redirect_uri:
            missing.append("GOOGLE_OAUTH_REDIRECT_URI")

        if not self.state_secret:
            missing.append("GOOGLE_OAUTH_STATE_SECRET")

        if missing:
            raise RuntimeError(
                "Missing Google OAuth configuration: "
                + ", ".join(missing)
            )