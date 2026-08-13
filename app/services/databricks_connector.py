from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests


logger = logging.getLogger(__name__)

DATABRICKS_USER_AGENT = "ADMS_AIDataStewardCopilot/1.0"


class DatabricksConnector:
    """
    Tenant-bound Databricks connector.

    Tenant isolation is enforced by binding each connector instance to one
    authenticated organization and one connection record. The organization ID
    is an application security boundary only; it is not sent to Databricks.

    In production, host/client credentials should be supplied from the
    authenticated tenant's connection record / secret store. Environment
    fallbacks are retained for local/internal testing only.
    """

    def __init__(
        self,
        *,
        organization_id: str,
        connection_id: str,
        host: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        timeout_seconds: int = 30,
        allow_environment_fallback: bool = False,
    ) -> None:
        self.organization_id = self._require_organization_id(
            organization_id
        )
        self.connection_id = self._require_connection_id(
            connection_id
        )

        if timeout_seconds <= 0:
            raise ValueError(
                "timeout_seconds must be greater than zero."
            )

        self.timeout_seconds = timeout_seconds

        if allow_environment_fallback:
            effective_host = (
                host
                or os.getenv("DATABRICKS_HOST")
                or ""
            )
            effective_client_id = (
                client_id
                or os.getenv("DATABRICKS_CLIENT_ID")
                or ""
            )
            effective_client_secret = (
                client_secret
                or os.getenv("DATABRICKS_CLIENT_SECRET")
                or ""
            )
        else:
            effective_host = host or ""
            effective_client_id = client_id or ""
            effective_client_secret = client_secret or ""

        self.host = self._normalize_host(
            effective_host
        )
        self.client_id = str(
            effective_client_id
        ).strip()
        self.client_secret = str(
            effective_client_secret
        ).strip()

        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

        missing = [
            name
            for name, value in {
                "Databricks host": self.host,
                "Databricks client ID": self.client_id,
                "Databricks client secret": self.client_secret,
            }.items()
            if not value
        ]

        if missing:
            raise ValueError(
                "Missing Databricks configuration: "
                + ", ".join(missing)
            )

    @staticmethod
    def _require_organization_id(
        organization_id: str,
    ) -> str:
        normalized = str(
            organization_id or ""
        ).strip()

        if not normalized:
            raise ValueError(
                "organization_id is required for "
                "tenant-isolated Databricks operations."
            )

        if not normalized.startswith("org_"):
            raise ValueError(
                "organization_id must use the org_ identifier standard."
            )

        return normalized

    @staticmethod
    def _require_connection_id(
        connection_id: str,
    ) -> str:
        normalized = str(
            connection_id or ""
        ).strip()

        if not normalized:
            raise ValueError(
                "connection_id is required for Databricks operations."
            )

        if not normalized.startswith("conn_"):
            raise ValueError(
                "connection_id must use the conn_ identifier standard."
            )

        return normalized

    @staticmethod
    def _normalize_host(
        host: str,
    ) -> str:
        normalized = str(
            host or ""
        ).strip().rstrip("/")

        if not normalized:
            return ""

        if not normalized.startswith(
            ("https://", "http://")
        ):
            normalized = (
                f"https://{normalized}"
            )

        if not normalized.startswith(
            "https://"
        ):
            raise ValueError(
                "Databricks host must use HTTPS."
            )

        return normalized

    def _get_oauth_token(
        self,
    ) -> str:
        if (
            self._access_token
            and time.time()
            < self._token_expires_at - 60
        ):
            return self._access_token

        try:
            response = requests.post(
                f"{self.host}/oidc/v1/token",
                auth=(
                    self.client_id,
                    self.client_secret,
                ),
                data={
                    "grant_type": "client_credentials",
                    "scope": "all-apis",
                },
                headers={
                    "User-Agent": (
                        DATABRICKS_USER_AGENT
                    ),
                    "Content-Type": (
                        "application/x-www-form-urlencoded"
                    ),
                },
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            logger.warning(
                "Databricks OAuth request failed. "
                "organization_id=%s connection_id=%s "
                "error=%s",
                self.organization_id,
                self.connection_id,
                type(exc).__name__,
            )
            raise RuntimeError(
                "Unable to reach Databricks OAuth endpoint."
            ) from exc

        if not response.ok:
            logger.warning(
                "Databricks OAuth request rejected. "
                "organization_id=%s connection_id=%s "
                "status_code=%s",
                self.organization_id,
                self.connection_id,
                response.status_code,
            )
            raise RuntimeError(
                "Databricks OAuth token request failed "
                f"with HTTP {response.status_code}."
            )

        try:
            token_payload = response.json()
        except ValueError as exc:
            raise RuntimeError(
                "Databricks OAuth response was not valid JSON."
            ) from exc

        access_token = str(
            token_payload.get("access_token")
            or ""
        ).strip()

        if not access_token:
            raise RuntimeError(
                "Databricks OAuth response did not "
                "include access_token."
            )

        try:
            expires_in = int(
                token_payload.get(
                    "expires_in",
                    3600,
                )
            )
        except (TypeError, ValueError):
            expires_in = 3600

        expires_in = max(
            expires_in,
            60,
        )

        self._access_token = access_token
        self._token_expires_at = (
            time.time() + expires_in
        )

        logger.info(
            "Databricks OAuth token acquired. "
            "organization_id=%s connection_id=%s "
            "expires_in=%s",
            self.organization_id,
            self.connection_id,
            expires_in,
        )

        return access_token

    def _headers(
        self,
    ) -> dict[str, str]:
        return {
            "Authorization": (
                f"Bearer {self._get_oauth_token()}"
            ),
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": DATABRICKS_USER_AGENT,
        }

    def list_catalogs(
        self,
    ) -> dict[str, Any]:
        """
        List catalogs visible to this tenant's Databricks service principal.

        Cross-tenant isolation is provided by using credentials resolved from
        the authenticated organization's connection record. This method does
        not accept organization_id from the caller and cannot switch tenants.
        """
        endpoint = (
            f"{self.host}"
            "/api/2.1/unity-catalog/catalogs"
        )

        logger.info(
            "Databricks catalog request started. "
            "organization_id=%s connection_id=%s",
            self.organization_id,
            self.connection_id,
        )

        try:
            response = requests.get(
                endpoint,
                headers=self._headers(),
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            logger.warning(
                "Databricks catalog request failed. "
                "organization_id=%s connection_id=%s "
                "error=%s",
                self.organization_id,
                self.connection_id,
                type(exc).__name__,
            )
            raise RuntimeError(
                "Unable to reach Databricks catalog endpoint."
            ) from exc

        if not response.ok:
            logger.warning(
                "Databricks catalog request rejected. "
                "organization_id=%s connection_id=%s "
                "status_code=%s",
                self.organization_id,
                self.connection_id,
                response.status_code,
            )
            raise RuntimeError(
                "Databricks catalog request failed "
                f"with HTTP {response.status_code}."
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(
                "Databricks catalog response was not valid JSON."
            ) from exc

        if not isinstance(payload, dict):
            raise RuntimeError(
                "Databricks catalog response had an unexpected shape."
            )

        return payload
