from __future__ import annotations

import json
from typing import Any

from app.services.connections.base_connector import BaseEnterpriseConnector
from app.services.connections.adapters.postgres_connector import PostgresConnector


class EnterpriseConnectorFactory:
    """
    Builds governed enterprise connectors from resolved Enterprise Connection
    records and resolved credentials.

    This factory does not retrieve connections, access Secret Manager,
    perform profiling, or execute DQ/remediation workflows.
    """

    @classmethod
    def create(
        cls,
        *,
        connection: dict[str, Any],
        credentials: dict[str, Any],
    ) -> BaseEnterpriseConnector:
        vendor = str(connection.get("vendor") or "").strip().upper()

        if vendor in {"POSTGRESQL", "POSTGRES"}:
            return cls._create_postgres(
                connection=connection,
                credentials=credentials,
            )

        raise ValueError(
            f"Enterprise connector factory does not support vendor: "
            f"{vendor or 'UNKNOWN'}"
        )

    @staticmethod
    def _create_postgres(
        *,
        connection: dict[str, Any],
        credentials: dict[str, Any],
    ) -> PostgresConnector:
        connection_details = connection.get("connection_details") or {}

        if isinstance(connection_details, str):
            try:
                connection_details = json.loads(connection_details)
            except json.JSONDecodeError:
                connection_details = {}

        if not isinstance(connection_details, dict):
            connection_details = {}

        additional_properties = credentials.get("additional_properties")

        if isinstance(additional_properties, dict):
            effective_credentials = {
                **additional_properties,
                **credentials,
            }
        else:
            effective_credentials = dict(credentials)

        host = (
            effective_credentials.get("host")
            or effective_credentials.get("server")
            or connection_details.get("host")
            or connection_details.get("server")
            or connection.get("api_endpoint")
        )

        database = (
            effective_credentials.get("database")
            or connection_details.get("database")
            or connection.get("workspace_name")
        )

        username = effective_credentials.get("username")
        password = effective_credentials.get("password")

        port = (
            effective_credentials.get("port")
            or connection_details.get("port")
            or 5432
        )

        sslmode = (
            effective_credentials.get("sslmode")
            or connection_details.get("sslmode")
            or "require"
        )

        if not host:
            raise ValueError("PostgreSQL host is missing.")

        if not database:
            raise ValueError("PostgreSQL database is missing.")

        if not username or not password:
            raise ValueError(
                "PostgreSQL username and password are missing."
            )

        return PostgresConnector(
            organization_id=str(
                connection.get("organization_id") or ""
            ),
            connection_id=str(
                connection.get("connection_id") or ""
            ),
            host=str(host),
            database=str(database),
            username=str(username),
            password=str(password),
            port=int(port),
            sslmode=str(sslmode),
        )