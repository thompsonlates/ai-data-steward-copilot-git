"""Tenant-bound native BigQuery preview and profiling routes."""

import json
import re
from typing import Any

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.cloud import bigquery
from google.oauth2 import credentials as user_credentials
from google.oauth2 import service_account

from app.api.route_dependencies import *
from app.api.schemas import BigQueryPreviewRequest, BigQueryProfileRequest


router = APIRouter()
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]+$")
_CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def _details(connection: dict[str, Any]) -> dict[str, Any]:
    value = connection.get("connection_details") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=409, detail="BigQuery connection_details are invalid.") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=409, detail="BigQuery connection_details are invalid.")
    return value


def _flatten(credentials: dict[str, Any]) -> dict[str, Any]:
    result = dict(credentials)
    nested = result.get("additional_properties")
    if isinstance(nested, dict):
        result.update(nested)
    return result


def _target(
    connection: dict[str, Any],
    credentials: dict[str, Any],
) -> tuple[str, str, str]:
    details = _details(connection)
    values = _flatten(credentials)
    project_id = str(
        details.get("project_id") or details.get("project")
        or values.get("project_id") or connection.get("workspace_name") or ""
    ).strip()
    dataset_id = str(details.get("dataset_id") or details.get("dataset") or "").strip()
    table_id = str(details.get("table_id") or details.get("table_name") or details.get("target_table") or "").strip()
    for label, value in (
        ("project_id", project_id), ("dataset_id", dataset_id), ("table_id", table_id)
    ):
        if not value or not _IDENTIFIER.fullmatch(value):
            raise HTTPException(status_code=409, detail=f"BigQuery {label} is missing or invalid.")
    return project_id, dataset_id, table_id


def _client(
    connection: dict[str, Any],
    credentials: dict[str, Any],
) -> bigquery.Client:
    values = _flatten(credentials)
    project_id, _, _ = _target(connection, credentials)
    raw_sa = values.get("service_account_json") or values.get("service_account") or values.get("credentials_json")
    info = None
    if isinstance(raw_sa, dict):
        info = raw_sa
    elif isinstance(raw_sa, str) and raw_sa.strip():
        try:
            info = json.loads(raw_sa)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=409, detail="BigQuery service account JSON is invalid.") from exc
    elif values.get("type") == "service_account" and values.get("client_email") and values.get("private_key"):
        info = values

    if isinstance(info, dict):
        auth = service_account.Credentials.from_service_account_info(
            info, scopes=[_CLOUD_SCOPE]
        )
        return bigquery.Client(project=project_id, credentials=auth)

    access_token = str(values.get("access_token") or "").strip()
    refresh_token = str(values.get("refresh_token") or "").strip() or None
    if not access_token and not refresh_token:
        raise HTTPException(
            status_code=409,
            detail="BigQuery requires explicit service-account or OAuth credentials; ambient credentials are not allowed.",
        )
    auth = user_credentials.Credentials(
        token=access_token or None,
        refresh_token=refresh_token,
        token_uri=str(values.get("token_url") or "https://oauth2.googleapis.com/token"),
        client_id=values.get("client_id"),
        client_secret=values.get("client_secret"),
        scopes=values.get("scopes") or [_CLOUD_SCOPE],
    )
    if not auth.valid and auth.refresh_token:
        auth.refresh(GoogleAuthRequest())
    return bigquery.Client(project=project_id, credentials=auth)


def _quote_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise HTTPException(status_code=409, detail="BigQuery column identifier is invalid.")
    return f"`{value}`"


@router.post("/connections/bigquery/preview")
def preview_bigquery(
    req: BigQueryPreviewRequest,
    current_user: AuthUser = Depends(require_active_product_access),
):
    organization_id = require_current_organization_id(current_user)
    connection, credentials = connection_service.get_bigquery_connection_context(
        connection_id=req.connection_id, organization_id=organization_id
    )
    project_id, dataset_id, table_id = _target(connection, credentials)
    client = _client(connection, credentials)
    source_table = f"{project_id}.{dataset_id}.{table_id}"
    rows = [dict(row) for row in client.query(
        f"SELECT * FROM `{source_table}` LIMIT {int(req.preview_limit)}"
    ).result()]
    return {
        "success": True, "organization_id": organization_id,
        "connection_id": req.connection_id, "source_type": "BIGQUERY",
        "source_table": source_table,
        "headers": list(rows[0].keys()) if rows else [], "rows": rows,
    }


@router.post("/dq/profile/bigquery")
def profile_bigquery(
    req: BigQueryProfileRequest,
    current_user: AuthUser = Depends(require_active_product_access),
):
    organization_id = require_current_organization_id(current_user)
    connection, credentials = connection_service.get_bigquery_connection_context(
        connection_id=req.connection_id, organization_id=organization_id
    )
    project_id, dataset_id, table_id = _target(connection, credentials)
    client = _client(connection, credentials)
    source_table = f"{project_id}.{dataset_id}.{table_id}"
    mapping = {item.source_column: item.target_field for item in req.column_mappings}
    if not mapping:
        raise HTTPException(status_code=400, detail="At least one column mapping is required.")
    if req.business_key_field not in set(mapping.values()):
        raise HTTPException(status_code=400, detail="business_key_field must be a mapped target field.")
    columns = ", ".join(_quote_identifier(name) for name in mapping)
    raw_rows = [dict(row) for row in client.query(
        f"SELECT {columns} FROM `{source_table}` LIMIT {int(req.row_limit)}"
    ).result()]
    normalized_rows = [
        {target: row.get(source) for source, target in mapping.items()}
        for row in raw_rows
    ]
    domain = req.domain.strip().upper()
    source_name = f"BIGQUERY:{source_table}"
    config = build_quality_profile_config(
        domain=domain, source_name=source_name,
        business_key_field=req.business_key_field,
        mapped_fields=set(mapping.values()), minimum_record_score=85.0,
    )
    result = QualityProfilerService().profile_rows(
        organization_id=organization_id, rows=normalized_rows,
        config=config, source_name=source_name,
    )
    quality_profiler_repository.save_profile_result(
        result=result,
        column_mappings=[{"source_column": s, "target_field": t} for s, t in mapping.items()],
    )
    quality_profiler_repository.save_profile_source_context(
        organization_id=organization_id,
        profile_run_id=result.profile_run_id,
        source_type="BIGQUERY", connection_id=req.connection_id,
        project_id=project_id, dataset_id=dataset_id, table_id=table_id,
    )
    return {
        "success": True, "organization_id": organization_id,
        "connection_id": req.connection_id, "source_type": "BIGQUERY",
        "source_table": source_table, "profile_run_id": result.profile_run_id,
        "rows_profiled": len(normalized_rows), "profile": result,
    }
