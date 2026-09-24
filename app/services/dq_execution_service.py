from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from google.auth.transport import requests as google_auth_request

import requests
import snowflake.connector
from fastapi import HTTPException, status
from google.cloud import bigquery
from google.oauth2 import service_account
from google.oauth2 import credentials as user_credentials

from app.repositories.connection_repository import ConnectionRepository
from app.repositories.quality_profiler_repository import QualityProfilerRepository
from app.repositories.dq_execution_repository import DqExecutionRepository
from app.services.databricks_connector import DatabricksConnector
from app.services.entitlement_service import EntitlementService
from app.services.secret_manager_service import SecretManagerService
from app.workflow.connectors.google_sheets_connector import GoogleSheetsConnector
from app.workflow.connectors.onedrive_connector import OneDriveConnector


logger = logging.getLogger(__name__)


class DqExecutionService:
    """
    Governed non-production execution service for approved DQ remediation.

    Security / governance principles:
      * organization_id is mandatory and enforced on every lookup.
      * remediation is loaded server-side from the approved DQ repository.
      * only active DEV / STG enterprise connections may execute.
      * PROD is always blocked in this service.
      * the connection must expose an explicit execution capability.
      * only steward-approved, non-blocked SQL artifacts may execute.
      * only a single UPDATE statement is executable in the initial version.
      * client secrets are resolved from Secret Manager and never returned.
      * the exact approved SQL is hashed before execution for audit/versioning.
      * "validate_only" performs all checks but does not touch target data.

    SQL execution and Google Sheets REGEX remediation are intentionally
    separate execution contracts.

    SQL vendors continue to execute only persisted, steward-approved UPDATE
    statements. Google Sheets remediation uses an exact, allow-listed REGEX
    transformation contract and never attempts to parse or execute SQL against
    a spreadsheet.

    The Google Sheets helpers below validate and deterministically transform
    approved values. The actual Sheets write-back remains a separate connector
    operation so spreadsheet/range metadata and Google OAuth write scope can be
    governed independently.
    """

    ALLOWED_ENVIRONMENTS = {
        "DEV",
        "DEVELOPMENT",
        "STG",
        "STAGE",
        "STAGING",
    }

    BLOCKED_ENVIRONMENTS = {
        "PROD",
        "PRODUCTION",
        "PRD",
    }

    ALLOWED_VENDORS = {
        "BIGQUERY",
        "DATABRICKS",
        "SNOWFLAKE",
    }

    EXECUTION_CAPABILITIES = {
        "EXECUTE_DQ_REMEDIATION",
        "EXECUTE_SQL",
        "WRITE_DATA",
    }

    GOOGLE_SHEETS_EXECUTION_CAPABILITIES = {
        "EXECUTE_DQ_REMEDIATION",
        "WRITE_DATA",
    }

    GOOGLE_SHEETS_WRITE_SCOPE = (
        "https://www.googleapis.com/auth/spreadsheets"
    )

    SAFE_ARTIFACT_TYPES = {
        "SQL",
    }

    # Google Sheets is deliberately NOT added to ALLOWED_VENDORS above.
    # It is not a SQL execution target. Spreadsheet remediation is handled
    # through an independent, allow-listed REGEX transformation contract.
    GOOGLE_SHEETS_REGEX_ARTIFACT_TYPES = {
        "REGEX",
    }

    GOOGLE_SHEETS_EXECUTABLE_REGEX_RULES = {
        "PRODUCT_VARIANT_STANDARDIZATION",
        "FULL_NAME_STANDARDIZATION",
    }

    ONEDRIVE_EXECUTION_CAPABILITIES = {
        "EXECUTE_DQ_REMEDIATION",
        "WRITE_DATA",
    }

    ONEDRIVE_EXECUTABLE_REGEX_RULES = {
        "PRODUCT_VARIANT_STANDARDIZATION",
        "FULL_NAME_STANDARDIZATION",
    }

    MICROSOFT_FILES_WRITE_SCOPE = "Files.ReadWrite"

    # Deterministic Product Variant normalization:
    #
    #   " 3.4   oz " -> "3.4 OZ"
    #   "12 ea"      -> "12 EA"
    #
    # This pattern intentionally preserves the numeric quantity and UOM token.
    # UOM vocabulary approval remains governed separately by
    # PRODUCT_VARIANT_UOM_ALLOWED_VALUE.
    GOOGLE_SHEETS_PRODUCT_VARIANT_REGEX = (
        r"^\s*([0-9]+(?:\.[0-9]+)?)\s+([A-Za-z]+)\s*$"
    )
    FULL_NAME_STANDARDIZATION_REGEX = (
        r"^\s*(\S+(?:\s+\S+)*)\s*$"
    )

    ALLOWED_SQL_VERBS = {
        "UPDATE",
    }

    BLOCKED_SQL_KEYWORDS = {
        "DELETE",
        "DROP",
        "TRUNCATE",
        "ALTER",
        "CREATE",
        "INSERT",
        "MERGE",
        "GRANT",
        "REVOKE",
        "CALL",
        "COPY",
        "PUT",
        "GET",
        "REMOVE",
        "UNDROP",
    }

    TERMINAL_DATABRICKS_STATES = {
        "SUCCEEDED",
        "FAILED",
        "CANCELED",
        "CLOSED",
    }

    def __init__(
        self,
        *,
        connection_repository: ConnectionRepository,
        secret_manager: SecretManagerService,
        quality_repository: QualityProfilerRepository,
        entitlement_service: EntitlementService,
        dq_execution_repository: DqExecutionRepository,
        require_healthy_connection: bool = True,
        require_execution_capability: bool = True,
        databricks_poll_interval_seconds: float = 1.0,
        databricks_poll_timeout_seconds: int = 60,
    ) -> None:
        self.connection_repository = connection_repository
        self.secret_manager = secret_manager
        self.quality_repository = quality_repository
        self.entitlement_service = entitlement_service
        self.dq_execution_repository = dq_execution_repository
        self.require_healthy_connection = require_healthy_connection
        self.require_execution_capability = require_execution_capability

        if databricks_poll_interval_seconds <= 0:
            raise ValueError(
                "databricks_poll_interval_seconds must be greater than zero."
            )

        if databricks_poll_timeout_seconds <= 0:
            raise ValueError(
                "databricks_poll_timeout_seconds must be greater than zero."
            )

        self.databricks_poll_interval_seconds = (
            databricks_poll_interval_seconds
        )
        self.databricks_poll_timeout_seconds = (
            databricks_poll_timeout_seconds
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_approved_remediation(
        self,
        *,
        organization_id: str,
        recommendation_id: str,
        profile_run_id: str,
        connection_id: str,
        requested_by: str,
        technical_approved_by: str,
    ) -> dict[str, Any]:
        """
        Perform all governance/security checks without executing target SQL.
        """

        context = self._build_execution_context(
            organization_id=organization_id,
            recommendation_id=recommendation_id,
            profile_run_id=profile_run_id,
            connection_id=connection_id,
            requested_by=requested_by,
            technical_approved_by=technical_approved_by,
        )

        # BigQuery validation is a real warehouse dry-run. It validates syntax,
        # permissions, and referenced objects without mutating target data.
        if context["vendor"] == "BIGQUERY":
            self._dry_run_bigquery(
                connection=context["connection"],
                credentials=context["credentials"],
                sql=context["remediation_sql"],
            )

        execution_id = f"exec_{uuid.uuid4().hex[:24]}"
        validated_at = datetime.now(timezone.utc)

        try:
            self.dq_execution_repository.create_validation_record(
                execution_id=execution_id,
                organization_id=context["organization_id"],
                recommendation_id=context["recommendation_id"],
                profile_run_id=context["profile_run_id"],
                remediation_version_id=context.get("remediation_version_id"),
                connection_id=context["connection_id"],
                vendor=context["vendor"],
                environment=context["environment"],
                domain=context.get("domain"),
                artifact_type=context["artifact_type"],
                artifact_hash=context["artifact_hash"],
                sql_dialect=context["sql_dialect"],
                approval_status=context["approval_status"],
                safety_status=context["safety_status"],
                requested_by=context["requested_by"],
                technical_approved_by=context["technical_approved_by"],
                validated_at=validated_at,
            )
        except Exception as exc:
            logger.exception(
                "DQ execution validation audit persistence failed. "
                "execution_id=%s organization_id=%s error=%s",
                execution_id,
                context["organization_id"],
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "DQ remediation validation could not be recorded in the "
                    "execution audit log. No target SQL was executed."
                ),
            ) from exc

        return self._build_result(
            context=context,
            execution_id=execution_id,
            execution_status="VALIDATED_NOT_EXECUTED",
            vendor_status="NOT_EXECUTED",
            rows_affected=None,
            statement_id=None,
            executed_at=None,
            error_message=None,
            validated_at=validated_at,
        )

    def execute_approved_remediation(
        self,
        *,
        organization_id: str,
        recommendation_id: str,
        profile_run_id: str,
        connection_id: str,
        requested_by: str,
        technical_approved_by: str,
    ) -> dict[str, Any]:
        """
        Execute the exact persisted and approved remediation SQL against
        a tenant-bound DEV / STG enterprise connection.
        """

        # Defense-in-depth entitlement enforcement.
        # Generation/validation may remain available without this capability,
        # but governed target execution requires the explicit SQL entitlement.
        self.entitlement_service.require_governed_sql_execution(
            organization_id=organization_id,
        )

        context = self._build_execution_context(
            organization_id=organization_id,
            recommendation_id=recommendation_id,
            profile_run_id=profile_run_id,
            connection_id=connection_id,
            requested_by=requested_by,
            technical_approved_by=technical_approved_by,
        )

        execution_id = f"exec_{uuid.uuid4().hex[:24]}"
        started_at = datetime.now(timezone.utc)

        logger.info(
            "DQ remediation execution started. "
            "execution_id=%s organization_id=%s recommendation_id=%s "
            "connection_id=%s environment=%s vendor=%s artifact_hash=%s "
            "requested_by=%s technical_approved_by=%s",
            execution_id,
            context["organization_id"],
            context["recommendation_id"],
            context["connection_id"],
            context["environment"],
            context["vendor"],
            context["artifact_hash"],
            context["requested_by"],
            context["technical_approved_by"],
        )

        try:
            self.dq_execution_repository.create_execution(
                execution_id=execution_id,
                organization_id=context["organization_id"],
                recommendation_id=context["recommendation_id"],
                profile_run_id=context["profile_run_id"],
                remediation_version_id=context.get("remediation_version_id"),
                connection_id=context["connection_id"],
                vendor=context["vendor"],
                environment=context["environment"],
                domain=context.get("domain"),
                artifact_type=context["artifact_type"],
                artifact_hash=context["artifact_hash"],
                sql_dialect=context["sql_dialect"],
                approval_status=context["approval_status"],
                safety_status=context["safety_status"],
                requested_by=context["requested_by"],
                technical_approved_by=context["technical_approved_by"],
                execution_status="RUNNING",
                vendor_status="NOT_STARTED",
                started_at=started_at,
            )
        except Exception as exc:
            logger.exception(
                "DQ execution audit start failed. "
                "execution_id=%s organization_id=%s error=%s",
                execution_id,
                context["organization_id"],
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "DQ execution audit could not be initialized. "
                    "No target SQL was executed."
                ),
            ) from exc

        target_execution_succeeded = False

        try:
            if context["vendor"] == "SNOWFLAKE":
                vendor_result = self._execute_snowflake(
                    connection=context["connection"],
                    credentials=context["credentials"],
                    sql=context["remediation_sql"],
                )

            elif context["vendor"] == "DATABRICKS":
                vendor_result = self._execute_databricks(
                    organization_id=context["organization_id"],
                    connection_id=context["connection_id"],
                    connection=context["connection"],
                    credentials=context["credentials"],
                    sql=context["remediation_sql"],
                )

            elif context["vendor"] == "BIGQUERY":
                self._dry_run_bigquery(
                    connection=context["connection"],
                    credentials=context["credentials"],
                    sql=context["remediation_sql"],
                )
                vendor_result = self._execute_bigquery(
                    connection=context["connection"],
                    credentials=context["credentials"],
                    sql=context["remediation_sql"],
                )

            else:
                # Defensive fail-closed. _build_execution_context already checks.
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Unsupported execution vendor.",
                )

            executed_at = datetime.now(timezone.utc)
            target_execution_succeeded = True

            try:
                self.dq_execution_repository.mark_execution_succeeded(
                    execution_id=execution_id,
                    organization_id=context["organization_id"],
                    vendor_status=str(
                        vendor_result.get("vendor_status") or "SUCCEEDED"
                    ),
                    rows_affected=vendor_result.get("rows_affected"),
                    statement_id=vendor_result.get("statement_id"),
                    executed_at=executed_at,
                )
            except Exception as audit_exc:
                logger.critical(
                    "DQ target execution succeeded but audit finalization failed. "
                    "execution_id=%s organization_id=%s error=%s",
                    execution_id,
                    context["organization_id"],
                    type(audit_exc).__name__,
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        "DQ remediation completed against the target connection, "
                        "but execution audit finalization failed. Do not retry "
                        f"automatically. Reference execution_id={execution_id}."
                    ),
                ) from audit_exc

            logger.info(
                "DQ remediation execution succeeded. "
                "execution_id=%s organization_id=%s recommendation_id=%s "
                "connection_id=%s vendor=%s rows_affected=%s statement_id=%s",
                execution_id,
                context["organization_id"],
                context["recommendation_id"],
                context["connection_id"],
                context["vendor"],
                vendor_result.get("rows_affected"),
                vendor_result.get("statement_id"),
            )

            return self._build_result(
                context=context,
                execution_id=execution_id,
                execution_status="SUCCEEDED",
                vendor_status=str(
                    vendor_result.get("vendor_status") or "SUCCEEDED"
                ),
                rows_affected=vendor_result.get("rows_affected"),
                statement_id=vendor_result.get("statement_id"),
                executed_at=executed_at,
                error_message=None,
                started_at=started_at,
            )

        except HTTPException as exc:
            if not target_execution_succeeded:
                try:
                    self.dq_execution_repository.mark_execution_failed(
                        execution_id=execution_id,
                        organization_id=context["organization_id"],
                        vendor_status="FAILED",
                        error_code=type(exc).__name__,
                        error_message="Target DQ remediation execution failed.",
                        executed_at=datetime.now(timezone.utc),
                    )
                except Exception:
                    logger.exception(
                        "DQ execution failure audit finalization also failed. "
                        "execution_id=%s organization_id=%s",
                        execution_id,
                        context["organization_id"],
                    )
            raise

        except Exception as exc:
            logger.exception(
                "DQ remediation execution failed. "
                "execution_id=%s organization_id=%s recommendation_id=%s "
                "connection_id=%s vendor=%s error=%s",
                execution_id,
                context["organization_id"],
                context["recommendation_id"],
                context["connection_id"],
                context["vendor"],
                type(exc).__name__,
            )

            try:
                self.dq_execution_repository.mark_execution_failed(
                    execution_id=execution_id,
                    organization_id=context["organization_id"],
                    vendor_status="FAILED",
                    error_code=type(exc).__name__,
                    error_message="Target DQ remediation execution failed.",
                    executed_at=datetime.now(timezone.utc),
                )
            except Exception:
                logger.exception(
                    "DQ execution failure audit finalization also failed. "
                    "execution_id=%s organization_id=%s",
                    execution_id,
                    context["organization_id"],
                )

            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "DQ remediation execution failed against the target "
                    f"{context['vendor']} connection. No credential values "
                    "were returned."
                ),
            ) from exc

    def execute_approved_google_sheets_regex_remediation(
        self,
        *,
        organization_id: str,
        recommendation_id: str,
        profile_run_id: str,
        connection_id: str,
        spreadsheet_id: str,
        sheet_name: str,
        requested_by: str,
        technical_approved_by: str,
        header_row: int = 1,
    ) -> dict[str, Any]:
        """
        Execute one exact, steward-approved Google Sheets REGEX remediation.

        This path is intentionally separate from SQL execution:
          * REGEX must exactly match the governed allow-listed artifact.
          * only a standard GRID worksheet may be mutated.
          * only the recommendation's target field/column may be changed.
          * writes are exact RAW cell replacements through the Sheets API.
          * PROD remains blocked by the same environment policy.
        """

        # Defense-in-depth entitlement enforcement.
        # AI-generated REGEX may still be reviewed manually, but governed
        # Google Sheets write-back requires the explicit execution entitlement.
        self.entitlement_service.require_governed_regex_execution(
            organization_id=organization_id,
        )

        context = self._build_google_sheets_execution_context(
            organization_id=organization_id,
            recommendation_id=recommendation_id,
            profile_run_id=profile_run_id,
            connection_id=connection_id,
            requested_by=requested_by,
            technical_approved_by=technical_approved_by,
        )

        effective_spreadsheet_id = self._require_value(
            spreadsheet_id,
            field_name="spreadsheet_id",
        )
        effective_sheet_name = self._require_value(
            sheet_name,
            field_name="sheet_name",
        )
        safe_header_row = max(
            1,
            int(header_row),
        )

        google_credentials = self._build_google_user_credentials(
            credentials=context["credentials"]
        )

        connector = GoogleSheetsConnector(
            credentials=google_credentials
        )

        target_column = str(
            context.get("source_column")
            or context.get("field_name")
            or ""
        ).strip()

        if not target_column:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Approved Google Sheets remediation is missing "
                    "its mapped source column."
                ),
            )

        source_cells = connector.read_column_cells(
            spreadsheet_id=effective_spreadsheet_id,
            sheet_name=effective_sheet_name,
            column_name=target_column,
            header_row=safe_header_row,
            limit=100_000,
        )

        updates: list[dict[str, Any]] = []
        rows_evaluated = 0
        rows_skipped = 0

        for cell in source_cells:
            rows_evaluated += 1

            transformation = self.apply_google_sheets_regex_value(
                rule_id=context["rule_id"],
                remediation_regex=context["remediation_regex"],
                value=cell.get("value"),
            )

            # Fail closed at the service layer:
            # non-matching cells and already-canonical cells are immutable.
            if (
                transformation.get("matched") is not True
                or transformation.get("changed") is not True
            ):
                rows_skipped += 1
                continue

            transformed_value = transformation.get(
                "transformed_value"
            )

            # A governed transformation may never blank a source cell.
            if (
                transformed_value is None
                or str(transformed_value) == ""
            ):
                rows_skipped += 1
                logger.warning(
                    "Governed Google Sheets remediation skipped an "
                    "empty transformed value. row_number=%s "
                    "rule_id=%s",
                    cell.get("row_number"),
                    context["rule_id"],
                )
                continue

            updates.append(
                {
                    "row_number": cell["row_number"],
                    "column_name": target_column,
                    "value": transformed_value,
                }
            )

        rows_changed = len(updates)

        # ---------------------------------------------------------
        # Governed worksheet write-safety preflight
        # ---------------------------------------------------------
        # The connector performs this check again at write time. Running it
        # here lets the service return a clean governance conflict before an
        # execution audit is opened and before any mutation is attempted.
        if updates:
            try:
                write_safety = connector.inspect_governed_write_safety(
                    spreadsheet_id=effective_spreadsheet_id,
                    sheet_name=effective_sheet_name,
                    updates=updates,
                    header_row=safe_header_row,
                    max_updates=10_000,
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Google Sheets remediation write-safety "
                        f"preflight failed: {exc}"
                    ),
                ) from exc
            except HTTPException:
                raise
            except Exception as exc:
                logger.exception(
                    "Google Sheets write-safety preflight failed. "
                    "organization_id=%s recommendation_id=%s error=%s",
                    context["organization_id"],
                    context["recommendation_id"],
                    type(exc).__name__,
                )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=(
                        "Google Sheets remediation write-safety "
                        "preflight could not be completed."
                    ),
                ) from exc

            if not write_safety.get("safe_to_write"):
                reason = str(
                    write_safety.get("reason")
                    or "UNSAFE_WRITE_TARGET"
                )

                if reason == "TARGET_CELL_CONTAINS_FORMULA":
                    detail = (
                        "Google Sheets remediation is blocked because "
                        "one or more target cells contain formulas. "
                        "Execute against physical source cells instead."
                    )
                elif reason == "SPILL_FORMULA_PRESENT":
                    detail = (
                        "Google Sheets remediation is blocked because "
                        "the worksheet contains a spill/array formula. "
                        "Writing physical values could collapse formula "
                        "output and cause #REF! errors. Execute against "
                        "the underlying physical source worksheet instead."
                    )
                elif reason == "NON_GRID_WORKSHEET":
                    detail = (
                        "Google Sheets remediation is blocked because "
                        "the selected worksheet is not a standard GRID "
                        "worksheet."
                    )
                else:
                    detail = (
                        "Google Sheets remediation is blocked by the "
                        f"write-safety preflight: {reason}."
                    )

                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=detail,
                )

        execution_id = f"exec_{uuid.uuid4().hex[:24]}"
        started_at = datetime.now(timezone.utc)

        try:
            self.dq_execution_repository.create_governed_regex_execution(
            execution_id=execution_id,
            organization_id=context["organization_id"],
            recommendation_id=context["recommendation_id"],
            profile_run_id=context["profile_run_id"],
            remediation_version_id=context.get(
                "remediation_version_id"
            ),
            connection_id=context["connection_id"],
            vendor=context["vendor"],                       
            environment=context["environment"],
            domain=context.get("domain"),
            rule_id=context["rule_id"],
            artifact_hash=context["artifact_hash"],
            approval_status=context["approval_status"],
            safety_status=context["safety_status"],
            requested_by=context["requested_by"],
            technical_approved_by=context[
                "technical_approved_by"
            ],
            execution_target=context["execution_target"],  
            rows_evaluated=rows_evaluated,
            rows_changed=rows_changed,
            rows_skipped=rows_skipped,
            started_at=started_at,
        )
        except Exception as exc:
            logger.exception(
                "Google Sheets DQ execution audit start failed. "
                "execution_id=%s organization_id=%s error=%s",
                execution_id,
                context["organization_id"],
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Google Sheets remediation audit could not be "
                    "initialized. No spreadsheet cells were changed."
                ),
            ) from exc

        target_execution_succeeded = False

        try:
            if updates:
                write_result = connector.apply_governed_cell_updates(
                    spreadsheet_id=effective_spreadsheet_id,
                    sheet_name=effective_sheet_name,
                    updates=updates,
                    header_row=safe_header_row,
                    max_updates=10_000,
                )
                rows_affected = int(
                    write_result.get("updated_cells")
                    or 0
                )
            else:
                write_result = {
                    "success": True,
                    "requested_updates": 0,
                    "updated_cells": 0,
                }
                rows_affected = 0

            executed_at = datetime.now(timezone.utc)
            target_execution_succeeded = True

            try:
                self.dq_execution_repository.mark_execution_succeeded(
                    execution_id=execution_id,
                    organization_id=context["organization_id"],
                    vendor_status="SUCCEEDED",
                    rows_affected=rows_affected,
                    statement_id=None,
                    rows_evaluated=rows_evaluated,
                    rows_changed=rows_changed,
                    rows_skipped=rows_skipped,
                    executed_at=executed_at,
                )
            except Exception as audit_exc:
                logger.critical(
                    "Google Sheets remediation succeeded but audit "
                    "finalization failed. execution_id=%s "
                    "organization_id=%s error=%s",
                    execution_id,
                    context["organization_id"],
                    type(audit_exc).__name__,
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        "Google Sheets remediation completed, but "
                        "execution audit finalization failed. Do not "
                        "retry automatically. Reference "
                        f"execution_id={execution_id}."
                    ),
                ) from audit_exc

            result = self._build_result(
                context=context,
                execution_id=execution_id,
                execution_status="SUCCEEDED",
                vendor_status="SUCCEEDED",
                rows_affected=rows_affected,
                statement_id=None,
                executed_at=executed_at,
                error_message=None,
                started_at=started_at,
                rows_evaluated=rows_evaluated,
                rows_changed=rows_changed,
                rows_skipped=rows_skipped,
            )

            result.update(
                {
                    "execution_target": "GOOGLE_SHEETS",
                    "rule_id": context["rule_id"],
                    "field_name": target_column,
                    "spreadsheet_id": effective_spreadsheet_id,
                    "sheet_name": effective_sheet_name,
                    "reprofile_required": True,
                    "policy_verification_status": "PENDING_REPROFILE",
                }
            )

            logger.info(
                "Google Sheets DQ remediation succeeded. "
                "execution_id=%s organization_id=%s rule_id=%s "
                "rows_evaluated=%s rows_changed=%s rows_skipped=%s",
                execution_id,
                context["organization_id"],
                context["rule_id"],
                rows_evaluated,
                rows_changed,
                rows_skipped,
            )

            return result

        except HTTPException:
            if not target_execution_succeeded:
                try:
                    self.dq_execution_repository.mark_execution_failed(
                        execution_id=execution_id,
                        organization_id=context["organization_id"],
                        vendor_status="FAILED",
                        error_code="HTTPException",
                        error_message=(
                            "Google Sheets governed remediation failed."
                        ),
                        executed_at=datetime.now(timezone.utc),
                    )
                except Exception:
                    logger.exception(
                        "Google Sheets execution failure audit "
                        "finalization also failed. execution_id=%s",
                        execution_id,
                    )
            raise

        except Exception as exc:
            logger.exception(
                "Google Sheets governed remediation failed. "
                "execution_id=%s organization_id=%s rule_id=%s error=%s",
                execution_id,
                context["organization_id"],
                context["rule_id"],
                type(exc).__name__,
            )

            try:
                self.dq_execution_repository.mark_execution_failed(
                    execution_id=execution_id,
                    organization_id=context["organization_id"],
                    vendor_status="FAILED",
                    error_code=type(exc).__name__,
                    error_message=(
                        "Google Sheets governed remediation failed."
                    ),
                    executed_at=datetime.now(timezone.utc),
                )
            except Exception:
                logger.exception(
                    "Google Sheets execution failure audit "
                    "finalization also failed. execution_id=%s",
                    execution_id,
                )

            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "Google Sheets governed remediation failed. "
                    "No credential values were returned."
                ),
            ) from exc


    def execute_approved_onedrive_regex_remediation(
        self,
        *,
        organization_id: str,
        recommendation_id: str,
        profile_run_id: str,
        connection_id: str,
        drive_id: str,
        item_id: str,
        sheet_name: str,
        requested_by: str,
        technical_approved_by: str,
        header_row: int = 1,
    ) -> dict[str, Any]:
        """
        Execute one exact, steward-approved REGEX remediation against
        physical Excel worksheet cells in OneDrive / SharePoint.
        """
        self.entitlement_service.require_governed_regex_execution(
            organization_id=organization_id,
        )

        context = self._build_onedrive_execution_context(
            organization_id=organization_id,
            recommendation_id=recommendation_id,
            profile_run_id=profile_run_id,
            connection_id=connection_id,
            requested_by=requested_by,
            technical_approved_by=technical_approved_by,
        )

        effective_drive_id = self._require_value(drive_id, field_name="drive_id")
        effective_item_id = self._require_value(item_id, field_name="item_id")
        effective_sheet_name = self._require_value(
            sheet_name,
            field_name="sheet_name",
        )
        safe_header_row = max(1, int(header_row))

        connector = OneDriveConnector(
            credentials=context["credentials"]
        )

        target_column = str(
            context.get("source_column")
            or context.get("field_name")
            or ""
        ).strip()
        if not target_column:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Approved OneDrive Excel remediation is missing "
                    "its mapped source column."
                ),
            )

        source_cells = connector.read_column_cells(
            drive_id=effective_drive_id,
            item_id=effective_item_id,
            sheet_name=effective_sheet_name,
            column_name=target_column,
            header_row=safe_header_row,
            limit=100_000,
        )

        updates: list[dict[str, Any]] = []
        rows_evaluated = 0
        rows_skipped = 0

        for cell in source_cells:
            rows_evaluated += 1
            transformation = self.apply_onedrive_regex_value(
                rule_id=context["rule_id"],
                remediation_regex=context["remediation_regex"],
                value=cell.get("value"),
            )
            if (
                transformation.get("matched") is not True
                or transformation.get("changed") is not True
            ):
                rows_skipped += 1
                continue

            transformed_value = transformation.get("transformed_value")
            if transformed_value is None or str(transformed_value) == "":
                rows_skipped += 1
                logger.warning(
                    "Governed OneDrive remediation skipped an empty "
                    "transformed value. row_number=%s rule_id=%s",
                    cell.get("row_number"),
                    context["rule_id"],
                )
                continue

            updates.append(
                {
                    "row_number": cell["row_number"],
                    "column_name": target_column,
                    "value": transformed_value,
                }
            )

        rows_changed = len(updates)

        # Preflight before opening the execution audit. The connector
        # rechecks safety again at mutation time.
        if updates:
            try:
                write_safety = connector.inspect_governed_write_safety(
                    drive_id=effective_drive_id,
                    item_id=effective_item_id,
                    sheet_name=effective_sheet_name,
                    updates=updates,
                    header_row=safe_header_row,
                    max_updates=10_000,
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "OneDrive Excel remediation write-safety "
                        f"preflight failed: {exc}"
                    ),
                ) from exc
            except HTTPException:
                raise
            except Exception as exc:
                logger.exception(
                    "OneDrive Excel write-safety preflight failed. "
                    "organization_id=%s recommendation_id=%s error=%s",
                    context["organization_id"],
                    context["recommendation_id"],
                    type(exc).__name__,
                )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=(
                        "OneDrive Excel remediation write-safety "
                        "preflight could not be completed."
                    ),
                ) from exc

            if not write_safety.get("safe_to_write"):
                reason = str(
                    write_safety.get("reason") or "UNSAFE_WRITE_TARGET"
                )
                if reason == "TARGET_CELL_CONTAINS_FORMULA":
                    detail = (
                        "OneDrive Excel remediation is blocked because "
                        "one or more target cells contain formulas. "
                        "Execute against physical source cells instead."
                    )
                else:
                    detail = (
                        "OneDrive Excel remediation is blocked by the "
                        f"write-safety preflight: {reason}."
                    )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=detail,
                )

        execution_id = f"exec_{uuid.uuid4().hex[:24]}"
        started_at = datetime.now(timezone.utc)

        try:
            # Reuse the existing governed REGEX audit shape. The audit
            # vendor/target is carried by the execution context/result.
            self.dq_execution_repository.create_governed_regex_execution(
                execution_id=execution_id,
                organization_id=context["organization_id"],
                recommendation_id=context["recommendation_id"],
                profile_run_id=context["profile_run_id"],
                remediation_version_id=context.get("remediation_version_id"),
                connection_id=context["connection_id"],
                environment=context["environment"],
                domain=context.get("domain"),
                rule_id=context["rule_id"],
                artifact_hash=context["artifact_hash"],
                approval_status=context["approval_status"],
                safety_status=context["safety_status"],
                requested_by=context["requested_by"],
                technical_approved_by=context["technical_approved_by"],
                rows_evaluated=rows_evaluated,
                rows_changed=rows_changed,
                rows_skipped=rows_skipped,
                started_at=started_at,
                vendor=context["vendor"],
                execution_target=context["execution_target"],
            )
        except Exception as exc:
            logger.exception(
                "OneDrive DQ execution audit start failed. "
                "execution_id=%s organization_id=%s error=%s",
                execution_id,
                context["organization_id"],
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "OneDrive Excel remediation audit could not be "
                    "initialized. No workbook cells were changed."
                ),
            ) from exc

        target_execution_succeeded = False
        try:
            if updates:
                write_result = connector.apply_governed_cell_updates(
                    drive_id=effective_drive_id,
                    item_id=effective_item_id,
                    sheet_name=effective_sheet_name,
                    updates=updates,
                    header_row=safe_header_row,
                    max_updates=10_000,
                )
                rows_affected = int(
                    write_result.get("updated_cells") or 0
                )
            else:
                rows_affected = 0

            executed_at = datetime.now(timezone.utc)
            target_execution_succeeded = True

            try:
                self.dq_execution_repository.mark_execution_succeeded(
                    execution_id=execution_id,
                    organization_id=context["organization_id"],
                    vendor_status="SUCCEEDED",
                    rows_affected=rows_affected,
                    statement_id=None,
                    rows_evaluated=rows_evaluated,
                    rows_changed=rows_changed,
                    rows_skipped=rows_skipped,
                    executed_at=executed_at,
                )
            except Exception as audit_exc:
                logger.critical(
                    "OneDrive remediation succeeded but audit finalization "
                    "failed. execution_id=%s organization_id=%s error=%s",
                    execution_id,
                    context["organization_id"],
                    type(audit_exc).__name__,
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        "OneDrive Excel remediation completed, but "
                        "execution audit finalization failed. Do not retry "
                        f"automatically. Reference execution_id={execution_id}."
                    ),
                ) from audit_exc

            result = self._build_result(
                context=context,
                execution_id=execution_id,
                execution_status="SUCCEEDED",
                vendor_status="SUCCEEDED",
                rows_affected=rows_affected,
                statement_id=None,
                executed_at=executed_at,
                error_message=None,
                started_at=started_at,
                rows_evaluated=rows_evaluated,
                rows_changed=rows_changed,
                rows_skipped=rows_skipped,
            )
            result.update(
                {
                    "execution_target": "ONEDRIVE_EXCEL",
                    "rule_id": context["rule_id"],
                    "field_name": target_column,
                    "drive_id": effective_drive_id,
                    "item_id": effective_item_id,
                    "sheet_name": effective_sheet_name,
                    "reprofile_required": True,
                    "policy_verification_status": "PENDING_REPROFILE",
                }
            )
            return result

        except HTTPException:
            if not target_execution_succeeded:
                try:
                    self.dq_execution_repository.mark_execution_failed(
                        execution_id=execution_id,
                        organization_id=context["organization_id"],
                        vendor_status="FAILED",
                        error_code="HTTPException",
                        error_message=(
                            "OneDrive Excel governed remediation failed."
                        ),
                        executed_at=datetime.now(timezone.utc),
                    )
                except Exception:
                    logger.exception(
                        "OneDrive execution failure audit finalization "
                        "also failed. execution_id=%s",
                        execution_id,
                    )
            raise
        except Exception as exc:
            logger.exception(
                "OneDrive Excel governed remediation failed. "
                "execution_id=%s organization_id=%s rule_id=%s error=%s",
                execution_id,
                context["organization_id"],
                context["rule_id"],
                type(exc).__name__,
            )
            try:
                self.dq_execution_repository.mark_execution_failed(
                    execution_id=execution_id,
                    organization_id=context["organization_id"],
                    vendor_status="FAILED",
                    error_code=type(exc).__name__,
                    error_message=(
                        "OneDrive Excel governed remediation failed."
                    ),
                    executed_at=datetime.now(timezone.utc),
                )
            except Exception:
                logger.exception(
                    "OneDrive execution failure audit finalization "
                    "also failed. execution_id=%s",
                    execution_id,
                )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "OneDrive Excel governed remediation failed. "
                    "No credential values were returned."
                ),
            ) from exc

    # ------------------------------------------------------------------
    # Context / governance validation
    # ------------------------------------------------------------------

    def _build_execution_context(
        self,
        *,
        organization_id: str,
        recommendation_id: str,
        profile_run_id: str,
        connection_id: str,
        requested_by: str,
        technical_approved_by: str,
    ) -> dict[str, Any]:
        effective_organization_id = self._require_organization_id(
            organization_id
        )
        effective_recommendation_id = self._require_value(
            recommendation_id,
            field_name="recommendation_id",
        )
        effective_profile_run_id = self._require_value(
            profile_run_id,
            field_name="profile_run_id",
        )
        effective_connection_id = self._require_connection_id(
            connection_id
        )
        effective_requested_by = self._require_actor(
            requested_by,
            field_name="requested_by",
        )
        effective_technical_approved_by = self._require_actor(
            technical_approved_by,
            field_name="technical_approved_by",
        )

        # Subscription access is checked before any external execution.
        self.entitlement_service.require_active_product_access(
            organization_id=effective_organization_id,
        )

        recommendation = self._load_approved_recommendation(
            organization_id=effective_organization_id,
            recommendation_id=effective_recommendation_id,
            profile_run_id=effective_profile_run_id,
        )

        domain = str(
            recommendation.get("domain") or ""
        ).strip().upper()

        if domain:
            self.entitlement_service.require_domain_entitlement(
                organization_id=effective_organization_id,
                domain=domain,
            )

        connection = self._load_connection(
            organization_id=effective_organization_id,
            connection_id=effective_connection_id,
        )

        self._validate_connection_for_execution(
            connection=connection,
            organization_id=effective_organization_id,
        )

        vendor = str(
            connection.get("vendor") or ""
        ).strip().upper()

        if vendor == "GOOGLE_BIGQUERY":
            vendor = "BIGQUERY"

        environment = str(
            connection.get("environment") or ""
        ).strip().upper()

        artifact_type = str(
            recommendation.get("remediation_artifact_type")
            or ""
        ).strip().upper()

        approval_status = str(
            recommendation.get("remediation_approval_status")
            or ""
        ).strip().upper()

        safety_status = str(
            recommendation.get("remediation_safety_status")
            or ""
        ).strip().upper()

        sql_dialect = str(
            recommendation.get("remediation_sql_dialect")
            or ""
        ).strip().upper()

        remediation_sql = str(
            recommendation.get("remediation_sql")
            or ""
        ).strip()

        if approval_status != "APPROVED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "DQ remediation cannot execute until the steward "
                    "approval status is APPROVED."
                ),
            )

        if artifact_type not in self.SAFE_ARTIFACT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Initial governed execution supports only persisted "
                    "SQL remediation artifacts. REGEX artifacts must first "
                    "be converted to approved target-platform SQL."
                ),
            )

        if safety_status == "BLOCKED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Blocked DQ remediation artifacts cannot execute.",
            )

        if not remediation_sql:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Approved remediation SQL is missing.",
            )

        self._validate_sql_dialect(
            vendor=vendor,
            sql_dialect=sql_dialect,
        )

        normalized_sql = self._validate_and_normalize_sql(
            remediation_sql
        )

        if vendor == "BIGQUERY":
            source_type = str(recommendation.get("source_type") or "").strip().upper()
            source_connection_id = str(recommendation.get("connection_id") or "").strip()
            if source_type != "BIGQUERY":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Approved remediation is not bound to a native BigQuery profile source.",
                )
            if source_connection_id != effective_connection_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Execution connection does not match the profiled BigQuery source.",
                )
            project_id = self._require_value(recommendation.get("project_id"), field_name="project_id")
            dataset_id = self._require_value(recommendation.get("dataset_id"), field_name="dataset_id")
            table_id = self._require_value(recommendation.get("table_id"), field_name="table_id")
            self._validate_bigquery_update_target(
                sql=normalized_sql,
                expected_target=f"{project_id}.{dataset_id}.{table_id}",
            )

        artifact_hash = hashlib.sha256(
            normalized_sql.encode("utf-8")
        ).hexdigest()

        credential_reference = str(
            connection.get("credential_reference")
            or ""
        ).strip()

        if not credential_reference:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Connection credential reference is missing.",
            )

        try:
            credentials = self.secret_manager.access_secret(
                credential_reference
            )
        except Exception as exc:
            logger.exception(
                "DQ execution secret retrieval failed. "
                "organization_id=%s connection_id=%s error=%s",
                effective_organization_id,
                effective_connection_id,
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unable to retrieve connection credentials.",
            ) from exc

        if not isinstance(credentials, dict):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Connection credentials have an invalid shape.",
            )

        return {
            "organization_id": effective_organization_id,
            "recommendation_id": effective_recommendation_id,
            "profile_run_id": effective_profile_run_id,
            "connection_id": effective_connection_id,
            "requested_by": effective_requested_by,
            "technical_approved_by": (
                effective_technical_approved_by
            ),
            "domain": domain or None,
            "vendor": vendor,
            "environment": environment,
            "artifact_type": artifact_type,
            "approval_status": approval_status,
            "safety_status": safety_status,
            "sql_dialect": sql_dialect,
            "remediation_version_id": recommendation.get(
                "remediation_version_id"
            ),
            "remediation_sql": normalized_sql,
            "artifact_hash": artifact_hash,
            "connection": connection,
            "credentials": credentials,
        }

    def _build_google_sheets_execution_context(
        self,
        *,
        organization_id: str,
        recommendation_id: str,
        profile_run_id: str,
        connection_id: str,
        requested_by: str,
        technical_approved_by: str,
    ) -> dict[str, Any]:
        effective_organization_id = self._require_organization_id(
            organization_id
        )
        effective_recommendation_id = self._require_value(
            recommendation_id,
            field_name="recommendation_id",
        )
        effective_profile_run_id = self._require_value(
            profile_run_id,
            field_name="profile_run_id",
        )
        effective_connection_id = self._require_connection_id(
            connection_id
        )
        effective_requested_by = self._require_actor(
            requested_by,
            field_name="requested_by",
        )
        effective_technical_approved_by = self._require_actor(
            technical_approved_by,
            field_name="technical_approved_by",
        )

        self.entitlement_service.require_active_product_access(
            organization_id=effective_organization_id,
        )

        recommendation = self._load_approved_recommendation(
            organization_id=effective_organization_id,
            recommendation_id=effective_recommendation_id,
            profile_run_id=effective_profile_run_id,
        )

        domain = str(
            recommendation.get("domain")
            or ""
        ).strip().upper()

        if domain:
            self.entitlement_service.require_domain_entitlement(
                organization_id=effective_organization_id,
                domain=domain,
            )

        connection = self._load_connection(
            organization_id=effective_organization_id,
            connection_id=effective_connection_id,
        )

        self._validate_google_sheets_connection_for_execution(
            connection=connection,
            organization_id=effective_organization_id,
        )

        artifact_type = str(
            recommendation.get("remediation_artifact_type")
            or ""
        ).strip().upper()

        approval_status = str(
            recommendation.get("remediation_approval_status")
            or ""
        ).strip().upper()

        safety_status = str(
            recommendation.get("remediation_safety_status")
            or ""
        ).strip().upper()

        remediation_regex = str(
            recommendation.get("remediation_regex")
            or ""
        ).strip()

        rule_id = str(
            recommendation.get("rule_id")
            or ""
        ).strip().upper()

        field_name = str(
            recommendation.get("field_name")
            or ""
        ).strip()

        source_column = str(
            recommendation.get("source_column")
            or ""
        ).strip()

        if approval_status != "APPROVED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Google Sheets remediation cannot execute until "
                    "the steward approval status is APPROVED."
                ),
            )

        if (
            artifact_type
            not in self.GOOGLE_SHEETS_REGEX_ARTIFACT_TYPES
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Google Sheets governed execution requires an "
                    "approved REGEX remediation artifact."
                ),
            )

        if safety_status == "BLOCKED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Blocked DQ remediation artifacts cannot execute."
                ),
            )

        if not rule_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Approved Google Sheets remediation is missing rule_id."
                ),
            )

        if not field_name:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Approved Google Sheets remediation is missing field_name."
                ),
            )

        artifact = self.validate_google_sheets_regex_artifact(
            rule_id=rule_id,
            field_name=field_name,
            remediation_regex=remediation_regex,
        )

        credential_reference = str(
            connection.get("credential_reference")
            or ""
        ).strip()

        if not credential_reference:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Connection credential reference is missing.",
            )

        try:
            credentials = self.secret_manager.access_secret(
                credential_reference
            )
        except Exception as exc:
            logger.exception(
                "Google Sheets execution secret retrieval failed. "
                "organization_id=%s connection_id=%s error=%s",
                effective_organization_id,
                effective_connection_id,
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unable to retrieve Google connection credentials.",
            ) from exc

        if not isinstance(credentials, dict):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Google connection credentials have an invalid shape.",
            )

        flattened_credentials = self._flatten_credentials(
            credentials
        )

        # ---------------------------------------------------------
