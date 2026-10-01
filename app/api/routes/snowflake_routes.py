"""Tenant-bound native Snowflake preview and quality profiling routes."""

import json

import logging

import re

from typing import Any

import snowflake.connector

from fastapi import HTTPException

from app.api.route_dependencies import *

from app.api.schemas import SnowflakePreviewRequest, SnowflakeProfileRequest, SnowflakeTablesRequest
from app.services.snowflake_connection_factory import (
    SnowflakeConnectionConfigurationError,
    connect_snowflake,
)

router = APIRouter()

logger = logging.getLogger(__name__)

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")



def _load_context(*, organization_id: str, connection_id: str):

    """Log the metadata check before touching Snowflake or a secret value."""

    logger.info(

        "Snowflake connection lookup started. organization_id=%s connection_id=%s",

        organization_id, connection_id,

    )

    try:

        connection, credentials = connection_service.get_snowflake_connection_context(

            connection_id=connection_id, organization_id=organization_id,

        )

    except HTTPException as exc:

        logger.warning(

            "Snowflake connection lookup rejected. organization_id=%s connection_id=%s status_code=%s",

            organization_id, connection_id, exc.status_code,

        )

        raise

    logger.info(

        "Snowflake connection lookup succeeded. organization_id=%s connection_id=%s vendor=%s connection_type=%s",

        organization_id, connection_id,

        connection.get("vendor"), connection.get("connection_type"),

    )

    return connection, credentials

def _identifier(value: Any, label: str) -> str:

    name = str(value or "").strip()

    if not _IDENTIFIER.fullmatch(name):

        raise HTTPException(status_code=409, detail=f"Snowflake {label} is missing or invalid.")

    return name.upper()

def _details(connection: dict[str, Any]) -> dict[str, Any]:

    details = connection.get("connection_details") or {}

    if isinstance(details, str):

        try:

            details = json.loads(details)

        except (TypeError, ValueError) as exc:

            raise HTTPException(status_code=409, detail="Snowflake connection_details are invalid.") from exc

    if not isinstance(details, dict):

        raise HTTPException(status_code=409, detail="Snowflake connection_details are invalid.")

    return details

def _credentials(

    credentials: dict[str, Any],

) -> dict[str, Any]:

    values = dict(credentials)

    additional = values.get(

        "additional_properties"

    )

    if isinstance(additional, dict):

        for key, value in additional.items():

            if not values.get(key):

                values[key] = value

    return values





def _connector_error_message(

    exc: snowflake.connector.errors.Error,

) -> str:

    """Return Snowflake diagnostics without logging credential values."""

    return str(getattr(exc, "msg", None) or exc)





def _connector_error_detail(

    *,

    summary: str,

    exc: snowflake.connector.errors.Error,

) -> dict[str, Any]:

    return {

        "message": summary,

        "error_type": type(exc).__name__,

        "errno": getattr(exc, "errno", None),

        "sqlstate": getattr(exc, "sqlstate", None),

        "connector_message": _connector_error_message(exc),

    }

def _schema_target(connection: dict[str, Any]) -> tuple[str, str]:

    details = _details(connection)

    return (

        _identifier(details.get("database"), "database"),

        _identifier(details.get("schema") or details.get("schema_name"), "schema"),

    )

def _list_tables(connection: dict[str, Any], credentials: dict[str, Any]) -> list[str]:

    database, schema = _schema_target(connection)

    try:

        client = _connect(connection, credentials)

    except snowflake.connector.errors.Error as exc:

        logger.error(

            "Snowflake table listing connection failed. "

            "connection_id=%s error_type=%s errno=%s sqlstate=%s message=%s",

            connection.get("connection_id"), type(exc).__name__,

            getattr(exc, "errno", None), getattr(exc, "sqlstate", None),

            _connector_error_message(exc),

        )

        raise HTTPException(

            status_code=502,

            detail=_connector_error_detail(

                summary="Snowflake connection failed.",

                exc=exc,

            ),

        ) from exc

    try:

        cursor = client.cursor()

        try:

            cursor.execute(f"SHOW TABLES IN SCHEMA {database}.{schema}")

            names = [str(item[0]).lower() for item in cursor.description or ()]

            name_index = names.index("name")

            return sorted({str(row[name_index]) for row in cursor.fetchall()})

        except snowflake.connector.errors.Error as exc:

            logger.error(

                "Snowflake table listing failed. "

                "connection_id=%s error_type=%s errno=%s sqlstate=%s message=%s",

                connection.get("connection_id"), type(exc).__name__,

                getattr(exc, "errno", None), getattr(exc, "sqlstate", None),

                _connector_error_message(exc),

            )

            raise HTTPException(

                status_code=502,

                detail=_connector_error_detail(

                    summary="Snowflake table listing failed.",

                    exc=exc,

                ),

            ) from exc

        finally:

            cursor.close()

    finally:

        client.close()

