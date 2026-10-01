"""Tenant-bound native Azure SQL preview and quality profiling routes."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import Depends, HTTPException

from app.api.route_dependencies import *
from app.api.schemas import (
    AzureSQLPreviewRequest,
    AzureSQLProfileRequest,
    AzureSQLTablesRequest,
)
from app.services.azure_sql_connector import AzureSQLConnector


router = APIRouter()
logger = logging.getLogger(__name__)


def _details(connection: dict[str, Any]) -> dict[str, Any]:
    details = connection.get("connection_details") or {}

    if isinstance(details, str):
        try:
            details = json.loads(details)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=409,
                detail="Azure SQL connection_details are invalid.",
            ) from exc

    if not isinstance(details, dict):
        raise HTTPException(
            status_code=409,
            detail="Azure SQL connection_details are invalid.",
        )

    return details


def _credentials(credentials: dict[str, Any]) -> dict[str, Any]:
    additional_properties = credentials.get("additional_properties")

    if isinstance(additional_properties, dict):
        return {
            **additional_properties,
            **credentials,
        }

    return dict(credentials)


def _load_context(
    *,
    organization_id: str,
    connection_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    return connection_service.get_azure_sql_connection_context(
        connection_id=connection_id,
        organization_id=organization_id,
    )


def _connector(
    *,
    organization_id: str,
    connection_id: str,
    connection: dict[str, Any],
    credentials: dict[str, Any],
) -> AzureSQLConnector:
    details = _details(connection)
    values = _credentials(credentials)

    server = str(
        values.get("server")
        or details.get("server")
        or connection.get("api_endpoint")
        or ""
    ).strip()

    database = str(
        values.get("database")
        or details.get("database")
        or connection.get("workspace_name")
        or ""
    ).strip()

    username = str(values.get("username") or "").strip()
    password = str(values.get("password") or "")

    port_raw = values.get("port") or details.get("port") or 1433

    try:
        port = int(port_raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail="Azure SQL port must be a valid integer.",
        ) from exc

    try:
        return AzureSQLConnector(
            organization_id=organization_id,
            connection_id=connection_id,
            server=server,
            database=database,
            username=username,
            password=password,
            port=port,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc


@router.post("/connections/azure-sql/tables")
def list_azure_sql_tables(
    req: AzureSQLTablesRequest,
    current_user: AuthUser = Depends(require_active_product_access),
):
    organization_id = require_current_organization_id(current_user)

    connection, credentials = _load_context(
        organization_id=organization_id,
        connection_id=req.connection_id,
    )

    connector = _connector(
        organization_id=organization_id,
        connection_id=req.connection_id,
        connection=connection,
        credentials=credentials,
    )

    return {
        "organization_id": organization_id,
        "connection_id": req.connection_id,
        "source_type": "AZURE_SQL",
        "database": connector.database,
        "schema": req.schema_name,
        "tables": connector.list_tables(schema=req.schema_name),
    }


@router.post("/connections/azure-sql/preview")
def preview_azure_sql(
    req: AzureSQLPreviewRequest,
    current_user: AuthUser = Depends(require_active_product_access),
):
    organization_id = require_current_organization_id(current_user)

    connection, credentials = _load_context(
        organization_id=organization_id,
        connection_id=req.connection_id,
    )

    connector = _connector(
        organization_id=organization_id,
        connection_id=req.connection_id,
        connection=connection,
        credentials=credentials,
    )

    rows = connector.read_sample(
        schema=req.schema_name,
        table=req.table_name,
        limit=req.preview_limit,
    )

    headers = list(rows[0].keys()) if rows else []

    return {
        "success": True,
        "organization_id": organization_id,
        "connection_id": req.connection_id,
        "source_type": "AZURE_SQL",
        "source_table": f"{req.schema_name}.{req.table_name}",
        "headers": headers,
        "rows": rows,
    }


@router.post("/dq/profile/azure-sql")
def profile_azure_sql(
    req: AzureSQLProfileRequest,
    current_user: AuthUser = Depends(require_active_product_access),
):
    organization_id = require_current_organization_id(current_user)

    connection, credentials = _load_context(
        organization_id=organization_id,
        connection_id=req.connection_id,
    )

    connector = _connector(
        organization_id=organization_id,
        connection_id=req.connection_id,
        connection=connection,
        credentials=credentials,
    )

    columns = connector.get_columns(
        schema=req.schema_name,
        table=req.table_name,
    )

    available_columns = {
        str(column.get("column_name") or "")
        for column in columns
    }

    mapping = {
        item.source_column: item.target_field
        for item in req.column_mappings
    }

    if len(mapping) != len(req.column_mappings):
        raise HTTPException(
            status_code=400,
            detail="Source column mappings must be unique.",
        )

    if len(set(mapping.values())) != len(mapping):
        raise HTTPException(
            status_code=400,
            detail="Target field mappings must be unique.",
        )

    missing_columns = [
        source
        for source in mapping
        if source not in available_columns
    ]

    if missing_columns:
        raise HTTPException(
            status_code=400,
            detail=(
                "Mapped Azure SQL columns were not found: "
                + ", ".join(sorted(missing_columns))
            ),
        )

    if req.business_key_field not in set(mapping.values()):
        raise HTTPException(
            status_code=400,
            detail="business_key_field must be a mapped target field.",
        )

    raw_rows = connector.read_sample(
        schema=req.schema_name,
        table=req.table_name,
        limit=req.row_limit,
    )

    normalized_rows = [
        {
            target: row.get(source)
            for source, target in mapping.items()
        }
        for row in raw_rows
    ]

    source_table = f"{req.schema_name}.{req.table_name}"
    source_name = f"AZURE_SQL:{connector.database}.{source_table}"

    config = build_quality_profile_config(
        domain=req.domain.strip().upper(),
        source_name=source_name,
        business_key_field=req.business_key_field,
        mapped_fields=set(mapping.values()),
        minimum_record_score=85.0,
    )

    result = QualityProfilerService().profile_rows(
        organization_id=organization_id,
        rows=normalized_rows,
        config=config,
        source_name=source_name,
    )

    quality_profiler_repository.save_profile_result(
        result=result,
        column_mappings=[
            {
                "source_column": source,
                "target_field": target,
            }
            for source, target in mapping.items()
        ],
    )

    quality_profiler_repository.save_profile_source_context(
        organization_id=organization_id,
        profile_run_id=result.profile_run_id,
        source_type="AZURE_SQL",
        connection_id=req.connection_id,
        database_name=connector.database,
        schema_name=req.schema_name,
        table_name=req.table_name,
    )

    logger.info(
        "Azure SQL profile persisted. "
        "organization_id=%s connection_id=%s "
        "profile_run_id=%s rows=%s",
        organization_id,
        req.connection_id,
        result.profile_run_id,
        len(normalized_rows),
    )

    return {
        "success": True,
        "organization_id": organization_id,
        "connection_id": req.connection_id,
        "source_type": "AZURE_SQL",
        "source_table": (
            f"{connector.database}."
            f"{req.schema_name}.{req.table_name}"
        ),
        "profile_run_id": result.profile_run_id,
        "rows_profiled": len(normalized_rows),
        "profile": result,
    }