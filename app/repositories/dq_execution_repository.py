from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from google.cloud import bigquery


logger = logging.getLogger(__name__)


class DqExecutionRepository:
    """
    Tenant-isolated persistence for governed DQ execution audit events.

    Lifecycle:
        create_execution(...) -> RUNNING
        mark_execution_succeeded(...) -> SUCCEEDED
        mark_execution_failed(...) -> FAILED

    Security:
        * organization_id is mandatory for every read/write.
        * every UPDATE is scoped by execution_id AND organization_id.
        * no credentials, remediation SQL, or spreadsheet cell values are
          persisted here.
        * artifact_hash identifies the exact approved artifact executed.
        * execution_target / rule_id distinguish SQL execution from governed
          Google Sheets REGEX remediation without storing source data.
    """

    VALID_STATUSES = {
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
        "VALIDATED_NOT_EXECUTED",
    }

    def __init__(
        self,
        *,
        client: bigquery.Client | None = None,
        project_id: str = "api-project-503305938314",
        dataset: str = "ai_data_steward_mvp",
    ) -> None:
        self.client = client or bigquery.Client(project=project_id)
        self.project_id = project_id
        self.dataset = dataset
        self.execution_table_id = (
            f"{project_id}.{dataset}.DQ_EXECUTION_LOG"
        )

    @staticmethod
    def _require_organization_id(
        organization_id: str,
    ) -> str:
        normalized = str(organization_id or "").strip()

        if not normalized:
            raise ValueError(
                "organization_id is required for "
                "tenant-isolated DQ execution persistence."
            )

        if not normalized.startswith("org_"):
            raise ValueError(
                "organization_id must use the org_ identifier standard."
            )

        return normalized

    @staticmethod
    def _require_execution_id(
        execution_id: str,
    ) -> str:
        normalized = str(execution_id or "").strip()

        if not normalized:
            raise ValueError("execution_id is required.")

        if not normalized.startswith("exec_"):
            raise ValueError(
                "execution_id must use the exec_ identifier standard."
            )

        return normalized

    @staticmethod
    def _require_value(
        value: Any,
        *,
        field_name: str,
    ) -> str:
        normalized = str(value or "").strip()

        if not normalized:
            raise ValueError(f"{field_name} is required.")

        return normalized

    @classmethod
    def _require_status(
        cls,
        execution_status: str,
    ) -> str:
        normalized = str(execution_status or "").strip().upper()

        if normalized not in cls.VALID_STATUSES:
            raise ValueError(
                "execution_status must be one of: "
                + ", ".join(sorted(cls.VALID_STATUSES))
            )

        return normalized

    @staticmethod
    def _normalize_optional_string(
        value: Any,
    ) -> str | None:
        if value is None:
            return None

        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _normalize_actor(
        value: Any,
        *,
        field_name: str,
    ) -> str:
        normalized = str(value or "").strip().lower()

        if not normalized:
            raise ValueError(f"{field_name} is required.")

        return normalized

    def create_execution(
        self,
        *,
        execution_id: str,
        organization_id: str,
        recommendation_id: str,
        profile_run_id: str,
        remediation_version_id: str | None,
        connection_id: str,
        vendor: str,
        environment: str,
        domain: str | None,
        artifact_type: str,
        artifact_hash: str,
        sql_dialect: str | None,
        approval_status: str,
        safety_status: str,
        requested_by: str,
        technical_approved_by: str,
        execution_target: str | None = None,
        rule_id: str | None = None,
        rows_evaluated: int | None = None,
        rows_changed: int | None = None,
        rows_skipped: int | None = None,
        execution_status: str = "RUNNING",
        vendor_status: str = "NOT_STARTED",
        started_at: datetime | None = None,
        validated_at: datetime | None = None,
    ) -> dict[str, Any]:
        effective_execution_id = self._require_execution_id(execution_id)
        effective_organization_id = self._require_organization_id(
            organization_id
        )
        effective_status = self._require_status(execution_status)
        effective_started_at = started_at or datetime.now(timezone.utc)
        effective_validated_at = validated_at
        effective_artifact_type = self._require_value(
            artifact_type,
            field_name="artifact_type",
        ).upper()

        effective_sql_dialect = (
            self._normalize_optional_string(sql_dialect).upper()
            if self._normalize_optional_string(sql_dialect)
            else None
        )

        if effective_artifact_type == "SQL":
            if not effective_sql_dialect:
                raise ValueError(
                    "sql_dialect is required for SQL execution."
                )

        elif effective_artifact_type == "REGEX":
            if effective_sql_dialect is not None:
                raise ValueError(
                    "sql_dialect must be null for REGEX execution."
                )

        row = {
            "execution_id": effective_execution_id,
            "organization_id": effective_organization_id,
            "recommendation_id": self._require_value(
                recommendation_id,
                field_name="recommendation_id",
            ),
            "profile_run_id": self._require_value(
                profile_run_id,
                field_name="profile_run_id",
            ),
            "remediation_version_id": self._normalize_optional_string(
                remediation_version_id
            ),
            "connection_id": self._require_value(
                connection_id,
                field_name="connection_id",
            ),
            "vendor": self._require_value(
                vendor,
                field_name="vendor",
            ).upper(),
            "environment": self._require_value(
                environment,
                field_name="environment",
            ).upper(),
            "domain": self._normalize_optional_string(domain),

            "artifact_type": effective_artifact_type,
            "artifact_hash": self._require_value(
                artifact_hash,
                field_name="artifact_hash",
            ).lower(),
            "sql_dialect": effective_sql_dialect,
            "execution_target": (
                self._normalize_optional_string(execution_target).upper()
                if self._normalize_optional_string(execution_target)
                else None
            ),
            "rule_id": (
                self._normalize_optional_string(rule_id).upper()
                if self._normalize_optional_string(rule_id)
                else None
            ),
            "approval_status": self._require_value(
                approval_status,
                field_name="approval_status",
            ).upper(),
            "safety_status": self._require_value(
                safety_status,
                field_name="safety_status",
            ).upper(),
            "requested_by": self._normalize_actor(
                requested_by,
                field_name="requested_by",
            ),
            "technical_approved_by": self._normalize_actor(
                technical_approved_by,
                field_name="technical_approved_by",
            ),
            "execution_status": effective_status,
            "vendor_status": self._require_value(
                vendor_status,
                field_name="vendor_status",
            ).upper(),
            "rows_affected": None,
            "rows_evaluated": rows_evaluated,
            "rows_changed": rows_changed,
            "rows_skipped": rows_skipped,
            "statement_id": None,
            "error_code": None,
            "error_message": None,
            "started_at": effective_started_at.isoformat(),
            "executed_at": None,
            "validated_at": (
                effective_validated_at.isoformat()
                if effective_validated_at is not None
                else None
            ),
            "created_at": effective_started_at.isoformat(),
            "updated_at": effective_started_at.isoformat(),
        }

        insert_sql = f"""
        INSERT INTO `{self.execution_table_id}`
        (
            execution_id,
            organization_id,
            recommendation_id,
            profile_run_id,
            remediation_version_id,
            connection_id,
            vendor,
            environment,
            domain,
            artifact_type,
            artifact_hash,
            sql_dialect,
            execution_target,
            rule_id,
            approval_status,
            safety_status,
            requested_by,
            technical_approved_by,
            execution_status,
            vendor_status,
            rows_affected,
            rows_evaluated,
            rows_changed,
            rows_skipped,
            statement_id,
            error_code,
            error_message,
            started_at,
            executed_at,
            validated_at,
            created_at,
            updated_at
        )
        VALUES
        (
            @execution_id,
            @organization_id,
            @recommendation_id,
            @profile_run_id,
            @remediation_version_id,
            @connection_id,
            @vendor,
            @environment,
            @domain,
            @artifact_type,
            @artifact_hash,
            @sql_dialect,
            @execution_target,
            @rule_id,
            @approval_status,
            @safety_status,
            @requested_by,
            @technical_approved_by,
            @execution_status,
            @vendor_status,
            NULL,
            @rows_evaluated,
            @rows_changed,
            @rows_skipped,
            NULL,
            NULL,
            NULL,
            @started_at,
            NULL,
            @validated_at,
            @created_at,
            @updated_at
        )
        """

        insert_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("execution_id", "STRING", effective_execution_id),
                bigquery.ScalarQueryParameter("organization_id", "STRING", effective_organization_id),
                bigquery.ScalarQueryParameter("recommendation_id", "STRING", row["recommendation_id"]),
                bigquery.ScalarQueryParameter("profile_run_id", "STRING", row["profile_run_id"]),
                bigquery.ScalarQueryParameter("remediation_version_id", "STRING", row["remediation_version_id"]),
                bigquery.ScalarQueryParameter("connection_id", "STRING", row["connection_id"]),
                bigquery.ScalarQueryParameter("vendor", "STRING", row["vendor"]),
                bigquery.ScalarQueryParameter("environment", "STRING", row["environment"]),
                bigquery.ScalarQueryParameter("domain", "STRING", row["domain"]),
                bigquery.ScalarQueryParameter("artifact_type", "STRING", row["artifact_type"]),
                bigquery.ScalarQueryParameter("artifact_hash", "STRING", row["artifact_hash"]),
                bigquery.ScalarQueryParameter("sql_dialect", "STRING", row["sql_dialect"]),
                bigquery.ScalarQueryParameter("execution_target", "STRING", row["execution_target"]),
                bigquery.ScalarQueryParameter("rule_id", "STRING", row["rule_id"]),
                bigquery.ScalarQueryParameter("approval_status", "STRING", row["approval_status"]),
                bigquery.ScalarQueryParameter("safety_status", "STRING", row["safety_status"]),
                bigquery.ScalarQueryParameter("requested_by", "STRING", row["requested_by"]),
                bigquery.ScalarQueryParameter("technical_approved_by", "STRING", row["technical_approved_by"]),
                bigquery.ScalarQueryParameter("execution_status", "STRING", row["execution_status"]),
                bigquery.ScalarQueryParameter("vendor_status", "STRING", row["vendor_status"]),
                bigquery.ScalarQueryParameter("rows_evaluated", "INT64", row["rows_evaluated"]),
                bigquery.ScalarQueryParameter("rows_changed", "INT64", row["rows_changed"]),
                bigquery.ScalarQueryParameter("rows_skipped", "INT64", row["rows_skipped"]),
                bigquery.ScalarQueryParameter("started_at", "TIMESTAMP", effective_started_at),
                bigquery.ScalarQueryParameter("validated_at", "TIMESTAMP", effective_validated_at),
                bigquery.ScalarQueryParameter("created_at", "TIMESTAMP", effective_started_at),
                bigquery.ScalarQueryParameter("updated_at", "TIMESTAMP", effective_started_at),
            ]
        )

        try:
            self.client.query(
                insert_sql,
                job_config=insert_config,
            ).result()
        except Exception:
            logger.exception(
                "DQ execution audit insert failed. "
                "execution_id=%s organization_id=%s",
                effective_execution_id,
                effective_organization_id,
            )
            raise RuntimeError(
                "Unable to create DQ execution audit record."
            )

        logger.info(
            "DQ execution audit record created. "
            "execution_id=%s organization_id=%s status=%s",
            effective_execution_id,
            effective_organization_id,
            effective_status,
        )

        return row

    def create_governed_regex_execution(
        self,
        *,
        execution_id: str,
        organization_id: str,
        recommendation_id: str,
        profile_run_id: str,
        remediation_version_id: str | None,
        connection_id: str,
        vendor: str,
        environment: str,
        domain: str | None,
        rule_id: str,
        artifact_hash: str,
        approval_status: str,
        safety_status: str,
        requested_by: str,
        technical_approved_by: str,
        execution_target: str,
        rows_evaluated: int | None = None,
        rows_changed: int | None = None,
        rows_skipped: int | None = None,
        started_at: datetime | None = None,
    ) -> dict[str, Any]:
        """
        Create a RUNNING audit record for governed REGEX remediation.

        The execution target and vendor identify the governed source.
        Source values and REGEX text are not persisted here.
        The exact approved artifact is represented by artifact_hash.
        """

        return self.create_execution(
            execution_id=execution_id,
            organization_id=organization_id,
            recommendation_id=recommendation_id,
            profile_run_id=profile_run_id,
            remediation_version_id=remediation_version_id,
            connection_id=connection_id,
            vendor=vendor,
            environment=environment,
            domain=domain,
            artifact_type="REGEX",
            artifact_hash=artifact_hash,
            sql_dialect=None,
            approval_status=approval_status,
            safety_status=safety_status,
            requested_by=requested_by,
            technical_approved_by=technical_approved_by,
            execution_target=execution_target,
            rule_id=rule_id,
            rows_evaluated=rows_evaluated,
            rows_changed=rows_changed,
            rows_skipped=rows_skipped,
            execution_status="RUNNING",
            vendor_status="NOT_STARTED",
            started_at=started_at,
        )

    def mark_execution_succeeded(
        self,
        *,
        execution_id: str,
        organization_id: str,
        vendor_status: str,
        rows_affected: int | None,
        statement_id: str | None,
        rows_evaluated: int | None = None,
        rows_changed: int | None = None,
        rows_skipped: int | None = None,
        executed_at: datetime | None = None,
    ) -> None:
        effective_execution_id = self._require_execution_id(execution_id)
        effective_organization_id = self._require_organization_id(
            organization_id
        )
        effective_executed_at = executed_at or datetime.now(timezone.utc)

        sql = f"""
        UPDATE `{self.execution_table_id}`
        SET
            execution_status = 'SUCCEEDED',
            vendor_status = @vendor_status,
            rows_affected = @rows_affected,
            rows_evaluated = COALESCE(@rows_evaluated, rows_evaluated),
            rows_changed = COALESCE(@rows_changed, rows_changed),
            rows_skipped = COALESCE(@rows_skipped, rows_skipped),
            statement_id = @statement_id,
            error_code = NULL,
            error_message = NULL,
            executed_at = @executed_at,
            updated_at = @updated_at
        WHERE execution_id = @execution_id
          AND organization_id = @organization_id
          AND execution_status = 'RUNNING'
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "execution_id",
                    "STRING",
                    effective_execution_id,
                ),
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "vendor_status",
                    "STRING",
                    self._require_value(
                        vendor_status,
                        field_name="vendor_status",
                    ).upper(),
                ),
                bigquery.ScalarQueryParameter(
                    "rows_affected",
                    "INT64",
                    rows_affected,
                ),
                bigquery.ScalarQueryParameter(
                    "rows_evaluated",
                    "INT64",
                    rows_evaluated,
                ),
                bigquery.ScalarQueryParameter(
                    "rows_changed",
                    "INT64",
                    rows_changed,
                ),
                bigquery.ScalarQueryParameter(
                    "rows_skipped",
                    "INT64",
                    rows_skipped,
                ),
                bigquery.ScalarQueryParameter(
                    "statement_id",
                    "STRING",
                    self._normalize_optional_string(statement_id),
                ),
                bigquery.ScalarQueryParameter(
                    "executed_at",
                    "TIMESTAMP",
                    effective_executed_at,
                ),
                bigquery.ScalarQueryParameter(
                    "updated_at",
                    "TIMESTAMP",
                    effective_executed_at,
                ),
            ]
        )

        query_job = self.client.query(sql, job_config=job_config)
        query_job.result()

        if query_job.num_dml_affected_rows != 1:
            raise RuntimeError(
                "DQ execution audit success update did not affect "
                "exactly one RUNNING tenant-owned record."
            )

    def mark_execution_failed(
        self,
        *,
        execution_id: str,
        organization_id: str,
        vendor_status: str = "FAILED",
        error_code: str | None = None,
        error_message: str | None = None,
        executed_at: datetime | None = None,
    ) -> None:
        effective_execution_id = self._require_execution_id(execution_id)
        effective_organization_id = self._require_organization_id(
            organization_id
        )
        effective_executed_at = executed_at or datetime.now(timezone.utc)

        normalized_error_message = self._normalize_optional_string(
            error_message
        )
        if normalized_error_message and len(normalized_error_message) > 2000:
            normalized_error_message = normalized_error_message[:2000]

        normalized_error_code = self._normalize_optional_string(error_code)
        if normalized_error_code and len(normalized_error_code) > 200:
            normalized_error_code = normalized_error_code[:200]

        sql = f"""
        UPDATE `{self.execution_table_id}`
        SET
            execution_status = 'FAILED',
            vendor_status = @vendor_status,
            error_code = @error_code,
            error_message = @error_message,
            executed_at = @executed_at,
            updated_at = @updated_at
        WHERE execution_id = @execution_id
          AND organization_id = @organization_id
          AND execution_status = 'RUNNING'
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "execution_id",
                    "STRING",
                    effective_execution_id,
                ),
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "vendor_status",
                    "STRING",
                    self._require_value(
                        vendor_status,
                        field_name="vendor_status",
                    ).upper(),
                ),
                bigquery.ScalarQueryParameter(
                    "error_code",
                    "STRING",
                    normalized_error_code,
                ),
                bigquery.ScalarQueryParameter(
                    "error_message",
                    "STRING",
                    normalized_error_message,
                ),
                bigquery.ScalarQueryParameter(
                    "executed_at",
                    "TIMESTAMP",
                    effective_executed_at,
                ),
                bigquery.ScalarQueryParameter(
                    "updated_at",
                    "TIMESTAMP",
                    effective_executed_at,
                ),
            ]
        )

        query_job = self.client.query(sql, job_config=job_config)
        query_job.result()

        if query_job.num_dml_affected_rows != 1:
            raise RuntimeError(
                "DQ execution audit failure update did not affect "
                "exactly one RUNNING tenant-owned record."
            )

    def create_validation_record(
        self,
        *,
        execution_id: str,
        organization_id: str,
        recommendation_id: str,
        profile_run_id: str,
        remediation_version_id: str | None,
        connection_id: str,
        vendor: str,
        environment: str,
        domain: str | None,
        artifact_type: str,
        artifact_hash: str,
        sql_dialect: str | None,
        approval_status: str,
        safety_status: str,
        requested_by: str,
        technical_approved_by: str,
        execution_target: str | None = None,
        rule_id: str | None = None,
        rows_evaluated: int | None = None,
        rows_changed: int | None = None,
        rows_skipped: int | None = None,
        validated_at: datetime | None = None,
    ) -> dict[str, Any]:
        effective_validated_at = validated_at or datetime.now(timezone.utc)

        row = self.create_execution(
            execution_id=execution_id,
            organization_id=organization_id,
            recommendation_id=recommendation_id,
            profile_run_id=profile_run_id,
            remediation_version_id=remediation_version_id,
            connection_id=connection_id,
            vendor=vendor,
            environment=environment,
            domain=domain,
            artifact_type=artifact_type,
            artifact_hash=artifact_hash,
            sql_dialect=sql_dialect,
            approval_status=approval_status,
            safety_status=safety_status,
            requested_by=requested_by,
            technical_approved_by=technical_approved_by,
            execution_target=execution_target,
            rule_id=rule_id,
            rows_evaluated=rows_evaluated,
            rows_changed=rows_changed,
            rows_skipped=rows_skipped,
            execution_status="VALIDATED_NOT_EXECUTED",
            vendor_status="NOT_EXECUTED",
            started_at=effective_validated_at,
            validated_at=effective_validated_at,
        )

        return row

    def get_execution(
        self,
        *,
        execution_id: str,
        organization_id: str,
    ) -> dict[str, Any] | None:
        effective_execution_id = self._require_execution_id(execution_id)
        effective_organization_id = self._require_organization_id(
            organization_id
        )

        sql = f"""
        SELECT
            execution_id,
            organization_id,
            recommendation_id,
            profile_run_id,
            remediation_version_id,
            connection_id,
            vendor,
            environment,
            domain,
            artifact_type,
            artifact_hash,
            sql_dialect,
            execution_target,
            rule_id,
            approval_status,
            safety_status,
            requested_by,
            technical_approved_by,
            execution_status,
            vendor_status,
            rows_affected,
            rows_evaluated,
            rows_changed,
            rows_skipped,
            statement_id,
            error_code,
            error_message,
            started_at,
            executed_at,
            validated_at,
            created_at,
            updated_at
        FROM `{self.execution_table_id}`
        WHERE execution_id = @execution_id
          AND organization_id = @organization_id
        LIMIT 1
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "execution_id",
                    "STRING",
                    effective_execution_id,
                ),
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                ),
            ]
        )

        rows = list(
            self.client.query(sql, job_config=job_config).result()
        )

        if not rows:
            return None

        return dict(rows[0])

    def list_executions(
        self,
        *,
        organization_id: str,
        profile_run_id: str | None = None,
        recommendation_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        effective_organization_id = self._require_organization_id(
            organization_id
        )

        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500.")

        conditions = ["organization_id = @organization_id"]
        parameters = [
            bigquery.ScalarQueryParameter(
                "organization_id",
                "STRING",
                effective_organization_id,
            ),
            bigquery.ScalarQueryParameter(
                "limit",
                "INT64",
                limit,
            ),
        ]

        normalized_profile_run_id = self._normalize_optional_string(
            profile_run_id
        )
        normalized_recommendation_id = self._normalize_optional_string(
            recommendation_id
        )

        if normalized_profile_run_id:
            conditions.append("profile_run_id = @profile_run_id")
            parameters.append(
                bigquery.ScalarQueryParameter(
                    "profile_run_id",
                    "STRING",
                    normalized_profile_run_id,
                )
            )

        if normalized_recommendation_id:
            conditions.append(
                "recommendation_id = @recommendation_id"
            )
            parameters.append(
                bigquery.ScalarQueryParameter(
                    "recommendation_id",
                    "STRING",
                    normalized_recommendation_id,
                )
            )

        where_clause = " AND ".join(conditions)

        sql = f"""
        SELECT
            execution_id,
            organization_id,
            recommendation_id,
            profile_run_id,
            remediation_version_id,
            connection_id,
            vendor,
            environment,
            domain,
            artifact_type,
            artifact_hash,
            sql_dialect,
            execution_target,
            rule_id,
            approval_status,
            safety_status,
            requested_by,
            technical_approved_by,
            execution_status,
            vendor_status,
            rows_affected,
            rows_evaluated,
            rows_changed,
            rows_skipped,
            statement_id,
            error_code,
            error_message,
            started_at,
            executed_at,
            validated_at,
            created_at,
            updated_at
        FROM `{self.execution_table_id}`
        WHERE {where_clause}
        ORDER BY created_at DESC
        LIMIT @limit
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=parameters
        )

        rows = self.client.query(
            sql,
            job_config=job_config,
        ).result()

        return [dict(row) for row in rows]