def _target(connection: dict[str, Any], credentials: dict[str, Any], requested_table: str | None) -> tuple[str, str, str]:

    database, schema = _schema_target(connection)

    details = _details(connection)

    configured_table = details.get("table_name") or details.get("target_table")

    if configured_table:

        table = _identifier(configured_table, "table")

        if requested_table and _identifier(requested_table, "table") != table:

            raise HTTPException(status_code=409, detail="Requested Snowflake table differs from the connection's configured target.")

    elif requested_table:

        table = _identifier(requested_table, "table")

    else:

        tables = _list_tables(connection, credentials)

        if len(tables) != 1:

            raise HTTPException(

                status_code=409,

                detail="Select a table_name from /v1/connections/snowflake/tables for this connection.",

            )

        table = _identifier(tables[0], "table")

    return database, schema, table

def _connect(connection: dict[str, Any], credentials: dict[str, Any]):

    try:

        return connect_snowflake(

            connection=connection,

            credentials=credentials,

            login_timeout=60,

        )

    except SnowflakeConnectionConfigurationError as exc:

        raise HTTPException(status_code=409, detail=str(exc)) from exc

def _read_rows(connection: dict[str, Any], credentials: dict[str, Any], columns: list[str], limit: int, table_name: str) -> tuple[list[str], list[dict[str, Any]]]:

    database, schema = _schema_target(connection)

    table = _identifier(table_name, "table")

    source = f"{database}.{schema}.{table}"

    projection = ", ".join(_identifier(column, "column") for column in columns) if columns else "*"

    connection_id = str(connection.get("connection_id") or "")

    logger.info("Snowflake query connection started. connection_id=%s target=%s", connection_id, source)

    try:

        client = _connect(connection, credentials)

    except snowflake.connector.errors.Error as exc:

        logger.error(

            "Snowflake authentication/connection failed. "

            "connection_id=%s error_type=%s errno=%s sqlstate=%s message=%s",

            connection_id, type(exc).__name__, getattr(exc, "errno", None), getattr(exc, "sqlstate", None),

            _connector_error_message(exc),

        )

        raise HTTPException(

            status_code=502,

            detail=_connector_error_detail(

                summary="Snowflake connection failed.",

                exc=exc,

            ),

        ) from exc

    try:

        cursor = client.cursor()

        try:

            logger.info("Snowflake SELECT started. connection_id=%s target=%s limit=%s", connection_id, source, limit)

            try:

                cursor.execute(f"SELECT {projection} FROM {source} LIMIT {int(limit)}")

            except snowflake.connector.errors.Error as exc:

                logger.error(

                    "Snowflake SELECT failed. "

                    "connection_id=%s target=%s error_type=%s errno=%s sqlstate=%s message=%s",

                    connection_id, source, type(exc).__name__, getattr(exc, "errno", None), getattr(exc, "sqlstate", None),

                    _connector_error_message(exc),

                )

                raise HTTPException(

                    status_code=502,

                    detail=_connector_error_detail(

                        summary="Snowflake query failed.",

                        exc=exc,

                    ),

                ) from exc

            headers = [str(item[0]) for item in cursor.description or ()]

            try:

                rows = [dict(zip(headers, row)) for row in cursor.fetchall()]

            except snowflake.connector.errors.Error as exc:

                logger.error(

                    "Snowflake fetch failed. "

                    "connection_id=%s target=%s error_type=%s errno=%s sqlstate=%s message=%s",

                    connection_id, source, type(exc).__name__, getattr(exc, "errno", None), getattr(exc, "sqlstate", None),

                    _connector_error_message(exc),

                )

                raise HTTPException(

                    status_code=502,

                    detail=_connector_error_detail(

                        summary="Snowflake fetch failed.",

                        exc=exc,

                    ),

                ) from exc

            logger.info("Snowflake SELECT succeeded. connection_id=%s target=%s rows=%s", connection_id, source, len(rows))

            return headers, rows

        finally:

            cursor.close()

    finally:

        client.close()

