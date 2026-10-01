"""Databricks-native profiling routes."""

import json

from app.api.route_dependencies import *
from app.api.schemas import DatabricksProfileRequest
from app.services.databricks_connector import DatabricksConnector

from app.api.schemas import (
    DatabricksPreviewRequest,
    DatabricksProfileRequest,
)

from app.services.databricks_connector import DatabricksConnector

router = APIRouter()

@router.post(
    "/connections/databricks/preview",
)
def preview_databricks(
    req: DatabricksPreviewRequest,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
):
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    connection, credentials = (
        connection_service
        .get_databricks_connection_context(
            connection_id=req.connection_id,
            organization_id=organization_id,
        )
    )

    connection_details = (
        connection.get("connection_details") or {}
    )

    if isinstance(connection_details, str):
        try:
            connection_details = json.loads(
                connection_details
            )
        except json.JSONDecodeError:
            connection_details = {}

    host = str(
        connection.get("api_endpoint")
        or connection_details.get("host")
        or ""
    ).strip()

    warehouse_id = str(
        connection_details.get("warehouse_id")
        or credentials.get("warehouse_id")
        or ""
    ).strip()

    catalog = str(
        connection_details.get("catalog")
        or ""
    ).strip()

    schema_name = str(
        connection_details.get("schema")
        or connection_details.get("schema_name")
        or ""
    ).strip()

    table_name = str(
        connection_details.get("table_name")
        or connection_details.get("target_table")
        or ""
    ).strip()

    print(
    "[DATABRICKS PREVIEW DEBUG]",
    {
        "connection_id": req.connection_id,
        "connection_details_keys": list(connection_details.keys()),
        "warehouse_id": connection_details.get("warehouse_id"),
        "catalog": connection_details.get("catalog"),
        "schema": connection_details.get("schema"),
        "table_name": (
            connection_details.get("table_name")
            or connection_details.get("target_table")
        ),
    },
)


    if not host:
        raise HTTPException(
            status_code=409,
            detail="Databricks workspace URL is missing.",
        )

    if not warehouse_id:
        raise HTTPException(
            status_code=409,
            detail="Databricks SQL warehouse_id is missing.",
        )

    if not catalog or not schema_name or not table_name:
        raise HTTPException(
            status_code=409,
            detail=(
                "Databricks catalog, schema, and target table "
                "must be configured."
            ),
        )

    client_id = str(
        credentials.get("client_id") or ""
    ).strip()

    client_secret = str(
        credentials.get("client_secret") or ""
    ).strip()

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=409,
            detail="Databricks OAuth credentials are incomplete.",
        )

    connector = DatabricksConnector(
        organization_id=organization_id,
        connection_id=req.connection_id,
        host=host,
        client_id=client_id,
        client_secret=client_secret,
        allow_environment_fallback=False,
    )

    rows = connector.read_table_rows(
        warehouse_id=warehouse_id,
        catalog=catalog,
        schema_name=schema_name,
        table_name=table_name,
        limit=req.preview_limit,
    )

    headers = (
        list(rows[0].keys())
        if rows
        else []
    )

    return {
        "success": True,
        "organization_id": organization_id,
        "connection_id": req.connection_id,
        "source_type": "DATABRICKS",
        "source_table": (
            f"{catalog}.{schema_name}.{table_name}"
        ),
        "headers": headers,
        "rows": rows,
    }

