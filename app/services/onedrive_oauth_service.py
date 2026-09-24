from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import jwt
import requests
from fastapi import HTTPException, status

from app.repositories.connection_repository import ConnectionRepository


MICROSOFT_GRAPH_SCOPES = [
    "openid",
    "profile",
    "email",
    "offline_access",
    "Files.ReadWrite",
]

MICROSOFT_FILES_WRITE_SCOPE = "Files.ReadWrite"


@dataclass(frozen=True)
class OneDriveOAuthStartResult:
    authorization_url: str
    connection_id: str


@dataclass(frozen=True)
class OneDriveOAuthCallbackResult:
    connection_id: str
    authorized_email: str | None
    return_url: str


class OneDriveOAuthService:
    """
    Delegated Microsoft OAuth for Excel workbooks stored in OneDrive/SharePoint.

    Security model:
      * tenant/organization comes from authenticated server context
      * signed short-lived state binds tenant + connection + return URL
      * refresh token is persisted only through the configured secret manager
      * delegated Files.ReadWrite is required for governed Excel write-back
    """

    STATE_ALGORITHM = "HS256"
    STATE_TTL_MINUTES = 10

    AUTHORITY_BASE = "https://login.microsoftonline.com"
    GRAPH_ME_URL = "https://graph.microsoft.com/v1.0/me"

    def __init__(
        self,
        *,
        repository: ConnectionRepository,
        secret_manager: Any,
        client_id: str | None = None,
        client_secret: str | None = None,
        redirect_uri: str | None = None,
        state_secret: str | None = None,
        tenant_id: str | None = None,
        allowed_return_origins: set[str] | None = None,
    ) -> None:
        self.repository = repository
        self.secret_manager = secret_manager

        self.client_id = (
            client_id
            or os.getenv("MICROSOFT_OAUTH_CLIENT_ID")
            or ""
        )
        self.client_secret = (
            client_secret
            or os.getenv("MICROSOFT_OAUTH_CLIENT_SECRET")
            or ""
        )
        self.redirect_uri = (
            redirect_uri
            or os.getenv("MICROSOFT_OAUTH_REDIRECT_URI")
            or ""
        )
        self.state_secret = (
            state_secret
            or os.getenv("MICROSOFT_OAUTH_STATE_SECRET")
            or ""
        )
        self.tenant_id = (
            tenant_id
            or os.getenv("MICROSOFT_OAUTH_TENANT_ID")
            or "common"
        ).strip()

        configured_origins = (
            os.getenv("MICROSOFT_OAUTH_ALLOWED_RETURN_ORIGINS")
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
    def _require_organization_id(organization_id: str) -> str:
        normalized = str(organization_id or "").strip()
        if not normalized:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authenticated organization ID is missing.",
            )
        return normalized

    @property
    def authorization_endpoint(self) -> str:
        return (
            f"{self.AUTHORITY_BASE}/{self.tenant_id}"
            "/oauth2/v2.0/authorize"
        )

    @property
    def token_endpoint(self) -> str:
        return (
            f"{self.AUTHORITY_BASE}/{self.tenant_id}"
            "/oauth2/v2.0/token"
        )

    def start_authorization(
        self,
        *,
        connection_id: str,
        current_user_email: str,
        organization_id: str,
        return_url: str,
    ) -> OneDriveOAuthStartResult:
        normalized_connection_id = str(connection_id or "").strip()
        normalized_email = str(current_user_email or "").strip().lower()
        effective_organization_id = self._require_organization_id(
            organization_id
        )

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

        safe_return_url = self._validate_return_url(return_url)

        connection = self.repository.get_connection_for_test(
            connection_id=normalized_connection_id,
            organization_id=effective_organization_id,
        )
        if connection is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pending OneDrive connection was not found.",
            )

        self._validate_pending_connection(connection)

        signed_state = self._create_signed_state(
            connection_id=normalized_connection_id,
            organization_id=effective_organization_id,
            created_by=normalized_email,
            return_url=safe_return_url,
        )

        parameters = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "response_mode": "query",
            "scope": " ".join(MICROSOFT_GRAPH_SCOPES),
            "state": signed_state,
            "prompt": "select_account",
            "login_hint": normalized_email,
        }

        authorization_url = (
            self.authorization_endpoint
            + "?"
            + urlencode(parameters)
        )

        return OneDriveOAuthStartResult(
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
    ) -> OneDriveOAuthCallbackResult:
        if oauth_error:
            message = oauth_error_description or oauth_error
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Microsoft authorization failed: {message}",
            )
        if not code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Microsoft authorization code is missing.",
            )
        if not state_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Microsoft OAuth state is missing.",
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
        return_url = self._validate_return_url(
            str(state_payload.get("return_url") or "")
        )

        if not connection_id or not created_by or not organization_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Microsoft OAuth state is incomplete.",
            )

        connection = self.repository.get_connection_for_test(
            connection_id=connection_id,
            organization_id=organization_id,
        )
        if connection is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pending OneDrive connection was not found.",
            )

        self._validate_pending_connection(connection)

        token_response = requests.post(
            self.token_endpoint,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "scope": " ".join(MICROSOFT_GRAPH_SCOPES),
            },
            timeout=30,
        )

        if not token_response.ok:
            try:
                body = token_response.json()
                detail = (
                    body.get("error_description")
                    or body.get("error")
                    or "Unknown token exchange error."
                )
            except Exception:
                detail = "Unknown token exchange error."

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Unable to exchange the Microsoft authorization code: "
                    f"{detail}"
                ),
            )

        token_payload = token_response.json()
        access_token = str(
            token_payload.get("access_token") or ""
        ).strip()
        refresh_token = str(
            token_payload.get("refresh_token") or ""
        ).strip()

        if not access_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Microsoft did not return an access token.",
            )

        granted_scopes = {
            str(scope).strip()
            for scope in str(
                token_payload.get("scope") or ""
            ).split()
            if str(scope).strip()
        }
        if MICROSOFT_FILES_WRITE_SCOPE not in granted_scopes:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Microsoft authorization did not grant Files.ReadWrite. "
                    "Reconnect Microsoft and approve the requested OneDrive "
                    "file access."
                ),
            )

        authorized_email = self._get_authorized_email(access_token)

        secret_id = self.secret_manager.build_secret_id(
            connection_id=connection_id,
            environment=str(
                connection.get("environment") or "PRODUCTION"
            ),
        )

        credential_reference = (
            self.secret_manager.create_or_update_secret(
                secret_id=secret_id,
                credential_payload={
                    "authentication_type": "MICROSOFT_OAUTH",
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "token_url": self.token_endpoint,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scopes": sorted(granted_scopes),
                    "microsoft_files_write_enabled": True,
                    "authorized_email": authorized_email,
                },
            )
        )

        print(
                    "MICROSOFT OAUTH CALLBACK DEBUG 1:",
                    {
                        "connection_id": connection_id,
                        "organization_id": organization_id,
                        "credential_reference": credential_reference,
                        "has_access_token": bool(access_token),
                        "has_refresh_token": bool(refresh_token),
                        "granted_scopes": sorted(granted_scopes),
                    },
        )
        try:
            existing_secret = (
                self.secret_manager.access_secret(
                    credential_reference
                )
                or {}
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unable to retrieve pending Microsoft OAuth credentials.",
            ) from exc

        expires_in = int(token_payload.get("expires_in") or 3600)
        expiry = datetime.now(timezone.utc) + timedelta(
            seconds=max(0, expires_in)
        )

        updated_credentials: dict[str, Any] = {
            **existing_secret,
            "authentication_type": "MICROSOFT_OAUTH",
            "access_token": access_token,
            "refresh_token": (
                refresh_token
                or existing_secret.get("refresh_token")
            ),
            "token_url": self.token_endpoint,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scopes": sorted(granted_scopes),
            "microsoft_files_write_enabled": True,
            "authorized_email": authorized_email,
            "token_expiry": expiry.isoformat(),
            "oauth_connected_at": (
                datetime.now(timezone.utc).isoformat()
            ),
        }

        if not updated_credentials.get("refresh_token"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Microsoft did not return a refresh token. "
                    "Reconnect Microsoft and approve offline access."
                ),
            )

        try:
            self._update_secret(
                credential_reference=credential_reference,
                payload=updated_credentials,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unable to store Microsoft OAuth credentials.",
            ) from exc

        self._complete_repository_connection(
            connection_id=connection_id,
            organization_id=organization_id,
            created_by=created_by,
            credential_reference=credential_reference,
        )

        completed_return_url = self._append_query_parameters(
            return_url,
            {
                "microsoft_oauth": "success",
                "connection_id": connection_id,
                "vendor": "onedrive",
            },
        )

        return OneDriveOAuthCallbackResult(
            connection_id=connection_id,
            authorized_email=authorized_email,
            return_url=completed_return_url,
        )

    def refresh_access_token(
        self,
        *,
        refresh_token: str,
    ) -> dict[str, Any]:
        """
        Refresh helper for the connection/credential service.
        Do not expose refresh tokens to the frontend.
        """
        normalized_refresh_token = str(refresh_token or "").strip()
        if not normalized_refresh_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Microsoft refresh token is missing.",
            )

        response = requests.post(
            self.token_endpoint,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": normalized_refresh_token,
                "scope": " ".join(MICROSOFT_GRAPH_SCOPES),
            },
            timeout=30,
        )

        if not response.ok:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=(
                    "Microsoft OAuth token refresh failed. "
                    "Reconnect Microsoft."
                ),
            )

        return response.json()

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
                    "microsoft_oauth": "failed",
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
                "microsoft_oauth": "failed",
                "message": message,
            },
        )

    def _create_signed_state(
        self,
        *,
        connection_id: str,
        organization_id: str,
        created_by: str,
        return_url: str,
    ) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "iss": "ai-data-steward-copilot",
            "aud": "microsoft-oauth-callback",
            "iat": now,
            "exp": now + timedelta(minutes=self.STATE_TTL_MINUTES),
            "jti": uuid.uuid4().hex,
            "connection_id": connection_id,
            "organization_id": organization_id,
            "created_by": created_by,
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
                audience="microsoft-oauth-callback",
                issuer="ai-data-steward-copilot",
            )
        except jwt.ExpiredSignatureError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Microsoft authorization expired. "
                    "Please start again."
                ),
            ) from exc
        except jwt.InvalidTokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Microsoft OAuth state is invalid.",
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
            "ONEDRIVE",
            "MICROSOFT_ONEDRIVE",
            "SHAREPOINT",
        }:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Microsoft OAuth can only be used with a "
                    "OneDrive/SharePoint connection."
                ),
            )

        if authentication_type not in {
            "MICROSOFT_OAUTH",
            "OAUTH",
            "OAUTH2",
            "U2M",
            "USER_OAUTH",
        }:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "The pending connection is not configured "
                    "for Microsoft user authorization."
                ),
            )

        if not connection.get("is_active", False):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The pending connection is inactive.",
            )

    def _validate_return_url(self, return_url: str) -> str:
        normalized = str(return_url or "").strip()
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
        access_token: str,
    ) -> str | None:
        try:
            response = requests.get(
                self.GRAPH_ME_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
                timeout=20,
            )
            if not response.ok:
                return None

            payload = response.json()
            email = (
                payload.get("mail")
                or payload.get("userPrincipalName")
            )
            return (
                str(email).strip().lower()
                if email
                else None
            )
        except Exception:
            return None

    def _complete_repository_connection(
        self,
        *,
        connection_id: str,
        organization_id: str,
        created_by: str,
        credential_reference: str,
    ) -> None:
        """
        Prefer a Microsoft-specific repository method. The temporary fallback
        keeps this service easy to integrate while the repository is extended.
        """
        complete_method = getattr(
            self.repository,
            "complete_microsoft_oauth_connection",
            None,
        )
        if not callable(complete_method):
            raise RuntimeError(
                "ConnectionRepository must implement "
                "complete_microsoft_oauth_connection()."
            )

        complete_method(
            connection_id=connection_id,
            organization_id=organization_id,
            created_by=created_by,
            credential_reference=credential_reference,
            updated_at=datetime.now(timezone.utc),
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
            parsed._replace(query=urlencode(query))
        )

    def _validate_configuration(self) -> None:
        missing: list[str] = []

        if not self.client_id:
            missing.append("MICROSOFT_OAUTH_CLIENT_ID")
        if not self.client_secret:
            missing.append("MICROSOFT_OAUTH_CLIENT_SECRET")
        if not self.redirect_uri:
            missing.append("MICROSOFT_OAUTH_REDIRECT_URI")
        if not self.state_secret:
            missing.append("MICROSOFT_OAUTH_STATE_SECRET")

        if missing:
            raise RuntimeError(
                "Missing Microsoft OAuth configuration: "
                + ", ".join(missing)
            )