@router.post("/connections/snowflake/tables")

def list_snowflake_tables(req: SnowflakeTablesRequest, current_user: AuthUser = Depends(require_active_product_access)):

    organization_id = require_current_organization_id(current_user)

    connection, credentials = _load_context(connection_id=req.connection_id, organization_id=organization_id)

    database, schema = _schema_target(connection)

    return {

        "organization_id": organization_id, "connection_id": req.connection_id,

        "source_type": "SNOWFLAKE", "database": database, "schema": schema,

        "tables": _list_tables(connection, credentials),

    }

@router.post("/connections/snowflake/preview")

def preview_snowflake(req: SnowflakePreviewRequest, current_user: AuthUser = Depends(require_active_product_access)):

    organization_id = require_current_organization_id(current_user)

    connection, credentials = _load_context(

        connection_id=req.connection_id, organization_id=organization_id,

    )

    database, schema, table = _target(connection, credentials, req.table_name)

    headers, rows = _read_rows(connection, credentials, [], req.preview_limit, table)

    return {

        "success": True, "organization_id": organization_id,

        "connection_id": req.connection_id, "source_type": "SNOWFLAKE",

        "source_table": f"{database}.{schema}.{table}",

        "headers": headers, "rows": rows,

    }

@router.post("/dq/profile/snowflake")

def profile_snowflake(req: SnowflakeProfileRequest, current_user: AuthUser = Depends(require_active_product_access)):

    organization_id = require_current_organization_id(current_user)

    connection, credentials = _load_context(

        connection_id=req.connection_id, organization_id=organization_id,

    )

    database, schema, table = _target(connection, credentials, req.table_name)

    mapping = {_identifier(item.source_column, "column"): item.target_field for item in req.column_mappings}

    if len(mapping) != len(req.column_mappings):

        raise HTTPException(status_code=400, detail="Source column mappings must be unique.")

    if len(set(mapping.values())) != len(mapping):

        raise HTTPException(status_code=400, detail="Target field mappings must be unique.")

    if req.business_key_field not in set(mapping.values()):

        raise HTTPException(status_code=400, detail="business_key_field must be a mapped target field.")

    _, raw_rows = _read_rows(connection, credentials, list(mapping), req.row_limit, table)

    normalized_rows = [

        {target: row.get(source) for source, target in mapping.items()}

        for row in raw_rows

    ]

    source_table = f"{database}.{schema}.{table}"

    source_name = f"SNOWFLAKE:{source_table}"

    config = build_quality_profile_config(

        domain=req.domain.strip().upper(), source_name=source_name,

        business_key_field=req.business_key_field,

        mapped_fields=set(mapping.values()), minimum_record_score=85.0,

    )

    result = QualityProfilerService().profile_rows(

        organization_id=organization_id, rows=normalized_rows,

        config=config, source_name=source_name,

    )

    quality_profiler_repository.save_profile_result(

        result=result,

        column_mappings=[{"source_column": source, "target_field": target} for source, target in mapping.items()],

    )

    quality_profiler_repository.save_profile_source_context(

        organization_id=organization_id, profile_run_id=result.profile_run_id,

        source_type="SNOWFLAKE", connection_id=req.connection_id,

        database_name=database, schema_name=schema, table_name=table,

    )

    logger.info(

        "Snowflake profile persisted. organization_id=%s connection_id=%s profile_run_id=%s rows=%s",

        organization_id, req.connection_id, result.profile_run_id, len(normalized_rows),

    )

    return {

        "success": True, "organization_id": organization_id,

        "connection_id": req.connection_id, "source_type": "SNOWFLAKE",

        "source_table": source_table, "profile_run_id": result.profile_run_id,

        "rows_profiled": len(normalized_rows), "profile": result,

    }