@router.post(
    "/dq/profile/databricks",
)
def profile_databricks(
    req: DatabricksProfileRequest,
    current_user: AuthUser = Depends(
        require_active_product_access
    ),
):
    organization_id = (
        require_current_organization_id(
            current_user
        )
    )

    # ---------------------------------------------------------
    # Resolve tenant-bound connection + server-side credentials
    # ---------------------------------------------------------

    connection, credentials = (
        connection_service
        .get_databricks_connection_context(
            connection_id=req.connection_id,
            organization_id=organization_id,
        )
    )

    connection_details = (
        connection.get("connection_details") or {}
    )

    if isinstance(connection_details, str):
        try:
            connection_details = json.loads(
                connection_details
            )
        except json.JSONDecodeError:
            connection_details = {}

    # ---------------------------------------------------------
    # Resolve governed Databricks target from connection
    # Never accept arbitrary table identifiers from browser.
    # ---------------------------------------------------------

    host = str(
        connection.get("api_endpoint")
        or connection_details.get("host")
        or ""
    ).strip()

    warehouse_id = str(
        connection_details.get("warehouse_id")
        or credentials.get("warehouse_id")
        or ""
    ).strip()

    catalog = str(
        connection_details.get("catalog")
        or ""
    ).strip()

    schema_name = str(
        connection_details.get("schema")
        or connection_details.get("schema_name")
        or ""
    ).strip()

    table_name = str(
        connection_details.get("table_name")
        or connection_details.get("target_table")
        or ""
    ).strip()

    if not host:
        raise HTTPException(
            status_code=409,
            detail="Databricks workspace URL is missing.",
        )

    if not warehouse_id:
        raise HTTPException(
            status_code=409,
            detail="Databricks SQL warehouse_id is missing.",
        )

    if not catalog or not schema_name or not table_name:
        raise HTTPException(
            status_code=409,
            detail=(
                "Databricks catalog, schema, and target table "
                "must be configured."
            ),
        )

    # ---------------------------------------------------------
    # Build tenant-bound Databricks connector
    # ---------------------------------------------------------

    authentication_type = str(
        credentials.get("authentication_type") or ""
    ).strip().upper()

    if authentication_type not in {
        "OAUTH_CLIENT",
        "OAUTH_CLIENT_CREDENTIALS",
    }:
        raise HTTPException(
            status_code=409,
            detail=(
                "Databricks native profiling currently requires "
                "OAuth client credentials."
            ),
        )

    client_id = str(
        credentials.get("client_id") or ""
    ).strip()

    client_secret = str(
        credentials.get("client_secret") or ""
    ).strip()

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=409,
            detail="Databricks OAuth credentials are incomplete.",
        )

    connector = DatabricksConnector(
        organization_id=organization_id,
        connection_id=req.connection_id,
        host=host,
        client_id=client_id,
        client_secret=client_secret,
        allow_environment_fallback=False,
    )

    # ---------------------------------------------------------
    # Read directly from Databricks
    # ---------------------------------------------------------

    raw_rows = connector.read_table_rows(
        warehouse_id=warehouse_id,
        catalog=catalog,
        schema_name=schema_name,
        table_name=table_name,
        limit=req.row_limit,
    )

    # ---------------------------------------------------------
    # Reuse common profiling normalization
    # ---------------------------------------------------------

    mapping = {
        item.source_column: item.target_field
        for item in req.column_mappings
    }

    normalized_rows = []

    for raw_row in raw_rows:
        normalized_row = {}

        for source_column, target_field in mapping.items():
            normalized_row[target_field] = (
                raw_row.get(source_column)
            )

        normalized_rows.append(
            normalized_row
        )

    normalized_domain = (
        req.domain.strip().upper()
    )

    source_table = (
        f"{catalog}.{schema_name}.{table_name}"
    )

    source_name = (
        f"DATABRICKS:{source_table}"
    )

    config = build_quality_profile_config(
        domain=normalized_domain,
        source_name=source_name,
        business_key_field=req.business_key_field,
        mapped_fields={
            item.target_field
            for item in req.column_mappings
        },
        minimum_record_score=85.0,
    )

    profiler = QualityProfilerService()

    result = profiler.profile_rows(
        organization_id=organization_id,
        rows=normalized_rows,
        config=config,
        source_name=source_name,
    )

    # ---------------------------------------------------------
    # Persist profile result.
    # Do NOT materialize Databricks rows into BigQuery.
    # ---------------------------------------------------------

    quality_profiler_repository.save_profile_result(
        result=result,
        column_mappings=[
            {
                "source_column": item.source_column,
                "target_field": item.target_field,
            }
            for item in req.column_mappings
        ],
    )

    return {
        "success": True,
        "organization_id": organization_id,
        "connection_id": req.connection_id,
        "source_type": "DATABRICKS",
        "source_table": source_table,
        "profile_run_id": result.profile_run_id,
        "rows_profiled": len(normalized_rows),
        "profile": result,
    }