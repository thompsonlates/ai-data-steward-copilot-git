from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import json
from typing import Any
from urllib.parse import urlparse

import requests
import snowflake.connector
from cryptography.hazmat.primitives import serialization


class SnowflakeConnectionConfigurationError(ValueError):
    """Raised when persisted Snowflake authentication settings are incomplete."""


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def flatten_snowflake_credentials(credentials: dict[str, Any]) -> dict[str, Any]:
    values = dict(credentials or {})
    additional = values.get("additional_properties")
    if isinstance(additional, dict):
        for key, value in additional.items():
            if not values.get(key):
                values[key] = value
    return values


def _required(values: dict[str, Any], *names: str) -> None:
    missing = [name for name in names if not values.get(name)]
    if missing:
        raise SnowflakeConnectionConfigurationError(
            "Snowflake credentials are incomplete. Missing: " + ", ".join(missing)
        )


def _account(connection: dict[str, Any], details: dict[str, Any], values: dict[str, Any]) -> str:
    account = str(
        values.get("account")
        or details.get("account")
        or connection.get("workspace_name")
        or ""
    ).strip()
    if account:
        return account

    endpoint = str(connection.get("api_endpoint") or "").strip()
    if endpoint:
        host = urlparse(endpoint if "://" in endpoint else f"https://{endpoint}").hostname
        if host and host.lower().endswith(".snowflakecomputing.com"):
            return host[: -len(".snowflakecomputing.com")]
    return ""


def _oauth_token(values: dict[str, Any], *, timeout: int) -> str:
    existing_token = str(values.get("access_token") or values.get("token") or "").strip()
    if existing_token:
        return existing_token

    token_url = str(values.get("token_url") or "").strip()
    client_id = str(values.get("client_id") or "").strip()
    client_secret = str(values.get("client_secret") or "")
    _required(
        {"token_url": token_url, "client_id": client_id, "client_secret": client_secret},
        "token_url",
        "client_id",
        "client_secret",
    )

    scope_value = values.get("scope") or values.get("scopes") or "session:role-any"
    if isinstance(scope_value, (list, tuple, set)):
        scope = " ".join(str(item).strip() for item in scope_value if str(item).strip())
    else:
        scope = str(scope_value).strip()

    response = requests.post(
        token_url,
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials", "scope": scope},
        headers={"Accept": "application/json"},
        timeout=timeout,
    )
    if not response.ok:
        raise SnowflakeConnectionConfigurationError(
            f"Snowflake OAuth token request returned HTTP {response.status_code}."
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise SnowflakeConnectionConfigurationError(
            "Snowflake OAuth token response was not valid JSON."
        ) from exc

    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise SnowflakeConnectionConfigurationError(
            "Snowflake OAuth token response contained no access_token."
        )
    return token


def _private_key_der(values: dict[str, Any]) -> bytes:
    private_key = values.get("private_key")
    _required({"private_key": private_key}, "private_key")
    if isinstance(private_key, str):
        private_key_bytes = private_key.replace("\\n", "\n").encode("utf-8")
    elif isinstance(private_key, bytes):
        private_key_bytes = private_key
    else:
        raise SnowflakeConnectionConfigurationError(
            "Snowflake private_key must be a PEM string."
        )

    passphrase = values.get("private_key_passphrase")
    password = str(passphrase).encode("utf-8") if passphrase else None
    try:
        key = serialization.load_pem_private_key(private_key_bytes, password=password)
        return key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    except (TypeError, ValueError) as exc:
        raise SnowflakeConnectionConfigurationError(
            "Snowflake private key or passphrase is invalid."
        ) from exc


def connect_snowflake(
    *,
    connection: dict[str, Any],
    credentials: dict[str, Any],
    autocommit: bool | None = None,
    login_timeout: int = 60,
):
    """Create a Snowflake connection for password, OAuth, or key-pair auth."""
    details = _mapping(connection.get("connection_details"))
    values = flatten_snowflake_credentials(credentials)
    authentication_type = str(
        values.get("authentication_type")
        or connection.get("authentication_type")
        or "USERNAME_PASSWORD"
    ).strip().upper()

    print(
    "SNOWFLAKE_FACTORY_DEBUG",
    {
        "resolved_authentication_type": authentication_type,
        "credential_authentication_type": values.get("authentication_type"),
        "connection_authentication_type": connection.get("authentication_type"),
        "credential_keys": sorted(values.keys()),
        "has_username": bool(values.get("username")),
        "has_private_key": bool(values.get("private_key")),
        "has_private_key_passphrase": bool(
            values.get("private_key_passphrase")
        ),
        "has_password": bool(values.get("password")),
    },
    flush=True,
)
    account = _account(connection, details, values)
    _required({"account": account}, "account")

    options: dict[str, Any] = {
        "account": account,
        "login_timeout": login_timeout,
        "network_timeout": login_timeout,
        "socket_timeout": login_timeout,
    }
    if autocommit is not None:
        options["autocommit"] = autocommit

    for name in ("warehouse", "database", "role"):
        value = details.get(name) or values.get(name)
        if value:
            options[name] = str(value).strip()
    schema_name = (
        details.get("schema")
        or details.get("schema_name")
        or values.get("schema")
        or values.get("schema_name")
    )
    if schema_name:
        options["schema"] = str(schema_name).strip()

    username = str(values.get("username") or "").strip()
    if authentication_type in {"USERNAME_PASSWORD", "PASSWORD", "BASIC"}:
        password = values.get("password")
        _required({"username": username, "password": password}, "username", "password")
        options.update(
            user=username,
            password=password,
            authenticator="username_password_mfa",
            client_request_mfa_token=True,
            client_store_temporary_credential=True,
        )
    elif authentication_type in {
        "OAUTH",
        "OAUTH2",
        "OAUTH_CLIENT",
        "OAUTH_CLIENT_CREDENTIALS",
        "OAUTH2_CLIENT_CREDENTIALS",
        "ACCESS_TOKEN",
    }:
        options.update(authenticator="oauth", token=_oauth_token(values, timeout=login_timeout))
        if username:
            options["user"] = username
    elif authentication_type in {"KEY_PAIR", "KEY_PAIR_JWT", "SNOWFLAKE_JWT"}:
        _required({"username": username}, "username")
        options.update(
            user=username,
            authenticator="SNOWFLAKE_JWT",
            private_key=_private_key_der(values),
        )
    else:
        raise SnowflakeConnectionConfigurationError(
            "Unsupported Snowflake authentication type: "
            f"{authentication_type or 'MISSING'}."
        )

    return snowflake.connector.connect(**options)
