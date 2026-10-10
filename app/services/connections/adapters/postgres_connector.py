from __future__ import annotations

import logging
import re
import time
from typing import Any

from app.services.connections.base_connector import BaseEnterpriseConnector

try:
    import psycopg
    from psycopg import sql
except ImportError:
    psycopg = None  # type: ignore[assignment]
    sql = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)


class PostgresConnector(BaseEnterpriseConnector):
    """
    Read-oriented PostgreSQL enterprise connector.

    Owns vendor-specific PostgreSQL connectivity, metadata discovery,
    identifier-safe sampling, bounded retries, and connection cleanup.

    Profiling, DQ evaluation, remediation generation, and governed writes
    belong to higher-level services.
    """

    def __init__(
        self,
        *,
        organization_id: str,
        connection_id: str,
        host: str,
        database: str,
        username: str,
        password: str,
        port: int = 5432,
        sslmode: str = "require",
        timeout_seconds: int = 15,
    ) -> None:
        self.organization_id = self._require_organization_id(organization_id)
        self.connection_id = self._require_connection_id(connection_id)
        self.host = self._require_host(host)
        self.database = str(database or "").strip()
        self.username = str(username or "").strip()
        self.password = str(password or "")
        self.port = int(port)
        self.sslmode = self._require_sslmode(sslmode)
        self.timeout_seconds = int(timeout_seconds)

        if not self.database:
            raise ValueError("PostgreSQL database is required.")
        if not self.username or not self.password:
            raise ValueError("PostgreSQL username and password are required.")
        if self.port <= 0 or self.port > 65535:
            raise ValueError("PostgreSQL port must be between 1 and 65535.")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero.")

    def test_connection(self) -> dict[str, Any]:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 AS connection_test")
                cursor.fetchone()

            return {
                "status": "HEALTHY",
                "vendor": "POSTGRESQL",
                "host": self.host,
                "database": self.database,
            }
        finally:
            connection.close()

    def list_schemas(self) -> list[str]:
        rows = self._query_rows(
            """
            SELECT schema_name
            FROM information_schema.schemata
            WHERE schema_name <> 'information_schema'
              AND schema_name NOT LIKE 'pg_%'
            ORDER BY schema_name
            """
        )

        return [
            str(row["schema_name"])
            for row in rows
            if row.get("schema_name")
        ]

    def list_tables(
        self,
        *,
        schema: str | None = None,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT
                table_schema AS schema_name,
                table_name,
                table_type
            FROM information_schema.tables
            WHERE table_schema <> 'information_schema'
              AND table_schema NOT LIKE 'pg_%'
        """

        parameters: list[Any] = []

        if schema:
            schema_name = self._require_identifier(
                schema,
                field_name="schema",
            )
            query += " AND table_schema = %s"
            parameters.append(schema_name)

        query += " ORDER BY table_schema, table_name"

        return self._query_rows(
            query,
            parameters=parameters,
        )

    def get_columns(
        self,
        *,
        schema: str,
        table: str,
    ) -> list[dict[str, Any]]:
        schema_name = self._require_identifier(
            schema,
            field_name="schema",
        )
        table_name = self._require_identifier(
            table,
            field_name="table",
        )

        query = """
            SELECT
                column_name,
                ordinal_position,
                data_type,
                is_nullable,
                character_maximum_length,
                numeric_precision,
                numeric_scale
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = %s
            ORDER BY ordinal_position
        """

        return self._query_rows(
            query,
            parameters=[schema_name, table_name],
        )

    def read_sample(
        self,
        *,
        schema: str,
        table: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self._require_identifier(schema, field_name="schema")
        self._require_identifier(table, field_name="table")

        if limit <= 0 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000.")

        if sql is None:
            raise RuntimeError(
                "PostgreSQL support requires psycopg."
            )

        query = sql.SQL("SELECT * FROM {}.{} LIMIT {}").format(
            sql.Identifier(schema),
            sql.Identifier(table),
            sql.Literal(int(limit)),
        )

        return self._query_rows(query)

    def _query_rows(
        self,
        query: Any,
        *,
        parameters: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        connection = self._connect()

        try:
            with connection.cursor() as cursor:
                cursor.execute(query, parameters or [])

                if cursor.description is None:
                    return []

                columns = [
                    str(column.name)
                    for column in cursor.description
                ]

                return [
                    dict(zip(columns, row))
                    for row in cursor.fetchall()
                ]
        finally:
            connection.close()

    def _connect(self) -> Any:
        if psycopg is None:
            raise RuntimeError(
                "PostgreSQL support requires psycopg."
            )

        max_attempts = 3

        for attempt in range(1, max_attempts + 1):
            try:
                return psycopg.connect(
                    host=self.host,
                    port=self.port,
                    dbname=self.database,
                    user=self.username,
                    password=self.password,
                    sslmode=self.sslmode,
                    connect_timeout=self.timeout_seconds,
                )

            except psycopg.OperationalError:
                if attempt >= max_attempts:
                    raise

                delay_seconds = attempt

                logger.warning(
                    "PostgreSQL connection attempt %s/%s "
                    "failed transiently. "
                    "organization_id=%s connection_id=%s "
                    "host=%s database=%s "
                    "retrying_in_seconds=%s",
                    attempt,
                    max_attempts,
                    self.organization_id,
                    self.connection_id,
                    self.host,
                    self.database,
                    delay_seconds,
                )

                time.sleep(delay_seconds)

        raise RuntimeError(
            "PostgreSQL connection attempts exhausted unexpectedly."
        )

    @staticmethod
    def _require_host(host: str) -> str:
        normalized = str(host or "").strip()

        if not normalized:
            raise ValueError("PostgreSQL host is required.")

        if "://" in normalized:
            raise ValueError(
                "PostgreSQL host must be a hostname, not a connection URL."
            )

        return normalized

    @staticmethod
    def _require_sslmode(sslmode: str) -> str:
        normalized = str(sslmode or "").strip().lower()

        allowed = {
            "require",
            "verify-ca",
            "verify-full",
        }

        if normalized not in allowed:
            raise ValueError(
                "PostgreSQL sslmode must be require, verify-ca, or verify-full."
            )

        return normalized

    @staticmethod
    def _require_identifier(
        value: str,
        *,
        field_name: str,
    ) -> str:
        normalized = str(value or "").strip()

        if not normalized:
            raise ValueError(f"{field_name} is required.")

        if not re.fullmatch(r"[A-Za-z0-9_]+", normalized):
            raise ValueError(
                f"{field_name} contains unsupported characters."
            )

        return normalized

    @staticmethod
    def _require_organization_id(organization_id: str) -> str:
        normalized = str(organization_id or "").strip()

        if not normalized:
            raise ValueError(
                "organization_id is required for "
                "tenant-isolated PostgreSQL operations."
            )

        if not normalized.startswith("org_"):
            raise ValueError(
                "organization_id must use the org_ identifier standard."
            )

        return normalized

    @staticmethod
    def _require_connection_id(connection_id: str) -> str:
        normalized = str(connection_id or "").strip()

        if not normalized:
            raise ValueError(
                "connection_id is required for PostgreSQL operations."
            )

        is_conn_id = normalized.startswith("conn_")

        is_uuid = bool(
            re.fullmatch(
                r"[0-9a-fA-F]{8}-"
                r"[0-9a-fA-F]{4}-"
                r"[0-9a-fA-F]{4}-"
                r"[0-9a-fA-F]{4}-"
                r"[0-9a-fA-F]{12}",
                normalized,
            )
        )

        if not is_conn_id and not is_uuid:
            raise ValueError(
                "connection_id must use either the "
                "conn_ identifier standard or a valid UUID."
            )

        return normalized