# Google Sheets OAuth execution diagnostics
# SECURITY: log metadata only — never tokens/secrets.
# ---------------------------------------------------------

        logger.warning(
            "GOOGLE SHEETS WRITE AUTH DEBUG "
            "organization_id=%s "
            "connection_id=%s "
            "credential_reference=%s "
            "credential_keys=%s "
            "flattened_credential_keys=%s "
            "stored_scopes=%r "
            "write_marker=%r",
            effective_organization_id,
            effective_connection_id,
            credential_reference,
            sorted(credentials.keys()),
            sorted(flattened_credentials.keys()),
            flattened_credentials.get("scopes"),
            flattened_credentials.get(
                "google_sheets_write_enabled"
            ),
        )

        granted_scopes = {
            str(value or "").strip()
            for value in (
                flattened_credentials.get("scopes")
                or []
            )
            if str(value or "").strip()
        }

        write_marker = bool(
            flattened_credentials.get(
                "google_sheets_write_enabled"
            )
        )

        if (
            self.GOOGLE_SHEETS_WRITE_SCOPE
            not in granted_scopes
            and not write_marker
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Google Sheets write authorization is missing. "
                    "Reconnect Google and approve Sheets write access."
                ),
            )

        return {
            "organization_id": effective_organization_id,
            "recommendation_id": effective_recommendation_id,
            "profile_run_id": effective_profile_run_id,
            "connection_id": effective_connection_id,
            "requested_by": effective_requested_by,
            "source_column": source_column,
            "technical_approved_by": effective_technical_approved_by,
            "domain": domain or None,
            "vendor": "GOOGLE",
            "connection_vendor": str(
                connection.get("vendor")
                or ""
            ).strip().upper(),
            "environment": str(
                connection.get("environment")
                or ""
            ).strip().upper(),
            "artifact_type": artifact_type,
            "approval_status": approval_status,
            "safety_status": safety_status,
            "sql_dialect": None,
            "remediation_version_id": recommendation.get(
                "remediation_version_id"
            ),
            "rule_id": rule_id,
            "field_name": field_name,
            "remediation_regex": artifact["pattern"],
            "artifact_hash": artifact["artifact_hash"],
            "execution_target": "GOOGLE_SHEETS",
            "connection": connection,
            "credentials": credentials,
        }


    def _build_onedrive_execution_context(
        self,
        *,
        organization_id: str,
        recommendation_id: str,
        profile_run_id: str,
        connection_id: str,
        requested_by: str,
        technical_approved_by: str,
    ) -> dict[str, Any]:
        effective_organization_id = self._require_organization_id(
            organization_id
        )
        effective_recommendation_id = self._require_value(
            recommendation_id,
            field_name="recommendation_id",
        )
        effective_profile_run_id = self._require_value(
            profile_run_id,
            field_name="profile_run_id",
        )
        effective_connection_id = self._require_connection_id(connection_id)
        effective_requested_by = self._require_actor(
            requested_by,
            field_name="requested_by",
        )
        effective_technical_approved_by = self._require_actor(
            technical_approved_by,
            field_name="technical_approved_by",
        )

        self.entitlement_service.require_active_product_access(
            organization_id=effective_organization_id,
        )
        recommendation = self._load_approved_recommendation(
            organization_id=effective_organization_id,
            recommendation_id=effective_recommendation_id,
            profile_run_id=effective_profile_run_id,
        )

        domain = str(recommendation.get("domain") or "").strip().upper()
        if domain:
            self.entitlement_service.require_domain_entitlement(
                organization_id=effective_organization_id,
                domain=domain,
            )

        connection = self._load_connection(
            organization_id=effective_organization_id,
            connection_id=effective_connection_id,
        )
        self._validate_onedrive_connection_for_execution(
            connection=connection,
            organization_id=effective_organization_id,
        )

        artifact_type = str(
            recommendation.get("remediation_artifact_type") or ""
        ).strip().upper()
        approval_status = str(
            recommendation.get("remediation_approval_status") or ""
        ).strip().upper()
        safety_status = str(
            recommendation.get("remediation_safety_status") or ""
        ).strip().upper()
        remediation_regex = str(
            recommendation.get("remediation_regex") or ""
        ).strip()
        rule_id = str(
            recommendation.get("rule_id") or ""
        ).strip().upper()
        field_name = str(
            recommendation.get("field_name") or ""
        ).strip()
        source_column = str(
            recommendation.get("source_column") or ""
        ).strip()

        if approval_status != "APPROVED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "OneDrive Excel remediation cannot execute until "
                    "the steward approval status is APPROVED."
                ),
            )
        if artifact_type != "REGEX":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "OneDrive Excel governed execution requires an "
                    "approved REGEX remediation artifact."
                ),
            )
        if safety_status == "BLOCKED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Blocked DQ remediation artifacts cannot execute.",
            )
        if not rule_id or not field_name:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Approved OneDrive Excel remediation is missing "
                    "rule_id or field_name."
                ),
            )

        artifact = self.validate_onedrive_regex_artifact(
            rule_id=rule_id,
            field_name=field_name,
            remediation_regex=remediation_regex,
        )

        credential_reference = str(
            connection.get("credential_reference") or ""
        ).strip()
        if not credential_reference:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Connection credential reference is missing.",
            )

        try:
            credentials = self.secret_manager.access_secret(
                credential_reference
            )
        except Exception as exc:
            logger.exception(
                "OneDrive execution secret retrieval failed. "
                "organization_id=%s connection_id=%s error=%s",
                effective_organization_id,
                effective_connection_id,
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unable to retrieve Microsoft connection credentials.",
            ) from exc

        if not isinstance(credentials, dict):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Microsoft connection credentials have an invalid shape.",
            )

        flattened = self._flatten_credentials(credentials)
        authentication_type = str(
            flattened.get("authentication_type") or ""
        ).strip().upper()
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
                    "Selected connection is not Microsoft OAuth authorized."
                ),
            )

        granted_scopes = {
            str(value or "").strip()
            for value in (flattened.get("scopes") or [])
            if str(value or "").strip()
        }
        write_marker = bool(
            flattened.get("microsoft_files_write_enabled")
        )
        if (
            self.MICROSOFT_FILES_WRITE_SCOPE not in granted_scopes
            and not write_marker
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Microsoft Files write authorization is missing. "
                    "Reconnect Microsoft and approve Files.ReadWrite."
                ),
            )

        return {
            "organization_id": effective_organization_id,
            "recommendation_id": effective_recommendation_id,
            "profile_run_id": effective_profile_run_id,
            "connection_id": effective_connection_id,
            "requested_by": effective_requested_by,
            "technical_approved_by": effective_technical_approved_by,
            "source_column": source_column,
            "domain": domain or None,
            "vendor": "MICROSOFT_ONEDRIVE",
            "connection_vendor": str(
                connection.get("vendor") or ""
            ).strip().upper(),
            "environment": str(
                connection.get("environment") or ""
            ).strip().upper(),
            "artifact_type": artifact_type,
            "approval_status": approval_status,
            "safety_status": safety_status,
            "sql_dialect": None,
            "remediation_version_id": recommendation.get(
                "remediation_version_id"
            ),
            "rule_id": rule_id,
            "field_name": field_name,
            "remediation_regex": artifact["pattern"],
            "artifact_hash": artifact["artifact_hash"],
            "execution_target": "ONEDRIVE_EXCEL",
            "connection": connection,
            "credentials": credentials,
        }

    def _validate_onedrive_connection_for_execution(
        self,
        *,
        connection: dict[str, Any],
        organization_id: str,
    ) -> None:
        if not connection.get("is_active", False):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Inactive enterprise connections cannot execute "
                    "OneDrive Excel remediation."
                ),
            )

        environment = str(
            connection.get("environment") or ""
        ).strip().upper()
        if environment in self.BLOCKED_ENVIRONMENTS:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Production OneDrive Excel remediation is blocked. "
                    "Select a DEV or STG enterprise connection."
                ),
            )
        if environment not in self.ALLOWED_ENVIRONMENTS:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "OneDrive Excel remediation is permitted only for "
                    "DEV or STG enterprise connections."
                ),
            )

        vendor = str(
            connection.get("vendor") or ""
        ).strip().upper()

        if vendor == "GOOGLE_BIGQUERY":
            vendor = "BIGQUERY"
        if vendor not in {
            "ONEDRIVE",
            "MICROSOFT_ONEDRIVE",
            "SHAREPOINT",
        }:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "OneDrive Excel remediation requires a Microsoft "
                    "OAuth OneDrive or SharePoint connection."
                ),
            )

        if self.require_healthy_connection:
            health_status = str(
                connection.get("health_status") or ""
            ).strip().upper()
            if health_status != "HEALTHY":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Microsoft enterprise connection must pass its "
                        "health test before remediation can execute."
                    ),
                )

        if self.require_execution_capability:
            capabilities = {
                str(value or "").strip().upper()
                for value in (
                    connection.get("connection_capabilities") or []
                )
                if str(value or "").strip()
            }
            if not capabilities.intersection(
                self.ONEDRIVE_EXECUTION_CAPABILITIES
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "Microsoft connection is not authorized for DQ "
                        "write-back. Add EXECUTE_DQ_REMEDIATION or "
                        "WRITE_DATA to its approved capabilities."
                    ),
                )

        record_organization_id = str(
            connection.get("organization_id") or ""
        ).strip()
        if record_organization_id != organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Microsoft enterprise connection tenant "
                    "validation failed."
                ),
            )

    def _validate_google_sheets_connection_for_execution(
        self,
        *,
        connection: dict[str, Any],
        organization_id: str,
    ) -> None:
        if not connection.get("is_active", False):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Inactive enterprise connections cannot execute "
                    "Google Sheets remediation."
                ),
            )

        environment = str(
            connection.get("environment")
            or ""
        ).strip().upper()

        if environment in self.BLOCKED_ENVIRONMENTS:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Production Google Sheets remediation is blocked. "
                    "Select a DEV or STG enterprise connection."
                ),
            )

        if environment not in self.ALLOWED_ENVIRONMENTS:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Google Sheets remediation is permitted only for "
                    "DEV or STG enterprise connections."
                ),
            )

        vendor = str(
            connection.get("vendor")
            or ""
        ).strip().upper()

        if vendor not in {
            "BIGQUERY",
            "GOOGLE_BIGQUERY",
            "GOOGLE",
        }:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Google Sheets remediation requires a Google OAuth "
                    "enterprise connection."
                ),
            )

        if self.require_healthy_connection:
            health_status = str(
                connection.get("health_status")
                or ""
            ).strip().upper()

            if health_status != "HEALTHY":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Google enterprise connection must pass its "
                        "health test before remediation can execute."
                    ),
                )

        if self.require_execution_capability:
            capabilities = {
                str(value or "").strip().upper()
                for value in (
                    connection.get("connection_capabilities")
                    or []
                )
                if str(value or "").strip()
            }

            if not capabilities.intersection(
                self.GOOGLE_SHEETS_EXECUTION_CAPABILITIES
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "Google connection is not authorized for DQ "
                        "write-back. Add EXECUTE_DQ_REMEDIATION or "
                        "WRITE_DATA to its approved capabilities."
                    ),
                )

        record_organization_id = str(
            connection.get("organization_id")
            or ""
        ).strip()

        if record_organization_id != organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Google enterprise connection tenant validation failed."
                ),
            )

    def _build_google_user_credentials(
        self,
        *,
        credentials: dict[str, Any],
    ) -> user_credentials.Credentials:
        values = self._flatten_credentials(
            credentials
        )

        authentication_type = str(
            values.get("authentication_type")
            or ""
        ).strip().upper()

        if authentication_type not in {
            "GOOGLE_OAUTH",
            "OAUTH",
            "OAUTH2",
            "U2M",
            "USER_OAUTH",
        }:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Selected connection is not Google OAuth authorized."
                ),
            )

        access_token = str(
            values.get("access_token")
            or values.get("token")
            or ""
        ).strip()

        if not access_token:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Google OAuth access token is missing. "
                    "Reconnect Google."
                ),
            )

        scopes = [
            str(scope).strip()
            for scope in (
                values.get("scopes")
                or [self.GOOGLE_SHEETS_WRITE_SCOPE]
            )
            if str(scope).strip()
        ]

        google_credentials = user_credentials.Credentials(
            token=access_token,
            refresh_token=values.get("refresh_token"),
            token_uri=(
                values.get("token_url")
                or values.get("token_uri")
                or "https://oauth2.googleapis.com/token"
            ),
            client_id=values.get("client_id"),
            client_secret=values.get("client_secret"),
            scopes=scopes,
        )

        try:
            google_credentials.refresh(
                google_auth_request.Request()
            )
        except Exception as exc:
            logger.exception(
                "Google OAuth credential refresh failed during DQ execution. "
                "error=%s",
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Google authorization expired or could not be refreshed. "
                    "Reconnect Google and try again."
                ),
            ) from exc

        return google_credentials

    def _load_approved_recommendation(
        self,
        *,
        organization_id: str,
        recommendation_id: str,
        profile_run_id: str,
    ) -> dict[str, Any]:
        """
        Uses the repository method already present in routes.py:
        get_approved_ai_recommendations(...).

        This initial implementation filters the returned tenant/profile set
        by recommendation_id server-side. A dedicated repository
        get_approved_ai_recommendation_by_id(...) method can replace this
        later without changing the public execution contract.
        """

        rows = self.quality_repository.get_approved_ai_recommendations(
            organization_id=organization_id,
            profile_run_id=profile_run_id,
            limit=500,
        )

        for row in rows:
            row_recommendation_id = str(
                row.get("recommendation_id") or ""
            ).strip()

            if row_recommendation_id == recommendation_id:
                row_organization_id = str(
                    row.get("organization_id") or ""
                ).strip()

                if row_organization_id != organization_id:
                    logger.error(
                        "Cross-tenant approved remediation blocked. "
                        "authenticated_organization_id=%s "
                        "record_organization_id=%s recommendation_id=%s",
                        organization_id,
                        row_organization_id,
                        recommendation_id,
                    )
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=(
                            "Approved DQ remediation does not belong "
                            "to the authenticated organization."
                        ),
                    )

                return dict(row)

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Approved DQ remediation was not found for the "
                "authenticated organization and profile run."
            ),
        )

    def _load_connection(
        self,
        *,
        organization_id: str,
        connection_id: str,
    ) -> dict[str, Any]:
        connection = self.connection_repository.get_connection(
            connection_id=connection_id,
            organization_id=organization_id,
        )

        if connection is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Enterprise connection not found.",
            )

        record_organization_id = str(
            connection.get("organization_id") or ""
        ).strip()

        if record_organization_id != organization_id:
            logger.error(
                "Cross-tenant execution connection blocked. "
                "authenticated_organization_id=%s "
                "record_organization_id=%s connection_id=%s",
                organization_id,
                record_organization_id,
                connection_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Enterprise connection does not belong to the "
                    "authenticated organization."
                ),
            )

        return dict(connection)

    def _validate_connection_for_execution(
        self,
        *,
        connection: dict[str, Any],
        organization_id: str,
    ) -> None:
        if not connection.get("is_active", False):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Inactive enterprise connections cannot execute DQ remediation.",
            )

        environment = str(
            connection.get("environment") or ""
        ).strip().upper()

        if environment in self.BLOCKED_ENVIRONMENTS:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Production DQ execution is blocked. "
                    "Select a DEV or STG enterprise connection."
                ),
            )

        if environment not in self.ALLOWED_ENVIRONMENTS:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "DQ execution is permitted only for DEV or STG "
                    "enterprise connections."
                ),
            )

        vendor = str(
            connection.get("vendor") or ""
        ).strip().upper()

        if vendor == "GOOGLE_BIGQUERY":
            vendor = "BIGQUERY"

        if vendor not in self.ALLOWED_VENDORS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Initial governed DQ execution supports only "
                    "BigQuery, Databricks, and Snowflake."
                ),
            )

        if self.require_healthy_connection:
            health_status = str(
                connection.get("health_status") or ""
            ).strip().upper()

            if health_status != "HEALTHY":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Enterprise connection must pass its connection "
                        "health test before DQ remediation can execute."
                    ),
                )

        if self.require_execution_capability:
            capabilities = {
                str(value or "").strip().upper()
                for value in (
                    connection.get("connection_capabilities")
                    or []
                )
                if str(value or "").strip()
            }

            if not capabilities.intersection(
                self.EXECUTION_CAPABILITIES
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "Enterprise connection is not authorized for DQ "
                        "execution. Add EXECUTE_DQ_REMEDIATION, EXECUTE_SQL, "
                        "or WRITE_DATA to its approved capabilities."
                    ),
                )

        record_organization_id = str(
            connection.get("organization_id") or ""
        ).strip()

        if record_organization_id != organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Enterprise connection tenant validation failed.",
            )

    # ------------------------------------------------------------------
    # Google Sheets REGEX remediation
    # ------------------------------------------------------------------

    @classmethod
    def build_google_sheets_regex_artifact(
        cls,
        *,
        rule_id: str,
        field_name: str | None,
    ) -> dict[str, Any]:
        """Return the exact executable REGEX contract for Google Sheets."""
        normalized_rule_id = str(rule_id or "").strip().upper()
        normalized_field_name = str(field_name or "").strip() or None

        if normalized_rule_id not in cls.GOOGLE_SHEETS_EXECUTABLE_REGEX_RULES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This DQ rule does not have an approved executable "
                    "Google Sheets REGEX remediation contract."
                ),
            )

        if normalized_rule_id == "PRODUCT_VARIANT_STANDARDIZATION":
            return {
                "artifact_type": "REGEX",
                "execution_target": "GOOGLE_SHEETS",
                "rule_id": normalized_rule_id,
                "field_name": normalized_field_name or "product_variant",
                "pattern": cls.GOOGLE_SHEETS_PRODUCT_VARIANT_REGEX,
                "operation": "NORMALIZE_PRODUCT_VARIANT",
                "replacement_strategy": (
                    "PRESERVE_QUANTITY_SINGLE_SPACE_UPPERCASE_UOM"
                ),
            }
        if normalized_rule_id == "FULL_NAME_STANDARDIZATION":
            return {
                "artifact_type": "REGEX",
                "execution_target": "GOOGLE_SHEETS",
                "rule_id": normalized_rule_id,
                "field_name": normalized_field_name or "full_name",
                "pattern": cls.FULL_NAME_STANDARDIZATION_REGEX,
                "operation": "NORMALIZE_FULL_NAME",
                "replacement_strategy": (
                    "TRIM_COLLAPSE_WHITESPACE_UPPERCASE"
                ),
        }

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Google Sheets REGEX remediation implementation "
                "is not available for this DQ rule."
            ),
        )

    @classmethod
    def validate_google_sheets_regex_artifact(
        cls,
        *,
        rule_id: str,
        field_name: str | None,
        remediation_regex: str,
    ) -> dict[str, Any]:
        """Require the persisted REGEX to exactly match the governed pattern."""
        expected = cls.build_google_sheets_regex_artifact(
            rule_id=rule_id,
            field_name=field_name,
        )
        normalized_regex = str(remediation_regex or "").strip()

        if not normalized_regex:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Approved Google Sheets REGEX remediation is missing.",
            )
        if len(normalized_regex) > 500:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Approved Google Sheets REGEX remediation exceeds the "
                    "maximum supported length."
                ),
            )
        try:
            re.compile(normalized_regex)
        except re.error as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Approved Google Sheets REGEX remediation is invalid.",
            ) from exc

        if normalized_regex != str(expected["pattern"]):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Approved Google Sheets REGEX does not match the governed "
                    "executable pattern for this rule."
                ),
            )

        artifact_hash = hashlib.sha256(
            normalized_regex.encode("utf-8")
        ).hexdigest()
        return {**expected, "pattern": normalized_regex, "artifact_hash": artifact_hash}

    @classmethod
    def apply_google_sheets_regex_value(
        cls,
        *,
        rule_id: str,
        remediation_regex: str,
        value: Any,
    ) -> dict[str, Any]:
        """Apply one governed transformation in memory; perform no network write."""
        artifact = cls.validate_google_sheets_regex_artifact(
            rule_id=rule_id,
            field_name=None,
            remediation_regex=remediation_regex,
        )
        original = "" if value is None else str(value)
        if not original.strip():
            return {
                "matched": False,
                "changed": False,
                "original_value": original,
                "transformed_value": original,
                "reason": "BLANK_SKIPPED",
            }

        match = re.fullmatch(str(artifact["pattern"]), original)
        if match is None:
            return {
                "matched": False,
                "changed": False,
                "original_value": original,
                "transformed_value": original,
                "reason": "PATTERN_NOT_MATCHED",
            }

        normalized_rule_id = str(rule_id or "").strip().upper()
        if normalized_rule_id == "PRODUCT_VARIANT_STANDARDIZATION":
            transformed = f"{match.group(1)} {match.group(2).upper()}"
            return {
                "matched": True,
                "changed": transformed != original,
                "original_value": original,
                "transformed_value": transformed,
                "reason": (
                    "NORMALIZED"
                    if transformed != original
                    else "ALREADY_CANONICAL"
                ),
            }

        if normalized_rule_id == "FULL_NAME_STANDARDIZATION":
            transformed = " ".join(
                original.strip().split()
            ).upper()

            return {
                "matched": True,
                "changed": transformed != original,
                "original_value": original,
                "transformed_value": transformed,
                "reason": (
                    "NORMALIZED"
                    if transformed != original
                    else "ALREADY_CANONICAL"
                ),
            }

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Google Sheets REGEX transformation is not implemented "
                "for this DQ rule."
            ),
        )


    @classmethod
    def build_onedrive_regex_artifact(
        cls,
        *,
        rule_id: str,
        field_name: str | None,
    ) -> dict[str, Any]:
        normalized_rule_id = str(rule_id or "").strip().upper()
        if normalized_rule_id not in cls.ONEDRIVE_EXECUTABLE_REGEX_RULES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This DQ rule does not have an approved executable "
                    "OneDrive Excel REGEX remediation contract."
                ),
            )
        artifact = cls.build_google_sheets_regex_artifact(
            rule_id=normalized_rule_id,
            field_name=field_name,
        )
        return {
            **artifact,
            "execution_target": "ONEDRIVE_EXCEL",
        }

    @classmethod
    def validate_onedrive_regex_artifact(
        cls,
        *,
        rule_id: str,
        field_name: str | None,
        remediation_regex: str,
    ) -> dict[str, Any]:
        expected = cls.build_onedrive_regex_artifact(
            rule_id=rule_id,
            field_name=field_name,
        )
        normalized_regex = str(remediation_regex or "").strip()
        if not normalized_regex:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Approved OneDrive Excel REGEX remediation is missing.",
            )
        if len(normalized_regex) > 500:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Approved OneDrive Excel REGEX remediation exceeds "
                    "the maximum supported length."
                ),
            )
        try:
            re.compile(normalized_regex)
        except re.error as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Approved OneDrive Excel REGEX remediation is invalid.",
            ) from exc
        if normalized_regex != str(expected["pattern"]):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Approved OneDrive Excel REGEX does not match the "
                    "governed executable pattern for this rule."
                ),
            )
        artifact_hash = hashlib.sha256(
            normalized_regex.encode("utf-8")
        ).hexdigest()
        return {
            **expected,
            "pattern": normalized_regex,
            "artifact_hash": artifact_hash,
        }

    @classmethod
    def apply_onedrive_regex_value(
        cls,
        *,
        rule_id: str,
        remediation_regex: str,
        value: Any,
    ) -> dict[str, Any]:
        cls.validate_onedrive_regex_artifact(
            rule_id=rule_id,
            field_name=None,
            remediation_regex=remediation_regex,
        )
        # Transformation semantics are deliberately identical for this
        # allow-listed rule; only the execution target differs.
        return cls.apply_google_sheets_regex_value(
            rule_id=rule_id,
            remediation_regex=remediation_regex,
            value=value,
        )

    # ------------------------------------------------------------------
    # SQL safety
    # ------------------------------------------------------------------

    def _validate_sql_dialect(
        self,
        *,
        vendor: str,
        sql_dialect: str,
    ) -> None:
        allowed_dialects = {
            "BIGQUERY": {"BIGQUERY", "GENERIC"},
            "SNOWFLAKE": {"SNOWFLAKE", "GENERIC"},
            "DATABRICKS": {"DATABRICKS", "GENERIC"},
        }

        if sql_dialect not in allowed_dialects.get(
            vendor,
            set(),
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Approved SQL dialect {sql_dialect or 'MISSING'} "
                    f"is not compatible with {vendor}."
                ),
            )

    def _validate_and_normalize_sql(
        self,
        sql: str,
    ) -> str:
        normalized = str(sql or "").strip()

        if not normalized:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Remediation SQL is required.",
            )

        # Remove one harmless trailing terminator.
        if normalized.endswith(";"):
            normalized = normalized[:-1].rstrip()

        # Multiple statements are never allowed in governed execution.
        if ";" in normalized:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Multiple SQL statements are blocked for governed "
                    "DQ remediation execution."
                ),
            )

        # Strip leading SQL comments only for first-keyword inspection.
        inspect_sql = re.sub(
            r"^\s*(?:--[^\n]*\n\s*|/\*.*?\*/\s*)*",
            "",
            normalized,
            flags=re.DOTALL,
        )

        first_match = re.match(
            r"^([A-Za-z]+)\b",
            inspect_sql,
        )

        first_keyword = (
            first_match.group(1).upper()
            if first_match
            else ""
        )

        if first_keyword not in self.ALLOWED_SQL_VERBS:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Initial governed execution permits only a single "
                    "approved UPDATE statement."
                ),
            )

        uppercase_sql = inspect_sql.upper()

        for keyword in self.BLOCKED_SQL_KEYWORDS:
            if re.search(
                rf"\b{re.escape(keyword)}\b",
                uppercase_sql,
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"SQL keyword {keyword} is blocked by the "
                        "DQ execution safety policy."
                    ),
                )

        if not re.search(
            r"\bWHERE\b",
            uppercase_sql,
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "UPDATE remediation SQL must contain a WHERE clause."
                ),
            )

        return normalized

    # ------------------------------------------------------------------
    # Snowflake execution
    # ------------------------------------------------------------------

    def _execute_snowflake(
        self,
        *,
        connection: dict[str, Any],
        credentials: dict[str, Any],
        sql: str,
    ) -> dict[str, Any]:
        details = self._connection_details(
            connection
        )
        credential_values = self._flatten_credentials(
            credentials
        )

        username = credential_values.get("username")
        password = credential_values.get("password")
        account = (
            credential_values.get("account")
            or details.get("account")
            or connection.get("workspace_name")
        )

        if not username or not password or not account:
            raise RuntimeError(
                "Snowflake username/password/account configuration is incomplete."
            )

        snowflake_connection = None

        try:
            snowflake_connection = snowflake.connector.connect(
                user=username,
                password=password,
                account=account,
                warehouse=(
                    credential_values.get("warehouse")
                    or details.get("warehouse")
                ),
                database=(
                    credential_values.get("database")
                    or details.get("database")
                ),
                schema=(
                    credential_values.get("schema")
                    or details.get("schema")
                    or details.get("schema_name")
                ),
                role=(
                    credential_values.get("role")
                    or details.get("role")
                ),
                login_timeout=20,
                autocommit=False,
            )

            cursor = snowflake_connection.cursor()

            try:
                cursor.execute(sql)
                rows_affected = cursor.rowcount
                snowflake_connection.commit()

                return {
                    "vendor_status": "SUCCEEDED",
                    "rows_affected": (
                        int(rows_affected)
                        if rows_affected is not None
                        and rows_affected >= 0
                        else None
                    ),
                    "statement_id": getattr(
                        cursor,
                        "sfqid",
                        None,
                    ),
                }

            except Exception:
                try:
                    snowflake_connection.rollback()
                except Exception:
                    logger.exception(
                        "Snowflake rollback failed after DQ execution error."
                    )
                raise

            finally:
                cursor.close()

        finally:
            if snowflake_connection is not None:
                snowflake_connection.close()

    # ------------------------------------------------------------------
    # BigQuery execution
    # ------------------------------------------------------------------

    def _build_bigquery_client(
        self,
        *,
        connection: dict[str, Any],
        credentials: dict[str, Any],
    ) -> bigquery.Client:
        details = self._connection_details(connection)
        values = self._flatten_credentials(credentials)
        raw_sa = (
            values.get("service_account_json")
            or values.get("service_account")
            or values.get("credentials_json")
        )
        info: dict[str, Any] | None = None
        if isinstance(raw_sa, dict):
            info = dict(raw_sa)
        elif isinstance(raw_sa, str) and raw_sa.strip():
            try:
                parsed = json.loads(raw_sa)
            except json.JSONDecodeError as exc:
                raise RuntimeError("BigQuery service account JSON is invalid.") from exc
            if not isinstance(parsed, dict):
                raise RuntimeError("BigQuery service account JSON must decode to an object.")
            info = parsed
        elif values.get("type") == "service_account" and values.get("client_email") and values.get("private_key"):
            info = dict(values)

        project_id = str(
            (info or {}).get("project_id") or values.get("project_id")
            or details.get("project_id") or details.get("project")
            or connection.get("workspace_name") or ""
        ).strip()
        if not project_id:
            raise RuntimeError("BigQuery project_id is required for governed DQ execution.")

        if info is not None:
            auth = service_account.Credentials.from_service_account_info(
                info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            return bigquery.Client(project=project_id, credentials=auth)

        access_token = str(values.get("access_token") or "").strip()
        refresh_token = str(values.get("refresh_token") or "").strip() or None
        if not access_token and not refresh_token:
            raise RuntimeError(
                "BigQuery governed execution requires explicit service-account or OAuth credentials."
            )
        auth = user_credentials.Credentials(
            token=access_token or None,
            refresh_token=refresh_token,
            token_uri=str(values.get("token_url") or "https://oauth2.googleapis.com/token"),
            client_id=values.get("client_id"),
            client_secret=values.get("client_secret"),
            scopes=values.get("scopes") or ["https://www.googleapis.com/auth/cloud-platform"],
        )
        if not auth.valid and auth.refresh_token:
            auth.refresh(google_auth_request.Request())
        return bigquery.Client(project=project_id, credentials=auth)

    @staticmethod
    def _validate_bigquery_update_target(*, sql: str, expected_target: str) -> None:
        match = re.match(
            r"^\s*UPDATE\s+(?:`([^`]+)`|([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+))(?=\s)",
            sql,
            flags=re.IGNORECASE,
        )
        actual = (match.group(1) or match.group(2)) if match else ""
        if actual != expected_target:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Approved BigQuery SQL target does not match the persisted profile source. "
                    f"Expected {expected_target}."
                ),
            )

    def _dry_run_bigquery(
        self,
        *,
        connection: dict[str, Any],
        credentials: dict[str, Any],
        sql: str,
    ) -> None:
        client = self._build_bigquery_client(
            connection=connection, credentials=credentials
        )
        client.query(
            sql,
            job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False),
        )

    def _execute_bigquery(
        self,
        *,
        connection: dict[str, Any],
        credentials: dict[str, Any],
        sql: str,
    ) -> dict[str, Any]:
        """
        Execute one already-governed UPDATE statement in BigQuery.

        BigQuery DML statements are atomic at the statement level, so there is
        no explicit client-side rollback call. The BigQuery job_id is returned
        as the normalized statement_id for the execution audit log.
        """
        client = self._build_bigquery_client(
            connection=connection, credentials=credentials
        )

        query_job = client.query(sql)
        query_job.result()

        rows_affected = query_job.num_dml_affected_rows

        return {
            "vendor_status": "SUCCEEDED",
            "rows_affected": (
                int(rows_affected)
                if rows_affected is not None
                else None
            ),
            "statement_id": query_job.job_id,
        }

    # ------------------------------------------------------------------
    # Databricks execution
    # ------------------------------------------------------------------

    def _execute_databricks(
        self,
        *,
        organization_id: str,
        connection_id: str,
        connection: dict[str, Any],
        credentials: dict[str, Any],
        sql: str,
    ) -> dict[str, Any]:
        details = self._connection_details(
            connection
        )
        credential_values = self._flatten_credentials(
            credentials
        )

        host = str(
            connection.get("api_endpoint")
            or details.get("host")
            or ""
        ).strip().rstrip("/")

        warehouse_id = str(
            details.get("warehouse_id")
            or credential_values.get("warehouse_id")
            or ""
        ).strip()

        if not host:
            raise RuntimeError(
                "Databricks API endpoint is missing."
            )

        if not warehouse_id:
            raise RuntimeError(
                "Databricks SQL warehouse_id is required in connection_details "
                "or credential additional_properties for governed SQL execution."
            )

        authentication_type = str(
            credential_values.get("authentication_type")
            or connection.get("authentication_type")
            or ""
        ).strip().upper()

        if authentication_type == "PAT":
            access_token = str(
                credential_values.get("token")
                or credential_values.get("access_token")
                or ""
            ).strip()

            if not access_token:
                raise RuntimeError(
                    "Databricks PAT is missing."
                )

            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "ADMS_AIDataStewardCopilot/1.0",
            }

        elif authentication_type in {
            "OAUTH_CLIENT",
            "OAUTH_CLIENT_CREDENTIALS",
            "OAUTH2_CLIENT_CREDENTIALS",
        }:
            client_id = str(
                credential_values.get("client_id") or ""
            ).strip()
            client_secret = str(
                credential_values.get("client_secret") or ""
            ).strip()

            connector = DatabricksConnector(
                organization_id=organization_id,
                connection_id=connection_id,
                host=host,
                client_id=client_id,
                client_secret=client_secret,
                allow_environment_fallback=False,
            )

            # Existing connector owns OAuth token acquisition and telemetry.
            # _headers() is currently private; when the connector gains a
            # public request()/authorization_headers() method, use it here.
            headers = connector._headers()

        else:
            raise RuntimeError(
                "Unsupported Databricks authentication type for DQ execution."
            )

        payload: dict[str, Any] = {
            "warehouse_id": warehouse_id,
            "statement": sql,
            "wait_timeout": "30s",
            "on_wait_timeout": "CONTINUE",
        }

        catalog = str(
            details.get("catalog") or ""
        ).strip()
        schema_name = str(
            details.get("schema")
            or details.get("schema_name")
            or ""
        ).strip()

        if catalog:
            payload["catalog"] = catalog

        if schema_name:
            payload["schema"] = schema_name
        logger.warning(
            "DATABRICKS EXECUTION DEBUG "
            "connection_id=%s "
            "warehouse_id=%s "
            "statement=%r",
            connection_id,
            warehouse_id,
            sql,
        )

        response = requests.post(
            f"{host}/api/2.0/sql/statements",
            headers=headers,
            json=payload,
            timeout=35,
        )

        if not response.ok:
            raise RuntimeError(
                "Databricks Statement Execution API returned "
                f"HTTP {response.status_code}."
            )

        body = response.json()
        statement_id = str(
            body.get("statement_id") or ""
        ).strip()

        if not statement_id:
            raise RuntimeError(
                "Databricks execution response did not include statement_id."
            )

        body = self._poll_databricks_statement(
            host=host,
            headers=headers,
            statement_id=statement_id,
            initial_body=body,
        )
        logger.warning(
            "DATABRICKS FINAL RESPONSE DEBUG "
            "statement_id=%s body=%s",
            statement_id,
            body,
        )

        state = str(
            (body.get("status") or {}).get("state")
            or ""
        ).strip().upper()

        if state != "SUCCEEDED":
            error = (
                (body.get("status") or {}).get("error")
                or {}
            )

            error_message = str(
                error.get("message")
                or "Databricks SQL statement did not succeed."
            )

            raise RuntimeError(
                error_message
            )

        rows_affected = self._extract_databricks_rows_affected(
            body
        )

        return {
            "vendor_status": state,
            "rows_affected": rows_affected,
            "statement_id": statement_id,
        }

    def _poll_databricks_statement(
        self,
        *,
        host: str,
        headers: dict[str, str],
        statement_id: str,
        initial_body: dict[str, Any],
    ) -> dict[str, Any]:
        body = initial_body
        deadline = (
            time.monotonic()
            + self.databricks_poll_timeout_seconds
        )

        while True:
            state = str(
                (body.get("status") or {}).get("state")
                or ""
            ).strip().upper()

            if state in self.TERMINAL_DATABRICKS_STATES:
                return body

            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Timed out waiting for Databricks SQL statement completion."
                )

            time.sleep(
                self.databricks_poll_interval_seconds
            )

            response = requests.get(
                f"{host}/api/2.0/sql/statements/{statement_id}",
                headers=headers,
                timeout=20,
            )

            if not response.ok:
                raise RuntimeError(
                    "Databricks statement status request returned "
                    f"HTTP {response.status_code}."
                )

            body = response.json()

    @staticmethod
    def _extract_databricks_rows_affected(
        body: dict[str, Any],
    ) -> int | None:
        """
        Return an affected-row count only when Databricks explicitly
        provides one.

        Supports both direct affected_rows fields and Statement Execution
        API result sets where num_affected_rows is returned as a column.
        """

        # Direct response shapes.
        candidates = [
            body.get("affected_rows"),
            (body.get("result") or {}).get("affected_rows"),
            (body.get("manifest") or {}).get("affected_rows"),
        ]

        for value in candidates:
            if value is None:
                continue

            try:
                return int(value)
            except (TypeError, ValueError):
                continue

        # Databricks Statement Execution API DML result shape:
        #
        # manifest.schema.columns = [{"name": "num_affected_rows", ...}]
        # result.data_array = [["5"]]
        manifest = body.get("manifest") or {}
        schema = manifest.get("schema") or {}
        columns = schema.get("columns") or []

        result = body.get("result") or {}
        data_array = result.get("data_array") or []

        affected_column_index: int | None = None

        for index, column in enumerate(columns):
            if not isinstance(column, dict):
                continue

            column_name = str(
                column.get("name") or ""
            ).strip().lower()

            if column_name in {
                "num_affected_rows",
                "affected_rows",
            }:
                affected_column_index = index
                break

        if (
            affected_column_index is not None
            and data_array
            and isinstance(data_array[0], (list, tuple))
            and len(data_array[0]) > affected_column_index
        ):
            value = data_array[0][affected_column_index]

            try:
                return int(value)
            except (TypeError, ValueError):
                pass

        return None
    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _connection_details(
        connection: dict[str, Any],
    ) -> dict[str, Any]:
        details = connection.get(
            "connection_details"
        ) or {}

        if isinstance(details, str):
            try:
                details = json.loads(details)
            except json.JSONDecodeError:
                details = {}

        if not isinstance(details, dict):
            return {}

        return dict(details)

    @staticmethod
    def _flatten_credentials(
        credentials: dict[str, Any],
    ) -> dict[str, Any]:
        """
        ConnectionCredentialInput stores vendor-specific extras inside
        additional_properties. Merge them for execution-time resolution
        without mutating or logging the original secret payload.
        """

        flattened = dict(credentials)

        additional = flattened.get(
            "additional_properties"
        )

        if isinstance(additional, dict):
            for key, value in additional.items():
                flattened.setdefault(
                    key,
                    value,
                )

        return flattened

    @staticmethod
    def _require_organization_id(
        organization_id: str,
    ) -> str:
        normalized = str(
            organization_id or ""
        ).strip()

        if not normalized:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Authenticated organization context is required.",
            )

        if not normalized.startswith("org_"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Authenticated organization context is invalid.",
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
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="connection_id is required.",
            )

        is_conn_id = normalized.startswith(
            "conn_"
        )

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
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "connection_id must use either the conn_ identifier "
                    "standard or a valid UUID."
                ),
            )

        return normalized

    @staticmethod
    def _require_value(
        value: str,
        *,
        field_name: str,
    ) -> str:
        normalized = str(
            value or ""
        ).strip()

        if not normalized:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{field_name} is required.",
            )

        return normalized

    @staticmethod
    def _require_actor(
        actor: str,
        *,
        field_name: str,
    ) -> str:
        normalized = str(
            actor or ""
        ).strip().lower()

        if not normalized:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{field_name} is required.",
            )

        return normalized

    @staticmethod
    def _build_result(
        *,
        context: dict[str, Any],
        execution_id: str,
        execution_status: str,
        vendor_status: str,
        rows_affected: int | None,
        statement_id: str | None,
        executed_at: datetime | None,
        error_message: str | None,
        started_at: datetime | None = None,
        validated_at: datetime | None = None,
        rows_evaluated: int | None = None,
        rows_changed: int | None = None,
        rows_skipped: int | None = None,
    ) -> dict[str, Any]:
        now = validated_at or datetime.now(timezone.utc)

        return {
            "execution_id": execution_id,
            "organization_id": context["organization_id"],
            "recommendation_id": context["recommendation_id"],
            "profile_run_id": context["profile_run_id"],
            "remediation_version_id": context.get(
                "remediation_version_id"
            ),
            "connection_id": context["connection_id"],
            "vendor": context["vendor"],
            "environment": context["environment"],
            "domain": context.get("domain"),
            "artifact_type": context["artifact_type"],
            "artifact_hash": context["artifact_hash"],
            "approval_status": context["approval_status"],
            "safety_status": context["safety_status"],
            "sql_dialect": context["sql_dialect"],
            "requested_by": context["requested_by"],
            "technical_approved_by": context[
                "technical_approved_by"
            ],
            "execution_status": execution_status,
            "vendor_status": vendor_status,
            "rows_affected": rows_affected,
            "statement_id": statement_id,
            "started_at": (
                started_at.isoformat()
                if started_at
                else None
            ),
            "executed_at": (
                executed_at.isoformat()
                if executed_at
                else None
            ),
            "validated_at": now.isoformat(),
            "error_message": error_message,
        }
