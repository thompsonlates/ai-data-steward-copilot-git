from __future__ import annotations

import logging
import re
from typing import Any

try:
    import pyodbc
except ImportError:
    pyodbc = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)


class AzureSQLConnector:
    def __init__(
        self,
        *,
        organization_id: str,
        connection_id: str,
        server: str,
        database: str,
        username: str,
        password: str,
        port: int = 1433,
        driver: str = "ODBC Driver 18 for SQL Server",
        timeout_seconds: int = 15,
    ) -> None:
        self.organization_id = self._require_organization_id(organization_id)
        self.connection_id = self._require_connection_id(connection_id)
        self.server = self._normalize_server(server)
        self.database = str(database or "").strip()
        self.username = str(username or "").strip()
        self.password = str(password or "")
        self.port = int(port)
        self.driver = str(driver or "").strip()
        self.timeout_seconds = timeout_seconds

        if not self.database:
            raise ValueError("Azure SQL database is required.")
        if not self.username or not self.password:
            raise ValueError("Azure SQL username and password are required.")
        if self.port <= 0:
            raise ValueError("Azure SQL port must be greater than zero.")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero.")

    def test_connection(self) -> dict[str, Any]:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT 1 AS connection_test")
            cursor.fetchone()
            return {
                "status": "HEALTHY",
                "vendor": "AZURE_SQL",
                "server": self.server,
                "database": self.database,
            }
        finally:
            connection.close()

    def list_schemas(self) -> list[str]:
        rows = self._query_rows(
            "SELECT schema_name FROM information_schema.schemata ORDER BY schema_name"
        )
        return [str(row["schema_name"]) for row in rows if row.get("schema_name")]

    def list_tables(
        self,
        *,
        schema: str | None = None,
    ) -> list[dict[str, Any]]:
        query = '''
        SELECT table_schema, table_name, table_type
        FROM information_schema.tables
        WHERE 1 = 1
        '''
        params: list[Any] = []
        if schema:
            query += " AND table_schema = ?"
            params.append(str(schema).strip())
        query += " ORDER BY table_schema, table_name"
        return self._query_rows(query, parameters=params)

    def get_columns(
        self,
        *,
        schema: str,
        table: str,
    ) -> list[dict[str, Any]]:
        schema_name = self._require_identifier(schema, field_name="schema")
        table_name = self._require_identifier(table, field_name="table")
        query = '''
        SELECT
            column_name,
            ordinal_position,
            data_type,
            is_nullable,
            character_maximum_length,
            numeric_precision,
            numeric_scale
        FROM information_schema.columns
        WHERE table_schema = ?
          AND table_name = ?
        ORDER BY ordinal_position
        '''
        return self._query_rows(query, parameters=[schema_name, table_name])

    def read_sample(
        self,
        *,
        schema: str,
        table: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        schema_name = self._require_identifier(schema, field_name="schema")
        table_name = self._require_identifier(table, field_name="table")
        if limit <= 0 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000.")
        query = f"SELECT TOP ({int(limit)}) * FROM [{schema_name}].[{table_name}]"
        return self._query_rows(query)

    def execute_update(
        self,
        *,
        sql: str,
    ) -> dict[str, Any]:
        normalized = str(sql or "").strip()
        if not re.match(r"^\s*UPDATE\b", normalized, flags=re.IGNORECASE):
            raise ValueError("Azure SQL connector execute_update accepts UPDATE only.")

        connection = self._connect()
        try:
            connection.autocommit = False
            cursor = connection.cursor()
            cursor.execute(normalized)
            rows_affected = cursor.rowcount
            connection.commit()
            return {
                "vendor_status": "SUCCEEDED",
                "rows_affected": (
                    int(rows_affected)
                    if rows_affected is not None and rows_affected >= 0
                    else None
                ),
                "statement_id": None,
            }
        except Exception:
            try:
                connection.rollback()
            finally:
                pass
            raise
        finally:
            connection.close()

    def _query_rows(
        self,
        query: str,
        *,
        parameters: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            cursor.execute(query, parameters or [])
            columns = [str(column[0]) for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            connection.close()

    def _connect(self) -> Any:
        if pyodbc is None:
            raise RuntimeError(
                "Azure SQL support requires pyodbc and Microsoft ODBC Driver 18 for SQL Server."
            )

        connection_string = (
            f"DRIVER={{{self.driver}}};"
            f"SERVER=tcp:{self.server},{self.port};"
            f"DATABASE={self.database};"
            f"UID={self.username};"
            f"PWD={self.password};"
            "Encrypt=yes;"
            "TrustServerCertificate=no;"
            f"Connection Timeout={self.timeout_seconds};"
        )
        return pyodbc.connect(connection_string)

    @staticmethod
    def _normalize_server(server: str) -> str:
        normalized = str(server or "").strip()
        if normalized.startswith("tcp:"):
            normalized = normalized[4:]
        if "," in normalized:
            normalized = normalized.split(",", 1)[0].strip()
        if not normalized:
            raise ValueError("Azure SQL server is required.")
        return normalized

    @staticmethod
    def _require_identifier(value: str, *, field_name: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{field_name} is required.")
        if not re.fullmatch(r"[A-Za-z0-9_]+", normalized):
            raise ValueError(f"{field_name} contains unsupported characters.")
        return normalized

    @staticmethod
    def _require_organization_id(organization_id: str) -> str:
        normalized = str(organization_id or "").strip()
        if not normalized:
            raise ValueError("organization_id is required for tenant-isolated Azure SQL operations.")
        if not normalized.startswith("org_"):
            raise ValueError("organization_id must use the org_ identifier standard.")
        return normalized

    @staticmethod
    def _require_connection_id(connection_id: str) -> str:
        normalized = str(connection_id or "").strip()

        if not normalized:
            raise ValueError(
                "connection_id is required for Azure SQL operations."
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