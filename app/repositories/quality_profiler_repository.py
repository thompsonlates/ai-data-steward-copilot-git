from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from unittest import result

from google.cloud import bigquery

import uuid

from app.services.quality_profiler_service import (
    QualityFinding,
    QualityProfileResult,
    QualityRuleExecution,
    RecordQualityScore,
)


logger = logging.getLogger(__name__)


class QualityProfilerRepository:
    def __init__(
        self,
        *,
        client: bigquery.Client | None = None,
        project_id: str = "api-project-503305938314",
        dataset: str = "ai_data_steward_mvp",
    ) -> None:
        self.client = (
            client
            or bigquery.Client(
                project=project_id
            )
        )

        self.project_id = project_id
        
        self.dataset = dataset

        self.findings_table_id = (
            f"{project_id}.{dataset}.DQ_FINDINGS"
        )

        self.remediation_feedback_table_id = (
            f"{project_id}.{dataset}."
            "DQ_REMEDIATION_FEEDBACK_EVENTS"
        )

        self.record_score_table_id = (
            f"{project_id}.{dataset}.DQ_RECORD_SCORE"
        )

        self.daily_summary_table_id = (
            f"{project_id}.{dataset}.DQ_DAILY_SUMMARY"
        )

        self.ai_recommendations_table_id = (
            f"{project_id}.{dataset}."
            "DQ_AI_RECOMMENDATIONS"
        )

        self.rule_execution_table_id = (
            f"{project_id}.{dataset}.DQ_RULE_EXECUTION"
        )

        self.profile_source_context_table_id = (
            f"{project_id}.{dataset}.DQ_PROFILE_SOURCE_CONTEXT"
        )


        self.profile_stage_prefix = "DQ_PROFILE_STAGE"
        self.profile_stage_expiration_days = 7


    def _ensure_profile_source_context_table(self) -> None:
        create_sql = f"""
        CREATE TABLE IF NOT EXISTS `{self.profile_source_context_table_id}` (
            organization_id STRING NOT NULL,
            profile_run_id STRING NOT NULL,
            source_type STRING NOT NULL,
            connection_id STRING NOT NULL,
            project_id STRING,
            dataset_id STRING,
            table_id STRING,
            spreadsheet_id STRING,
            drive_id STRING,
            item_id STRING,
            sheet_name STRING,
            header_row INT64,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
        PARTITION BY DATE(created_at)
        CLUSTER BY organization_id, profile_run_id
        """
        self.client.query(create_sql).result()

        # CREATE TABLE IF NOT EXISTS does not evolve an existing table.
        # Add new source-neutral target columns and relax the two spreadsheet-
        # only fields when upgrading an installation created by an older build.
        table = self.client.get_table(self.profile_source_context_table_id)
        fields = {field.name: field for field in table.schema}

        for column_name in ("project_id", "dataset_id", "table_id"):
            if column_name not in fields:
                self.client.query(
                    f"ALTER TABLE `{self.profile_source_context_table_id}` "
                    f"ADD COLUMN {column_name} STRING"
                ).result()

        for column_name in ("sheet_name", "header_row"):
            field = fields.get(column_name)
            if field is not None and field.mode == "REQUIRED":
                self.client.query(
                    f"ALTER TABLE `{self.profile_source_context_table_id}` "
                    f"ALTER COLUMN {column_name} DROP NOT NULL"
                ).result()


    def save_profile_source_context(
        self,
        *,
        organization_id: str,
        profile_run_id: str,
        source_type: str,
        connection_id: str,
        sheet_name: str | None = None,
        header_row: int | None = None,
        project_id: str | None = None,
        dataset_id: str | None = None,
        table_id: str | None = None,
        spreadsheet_id: str | None = None,
        drive_id: str | None = None,
        item_id: str | None = None,
    ) -> None:
        """Persist the exact tenant-bound source target used by a profile."""
        organization_id = self._require_organization_id(organization_id)
        profile_run_id = self._require_profile_run_id(profile_run_id)
        normalized_source_type = str(source_type or "").strip().upper()
        normalized_connection_id = str(connection_id or "").strip()
        normalized_sheet_name = str(sheet_name or "").strip()
        safe_header_row = (
            max(1, int(header_row))
            if header_row is not None
            else None
        )

        if normalized_source_type not in {
            "GOOGLE_SHEETS",
            "ONEDRIVE_EXCEL",
            "BIGQUERY",
        }:
            raise ValueError("Unsupported profile source context type.")
        if not normalized_connection_id:
            raise ValueError("connection_id is required.")
        normalized_project_id = str(project_id or "").strip() or None
        normalized_dataset_id = str(dataset_id or "").strip() or None
        normalized_table_id = str(table_id or "").strip() or None

        normalized_spreadsheet_id = str(spreadsheet_id or "").strip() or None
        normalized_drive_id = str(drive_id or "").strip() or None
        normalized_item_id = str(item_id or "").strip() or None

        if normalized_source_type in {
            "GOOGLE_SHEETS",
            "ONEDRIVE_EXCEL",
        }:
            if not normalized_sheet_name:
                raise ValueError("sheet_name is required for spreadsheet sources.")
            if safe_header_row is None:
                safe_header_row = 1

        if normalized_source_type == "GOOGLE_SHEETS" and not normalized_spreadsheet_id:
            raise ValueError("spreadsheet_id is required for Google Sheets.")
        if normalized_source_type == "ONEDRIVE_EXCEL" and (
            not normalized_drive_id or not normalized_item_id
        ):
            raise ValueError("drive_id and item_id are required for OneDrive Excel.")
        if normalized_source_type == "BIGQUERY" and not all(
            [normalized_project_id, normalized_dataset_id, normalized_table_id]
        ):
            raise ValueError(
                "project_id, dataset_id, and table_id are required for BigQuery."
            )

        self._ensure_profile_source_context_table()

        merge_sql = f"""
        MERGE `{self.profile_source_context_table_id}` AS target
        USING (
            SELECT @organization_id AS organization_id,
                   @profile_run_id AS profile_run_id
        ) AS source
        ON target.organization_id = source.organization_id
           AND target.profile_run_id = source.profile_run_id
        WHEN MATCHED THEN UPDATE SET
            source_type = @source_type,
            connection_id = @connection_id,
            project_id = @project_id,
            dataset_id = @dataset_id,
            table_id = @table_id,
            spreadsheet_id = @spreadsheet_id,
            drive_id = @drive_id,
            item_id = @item_id,
            sheet_name = @sheet_name,
            header_row = @header_row,
            updated_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT (
            organization_id, profile_run_id, source_type, connection_id,
            project_id, dataset_id, table_id,
            spreadsheet_id, drive_id, item_id, sheet_name, header_row,
            created_at, updated_at
        ) VALUES (
            @organization_id, @profile_run_id, @source_type, @connection_id,
            @project_id, @dataset_id, @table_id,
            @spreadsheet_id, @drive_id, @item_id, @sheet_name, @header_row,
            CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()
        )
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("organization_id", "STRING", organization_id),
                bigquery.ScalarQueryParameter("profile_run_id", "STRING", profile_run_id),
                bigquery.ScalarQueryParameter("source_type", "STRING", normalized_source_type),
                bigquery.ScalarQueryParameter("connection_id", "STRING", normalized_connection_id),
                bigquery.ScalarQueryParameter("project_id", "STRING", normalized_project_id),
                bigquery.ScalarQueryParameter("dataset_id", "STRING", normalized_dataset_id),
                bigquery.ScalarQueryParameter("table_id", "STRING", normalized_table_id),
                bigquery.ScalarQueryParameter("spreadsheet_id", "STRING", normalized_spreadsheet_id),
                bigquery.ScalarQueryParameter("drive_id", "STRING", normalized_drive_id),
                bigquery.ScalarQueryParameter("item_id", "STRING", normalized_item_id),
                bigquery.ScalarQueryParameter("sheet_name", "STRING", normalized_sheet_name),
                bigquery.ScalarQueryParameter("header_row", "INT64", safe_header_row),
            ]
        )
        self.client.query(merge_sql, job_config=job_config).result()

    def get_profile_source_context(
        self,
        *,
        organization_id: str,
        profile_run_id: str,
    ) -> dict[str, Any] | None:
        """Return the exact tenant-bound source used for a profile run."""
        normalized_org = self._require_organization_id(organization_id)
        normalized_run = self._require_profile_run_id(profile_run_id)
        self._ensure_profile_source_context_table()

        sql = f"""
        SELECT *
        FROM `{self.profile_source_context_table_id}`
        WHERE organization_id = @organization_id
          AND profile_run_id = @profile_run_id
        ORDER BY updated_at DESC
        LIMIT 1
        """
        rows = list(self.client.query(
            sql,
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter(
                    "organization_id", "STRING", normalized_org
                ),
                bigquery.ScalarQueryParameter(
                    "profile_run_id", "STRING", normalized_run
                ),
            ]),
        ).result())
        if not rows:
            return None
        result = dict(rows[0])
        if str(result.get("organization_id") or "").strip() != normalized_org:
            raise RuntimeError("Profile source context crossed tenant boundary.")
        return result


    @staticmethod
    def _require_profile_run_id(
        profile_run_id: str,
    ) -> str:
        normalized = str(profile_run_id or "").strip()

        if not normalized:
            raise ValueError("profile_run_id is required.")

        if not normalized.startswith("dqp_"):
            raise ValueError(
                "profile_run_id must use the dqp_ identifier standard."
            )

        return normalized

    @staticmethod
    def _validate_stage_column_name(
        column_name: str,
    ) -> str:
        normalized = str(column_name or "").strip()

        if not normalized:
            raise ValueError(
                "Profile staging column names cannot be empty."
            )

        if normalized.startswith("_"):
            raise ValueError(
                "Mapped profile fields cannot use reserved "
                "staging metadata names beginning with underscore."
            )

        if not (
            normalized[0].isalpha()
            or normalized[0] == "_"
        ):
            raise ValueError(
                f"Invalid BigQuery staging column name: {normalized}"
            )

        if not all(
            char.isalnum() or char == "_"
            for char in normalized
        ):
            raise ValueError(
                f"Invalid BigQuery staging column name: {normalized}"
            )

        return normalized

    def get_profile_stage_table_id(
        self,
        *,
        organization_id: str,
        profile_run_id: str,
        require_exists: bool = False,
    ) -> str:
        organization_id = self._require_organization_id(
            organization_id
        )
        profile_run_id = self._require_profile_run_id(
            profile_run_id
        )

        safe_org = "".join(
            char
            for char in organization_id
            if char.isalnum() or char == "_"
        )
        safe_profile = "".join(
            char
            for char in profile_run_id
            if char.isalnum() or char == "_"
        )

        table_id = (
            f"{self.project_id}.{self.dataset}."
            f"{self.profile_stage_prefix}_"
            f"{safe_org}_{safe_profile}"
        )

        if require_exists:
            try:
                table = self.client.get_table(table_id)
            except Exception as exc:
                raise ValueError(
                    "No materialized BigQuery profile staging table "
                    "exists for this tenant and profile run."
                ) from exc

            labels = dict(table.labels or {})
            if (
                labels.get("organization_id")
                != organization_id.lower()
            ):
                raise ValueError(
                    "Profile staging table tenant metadata does not "
                    "match the authenticated organization."
                )

        return table_id

    def materialize_profile_rows(
        self,
        *,
        organization_id: str,
        profile_run_id: str,
        rows: Iterable[dict[str, Any]],
        source_type: str,
        source_name: str,
        domain: str,
        expiration_days: int | None = None,
    ) -> str:
        """
        Persist the exact mapped rows used for a DQ profile into a
        tenant/profile-scoped BigQuery staging table.

        The table is a non-production remediation target. All mapped
        business fields are stored as STRING so formatting corrections
        are deterministic and source values are preserved exactly.
        """
        organization_id = self._require_organization_id(
            organization_id
        )
        profile_run_id = self._require_profile_run_id(
            profile_run_id
        )

        normalized_source_type = str(
            source_type or ""
        ).strip().upper()
        normalized_source_name = str(
            source_name or ""
        ).strip()
        normalized_domain = str(
            domain or ""
        ).strip().upper()

        if not normalized_source_type:
            raise ValueError("source_type is required.")
        if not normalized_source_name:
            raise ValueError("source_name is required.")
        if not normalized_domain:
            raise ValueError("domain is required.")

        rows_list = list(rows)

        if not rows_list:
            raise ValueError(
                "Cannot materialize an empty DQ profile dataset."
            )

        field_names: set[str] = set()

        for row in rows_list:
            if not isinstance(row, dict):
                raise ValueError(
                    "Profile staging rows must be dictionaries."
                )

            for key in row.keys():
                field_names.add(
                    self._validate_stage_column_name(
                        str(key)
                    )
                )

        ordered_fields = sorted(field_names)

        table_id = self.get_profile_stage_table_id(
            organization_id=organization_id,
            profile_run_id=profile_run_id,
        )

        now = datetime.now(timezone.utc)
        retention_days = (
            expiration_days
            if expiration_days is not None
            else self.profile_stage_expiration_days
        )

        if retention_days < 1 or retention_days > 30:
            raise ValueError(
                "Profile staging expiration_days must be between 1 and 30."
            )

        schema = [
            bigquery.SchemaField(
                "_organization_id",
                "STRING",
                mode="REQUIRED",
            ),
            bigquery.SchemaField(
                "_profile_run_id",
                "STRING",
                mode="REQUIRED",
            ),
            bigquery.SchemaField(
                "_source_type",
                "STRING",
                mode="REQUIRED",
            ),
            bigquery.SchemaField(
                "_source_name",
                "STRING",
                mode="REQUIRED",
            ),
            bigquery.SchemaField(
                "_domain",
                "STRING",
                mode="REQUIRED",
            ),
            bigquery.SchemaField(
                "_source_row_number",
                "INT64",
                mode="REQUIRED",
            ),
            bigquery.SchemaField(
                "_loaded_at",
                "TIMESTAMP",
                mode="REQUIRED",
            ),
        ]

        schema.extend(
            bigquery.SchemaField(
                field_name,
                "STRING",
                mode="NULLABLE",
            )
            for field_name in ordered_fields
        )

        table = bigquery.Table(
            table_id,
            schema=schema,
        )
        table.expires = (
            now + timedelta(days=retention_days)
        )
        table.description = (
            "Tenant-isolated non-production DQ profile staging "
            "table used only for governed remediation validation "
            "and execution."
        )
        table.labels = {
            "organization_id": organization_id.lower(),
            "profile_run_id": profile_run_id.lower(),
            "domain": normalized_domain.lower(),
            "purpose": "dq_profile_stage",
        }

        self.client.create_table(
            table,
            exists_ok=True,
        )

        staged_rows: list[dict[str, Any]] = []

        for index, source_row in enumerate(
            rows_list,
            start=1,
        ):
            staged_row: dict[str, Any] = {
                "_organization_id": organization_id,
                "_profile_run_id": profile_run_id,
                "_source_type": normalized_source_type,
                "_source_name": normalized_source_name,
                "_domain": normalized_domain,
                "_source_row_number": index,
                "_loaded_at": now.isoformat(),
            }

            for field_name in ordered_fields:
                value = source_row.get(field_name)
                staged_row[field_name] = (
                    None
                    if value is None
                    else str(value)
                )

            staged_rows.append(staged_row)

        load_config = bigquery.LoadJobConfig(
            schema=schema,
            write_disposition=(
                bigquery.WriteDisposition.WRITE_TRUNCATE
            ),
        )

        load_job = self.client.load_table_from_json(
            staged_rows,
            table_id,
            job_config=load_config,
        )
        load_job.result()

        loaded_table = self.client.get_table(
            table_id
        )

        if loaded_table.num_rows != len(staged_rows):
            raise RuntimeError(
                "DQ profile staging row-count validation failed. "
                f"expected={len(staged_rows)} "
                f"actual={loaded_table.num_rows}"
            )

        logger.info(
            "DQ profile staging materialized. "
            "organization_id=%s profile_run_id=%s "
            "table_id=%s rows=%s expires=%s",
            organization_id,
            profile_run_id,
            table_id,
            len(staged_rows),
            table.expires.isoformat(),
        )

        return table_id

    def save_remediation_feedback(
        self,
        *,
        organization_id: str,
        recommendation_id: str,
        decision: str,
        steward_user: str,
        reason_code: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """
        Persist an immutable steward remediation decision and update
        the recommendation's current approval state.

        Tenant isolation is enforced by organization_id on every lookup
        and update.
        """
        organization_id = self._require_organization_id(
            organization_id
        )

        recommendation_id = str(
            recommendation_id or ""
        ).strip()

        if not recommendation_id:
            raise ValueError(
                "recommendation_id is required."
            )

        steward_user = str(
            steward_user or ""
        ).strip()

        if not steward_user:
            raise ValueError(
                "steward_user is required."
            )

        normalized_decision = str(
            decision or ""
        ).strip().upper()

        allowed_decisions = {
            "APPROVE",
            "REJECT",
            "REQUEST_CHANGES",
        }

        if normalized_decision not in allowed_decisions:
            raise ValueError(
                "decision must be APPROVE, REJECT, "
                "or REQUEST_CHANGES."
            )

        # ---------------------------------------------------------
        # Load recommendation using tenant boundary
        # ---------------------------------------------------------

        recommendation = self.get_ai_recommendation(
            organization_id=organization_id,
            recommendation_id=recommendation_id,
        )

        if recommendation is None:
            raise ValueError(
                "DQ recommendation was not found."
            )

        profile_run_id = recommendation.get(
            "profile_run_id"
        )

        artifact_type = recommendation.get(
            "remediation_artifact_type"
        )

        safety_status = recommendation.get(
            "remediation_safety_status"
        )
        if (
            normalized_decision == "APPROVE"
            and str(safety_status or "").strip().upper()
            == "BLOCKED"
        ):
            raise ValueError(
                "Blocked remediation cannot be approved."
            )

        feedback_event_id = (
            f"dqrf_{uuid.uuid4().hex}"
        )

        now = datetime.now(timezone.utc)

        # ---------------------------------------------------------
        # Immutable feedback event
        # ---------------------------------------------------------

        insert_sql = f"""
        INSERT INTO `{self.remediation_feedback_table_id}`
        (
            feedback_event_id,
            organization_id,
            recommendation_id,
            profile_run_id,
            decision,
            reason_code,
            note,
            steward_user,
            artifact_type,
            safety_status,
            created_at
        )
        VALUES
        (
            @feedback_event_id,
            @organization_id,
            @recommendation_id,
            @profile_run_id,
            @decision,
            @reason_code,
            @note,
            @steward_user,
            @artifact_type,
            @safety_status,
            @created_at
        )
        """

        insert_params = [
            bigquery.ScalarQueryParameter(
                "feedback_event_id",
                "STRING",
                feedback_event_id,
            ),
            bigquery.ScalarQueryParameter(
                "organization_id",
                "STRING",
                organization_id,
            ),
            bigquery.ScalarQueryParameter(
                "recommendation_id",
                "STRING",
                recommendation_id,
            ),
            bigquery.ScalarQueryParameter(
                "profile_run_id",
                "STRING",
                profile_run_id,
            ),
            bigquery.ScalarQueryParameter(
                "decision",
                "STRING",
                normalized_decision,
            ),
            bigquery.ScalarQueryParameter(
                "reason_code",
                "STRING",
                reason_code,
            ),
            bigquery.ScalarQueryParameter(
                "note",
                "STRING",
                note,
            ),
            bigquery.ScalarQueryParameter(
                "steward_user",
                "STRING",
                steward_user,
            ),
            bigquery.ScalarQueryParameter(
                "artifact_type",
                "STRING",
                artifact_type,
            ),
            bigquery.ScalarQueryParameter(
                "safety_status",
                "STRING",
                safety_status,
            ),
            bigquery.ScalarQueryParameter(
                "created_at",
                "TIMESTAMP",
                now,
            ),
        ]

        self.client.query(
            insert_sql,
            job_config=bigquery.QueryJobConfig(
                query_parameters=insert_params
            ),
        ).result()

        # ---------------------------------------------------------
        # Update current recommendation state
        # ---------------------------------------------------------

        if normalized_decision == "APPROVE":
            approval_status = "APPROVED"
            approved_by = steward_user
            approved_at = now
            rejection_reason = None

        elif normalized_decision == "REJECT":
            approval_status = "REJECTED"
            approved_by = None
            approved_at = None
            rejection_reason = (
                note
                or reason_code
                or "Rejected by data steward."
            )

        else:
            approval_status = "CHANGES_REQUESTED"
            approved_by = None
            approved_at = None
            rejection_reason = (
                note
                or reason_code
                or "Changes requested by data steward."
            )

        update_sql = f"""
        UPDATE `{self.ai_recommendations_table_id}`
        SET
            remediation_approval_status =
                @approval_status,
            remediation_approved_by =
                @approved_by,
            remediation_approved_at =
                @approved_at,
            remediation_rejection_reason =
                @rejection_reason
        WHERE organization_id = @organization_id
        AND recommendation_id = @recommendation_id
        """

        update_params = [
            bigquery.ScalarQueryParameter(
                "approval_status",
                "STRING",
                approval_status,
            ),
            bigquery.ScalarQueryParameter(
                "approved_by",
                "STRING",
                approved_by,
            ),
            bigquery.ScalarQueryParameter(
                "approved_at",
                "TIMESTAMP",
                approved_at,
            ),
            bigquery.ScalarQueryParameter(
                "rejection_reason",
                "STRING",
                rejection_reason,
            ),
            bigquery.ScalarQueryParameter(
                "organization_id",
                "STRING",
                organization_id,
            ),
            bigquery.ScalarQueryParameter(
                "recommendation_id",
                "STRING",
                recommendation_id,
            ),
        ]

        self.client.query(
            update_sql,
            job_config=bigquery.QueryJobConfig(
                query_parameters=update_params
            ),
        ).result()

        return {
            "feedback_event_id": feedback_event_id,
            "organization_id": organization_id,
            "recommendation_id": recommendation_id,
            "profile_run_id": profile_run_id,
            "decision": normalized_decision,
            "approval_status": approval_status,
            "reason_code": reason_code,
            "note": note,
            "steward_user": steward_user,
            "created_at": now,
        }

    def get_remediation_versions(
        self,
        *,
        organization_id: str,
        recommendation_id: str,
    ) -> list[dict[str, Any]]:
        organization_id = self._require_organization_id(
            organization_id
        )

        normalized_recommendation_id = str(
            recommendation_id or ""
        ).strip()

        if not normalized_recommendation_id:
            raise ValueError(
                "recommendation_id is required."
            )

        sql = f"""
        SELECT
            remediation_version_id,
            organization_id,
            recommendation_id,
            profile_run_id,
            version_number,
            artifact_type,
            remediation_sql,
            remediation_regex,
            sql_dialect,
            safety_status,
            reasoning_summary,
            steward_approval_required,
            generation_reason,
            generated_from_feedback_event_id,
            revision_guidance,
            ai_provider,
            ai_model,
            generated_by,
            generated_at,
            created_at
        FROM `{self.project_id}.{self.dataset}.DQ_AI_REMEDIATION_VERSIONS`
        WHERE organization_id = @organization_id
        AND recommendation_id = @recommendation_id
        ORDER BY version_number DESC
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "recommendation_id",
                    "STRING",
                    normalized_recommendation_id,
                ),
            ]
        )

        rows = self.client.query(
            sql,
            job_config=job_config,
        ).result()

        return [
            dict(row)
            for row in rows
        ]

    # ---------------------------------------------------------
    # Tenant validation
    # ---------------------------------------------------------

    @staticmethod
    def _require_organization_id(
        organization_id: str,
    ) -> str:
        normalized = str(
            organization_id or ""
        ).strip()


        if not normalized:
            raise ValueError(
                "organization_id is required for "
                "tenant-isolated DQ persistence."
            )

        if not normalized.startswith("org_"):
            raise ValueError(
                "organization_id must use the "
                "org_ identifier standard."
            )

        return normalized


    def _insert_json_batches(
        self,
        *,
        table_id: str,
        rows: list[dict[str, Any]],
        row_ids: list[str] | None = None,
        batch_size: int = 500,
        operation: str,
    ) -> None:
        """
        Persist rows with BigQuery streaming inserts in bounded batches.

        This avoids creating one BigQuery query job per finding/score,
        which is prohibitively slow for interactive profiling.
        """
        if not rows:
            return

        if batch_size < 1:
            raise ValueError("batch_size must be at least 1.")

        if row_ids is not None and len(row_ids) != len(rows):
            raise ValueError(
                "row_ids must contain one value for every row."
            )

        started = time.perf_counter()

        for start in range(0, len(rows), batch_size):
            end = min(start + batch_size, len(rows))
            batch = rows[start:end]
            batch_row_ids = (
                row_ids[start:end]
                if row_ids is not None
                else None
            )

            errors = self.client.insert_rows_json(
                table_id,
                batch,
                row_ids=batch_row_ids,
            )

            if errors:
                logger.error(
                    "BigQuery batch insert failed. "
                    "operation=%s table_id=%s "
                    "batch_start=%s batch_end=%s errors=%s",
                    operation,
                    table_id,
                    start,
                    end,
                    errors,
                )
                raise RuntimeError(
                    f"{operation} failed while writing "
                    f"rows {start}-{end - 1}: {errors}"
                )

        logger.info(
            "BigQuery batch insert complete. "
            "operation=%s rows=%s duration_ms=%.1f",
            operation,
            len(rows),
            (time.perf_counter() - started) * 1000.0,
        )

    # ---------------------------------------------------------
    # Main persistence entry point
    # ---------------------------------------------------------

    def save_profile_result(
        self,
        *,
        result: QualityProfileResult,
        column_mappings: list[dict[str, Any]] | None = None
    ) -> None:

        organization_id = self._require_organization_id(
            result.organization_id
        )

        self._validate_result_tenant(
            organization_id=organization_id,
            result=result,
        )

        logger.info(
            "Persisting DQ profile result. "
            "organization_id=%s profile_run_id=%s "
            "domain=%s source_table=%s",
            organization_id,
            result.profile_run_id,
            result.domain,
            result.source_table,
        )

        self.save_findings(
            organization_id=organization_id,
            findings=result.findings,
        )

        self.save_record_scores(
            organization_id=organization_id,
            scores=result.record_scores,
        )

        self.save_rule_executions(
            organization_id=organization_id,
            executions=result.rule_executions,
        )

        self.save_daily_summary(
            organization_id=organization_id,
            result=result,
            column_mappings= column_mappings,
        )

        logger.info(
            "DQ profile persistence complete. "
            "organization_id=%s profile_run_id=%s "
            "findings=%s record_scores=%s rule_executions=%s",
            organization_id,
            result.profile_run_id,
            len(result.findings),
            len(result.record_scores),
            len(result.rule_executions),
        )

    # ---------------------------------------------------------
    # Findings
    # ---------------------------------------------------------

    def save_findings(
        self,
        *,
        organization_id: str,
        findings: Iterable[QualityFinding],
    ) -> None:
        organization_id = self._require_organization_id(
            organization_id
        )

        findings_list = list(findings)

        if not findings_list:
            return

        now = datetime.now(timezone.utc).isoformat()

        rows: list[dict[str, Any]] = []
        row_ids: list[str] = []

        for finding in findings_list:
            if finding.organization_id != organization_id:
                raise ValueError(
                    "DQ finding belongs to a "
                    "different organization."
                )

            finding_id = str(finding.finding_id)

            rows.append(
                {
                    "finding_id": finding_id,
                    "organization_id": organization_id,
                    "profile_run_id": finding.profile_run_id,
                    "source_table": finding.source_table,
                    "source_row_id": finding.source_row_id,
                    "created_at": now,
                    "domain": finding.domain,
                    "record_id": finding.record_id,
                    "field_name": finding.field_name,
                    "rule_id": finding.rule_id,
                    "dimension": finding.dimension,
                    "rule_category": finding.dimension,
                    "rule_type": "PROFILE_RULE",
                    "severity": finding.severity,
                    "status": "OPEN",
                    "rule_weight": 1.0,
                    "finding_message": finding.finding_message,
                    "observed_value": (
                        None
                        if finding.observed_value is None
                        else str(finding.observed_value)
                    ),

                    "proposed_value": (
                        None
                        if finding.proposed_value is None
                        else str(finding.proposed_value)
                    ),
                }
            )
            row_ids.append(finding_id)

        self._insert_json_batches(
            table_id=self.findings_table_id,
            rows=rows,
            row_ids=row_ids,
            operation="save_findings",
        )

    # ---------------------------------------------------------
    # Record scores
    # ---------------------------------------------------------

    def save_record_scores(
        self,
        *,
        organization_id: str,
        scores: Iterable[RecordQualityScore],
    ) -> None:
        organization_id = self._require_organization_id(
            organization_id
        )

        score_list = list(scores)

        if not score_list:
            return

        now = datetime.now(timezone.utc).isoformat()

        rows: list[dict[str, Any]] = []
        row_ids: list[str] = []

        for score in score_list:
            if score.organization_id != organization_id:
                raise ValueError(
                    "DQ record score belongs to a "
                    "different organization."
                )

            rows.append(
                {
                    "created_at": now,
                    "organization_id": organization_id,
                    "profile_run_id": score.profile_run_id,
                    "source_table": score.source_table,
                    "source_row_id": score.source_row_id,
                    "domain": score.domain,
                    "record_id": score.record_id,
                    "record_score": score.overall_score,
                    "completeness_score": score.completeness_score,
                    "validity_score": score.validity_score,
                    "standardization_score": (
                        score.standardization_score
                    ),
                    "consistency_score": score.consistency_score,
                    "uniqueness_score": score.uniqueness_score,
                    "issue_count": score.issue_count,
                    "critical_issue_count": (
                        score.critical_issue_count
                    ),
                    "high_issue_count": score.high_issue_count,
                    "medium_issue_count": score.medium_issue_count,
                    "low_issue_count": score.low_issue_count,
                }
            )

            row_ids.append(
                f"{score.profile_run_id}:{score.source_row_id}"
            )

        self._insert_json_batches(
            table_id=self.record_score_table_id,
            rows=rows,
            row_ids=row_ids,
            operation="save_record_scores",
        )

    # ---------------------------------------------------------
    # Rule execution evidence
    # ---------------------------------------------------------

    def save_rule_executions(
        self,
        *,
        organization_id: str,
        executions: Iterable[QualityRuleExecution],
    ) -> None:
        organization_id = self._require_organization_id(
            organization_id
        )

        execution_list = list(executions)

        if not execution_list:
            return

        now = datetime.now(timezone.utc).isoformat()
        rows: list[dict[str, Any]] = []
        row_ids: list[str] = []

        for execution in execution_list:
            if execution.organization_id != organization_id:
                raise ValueError(
                    "DQ rule execution belongs to a "
                    "different organization."
                )

            rule_id = str(execution.rule_id or "").strip().upper()
            if not rule_id:
                raise ValueError(
                    "DQ rule execution rule_id is required."
                )

            rows.append(
                {
                    "created_at": now,
                    "organization_id": organization_id,
                    "profile_run_id": execution.profile_run_id,
                    "domain": execution.domain,
                    "source_table": execution.source_table,
                    "rule_id": rule_id,
                    "dimension": execution.dimension,
                    "severity": execution.severity,
                    "evaluated_record_count": (
                        execution.evaluated_record_count
                    ),
                    "passed_record_count": (
                        execution.passed_record_count
                    ),
                    "failed_record_count": (
                        execution.failed_record_count
                    ),
                    "skipped_record_count": (
                        execution.skipped_record_count
                    ),
                    "execution_status": (
                        execution.execution_status
                    ),
                    "compliance_rate": (
                        execution.compliance_rate
                    ),
                }
            )

            row_ids.append(
                f"{execution.profile_run_id}:{rule_id}"
            )

        self._insert_json_batches(
            table_id=self.rule_execution_table_id,
            rows=rows,
            row_ids=row_ids,
            operation="save_rule_executions",
        )

    def get_latest_remediation_change_request(
        self,
        *,
        organization_id: str,
        recommendation_id: str,
    ) -> dict[str, Any] | None:
        organization_id = self._require_organization_id(
            organization_id
        )

        sql = f"""
        SELECT
            feedback_event_id,
            recommendation_id,
            profile_run_id,
            decision,
            reason_code,
            note,
            steward_user,
            created_at
        FROM `{self.remediation_feedback_table_id}`
        WHERE organization_id = @organization_id
        AND recommendation_id = @recommendation_id
        AND decision = 'REQUEST_CHANGES'
        ORDER BY created_at DESC
        LIMIT 1
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "recommendation_id",
                    "STRING",
                    recommendation_id,
                ),
            ]
        )

        rows = list(
            self.client.query(
                sql,
                job_config=job_config,
            ).result()
        )

        if not rows:
            return None

        return dict(rows[0])

    # ---------------------------------------------------------
    # Daily summary
    # ---------------------------------------------------------

    def save_daily_summary(
        self,
        *,
        organization_id: str,
        result: QualityProfileResult,
        column_mappings: list[dict[str, Any]] | None = None,
    ) -> None:
        """
        Persist the profile summary with a BigQuery MERGE rather than a
        streaming insert.

        This is intentional: Analyze with AI updates the same summary row
        immediately after profiling. BigQuery DML cannot update rows that are
        still in the streaming buffer.
        """
        organization_id = self._require_organization_id(
            organization_id
        )

        if result.organization_id != organization_id:
            raise ValueError(
                "DQ summary belongs to a "
                "different organization."
            )

        total_rules_executed = sum(
            1
            for execution in result.rule_executions
            if str(
                execution.execution_status or ""
            ).strip().upper() == "EXECUTED"
        )

        failed_rule_count = sum(
            1
            for execution in result.rule_executions
            if (
                str(
                    execution.execution_status or ""
                ).strip().upper() == "EXECUTED"
                and int(
                    execution.failed_record_count or 0
                ) > 0
            )
        )
        dq_health_score = float(result.avg_record_score)

        dq_risk_score = round(
            max(
                0.0,
                min(
                    100.0,
                    100.0 - dq_health_score,
                ),
            ),
            2,
        )

        automation_readiness_score = (
            self._calculate_automation_readiness(result)
        )

        now = datetime.now(timezone.utc)

        sql = f"""
        MERGE `{self.daily_summary_table_id}` AS target
        USING (
            SELECT
                @organization_id AS organization_id,
                @profile_run_id AS profile_run_id,
                @metric_date AS metric_date,
                @domain AS domain,
                @source_table AS source_table
        ) AS source
        ON target.organization_id = source.organization_id
           AND target.profile_run_id = source.profile_run_id
           AND target.metric_date = source.metric_date
           AND target.domain = source.domain

        WHEN MATCHED THEN
          UPDATE SET
            source_table = @source_table,
            column_mappings = PARSE_JSON(@column_mappings_json),
            total_records = @total_records,
            scored_record_count = @scored_record_count,
            records_with_findings = @records_with_findings,
            avg_record_score = @avg_record_score,
            avg_completeness_score = @avg_completeness_score,
            avg_validity_score = @avg_validity_score,
            avg_standardization_score = @avg_standardization_score,
            avg_consistency_score = @avg_consistency_score,
            avg_uniqueness_score = @avg_uniqueness_score,
            records_below_threshold = @records_below_threshold,
            total_findings = @total_findings,
            critical_findings = @critical_findings,
            high_findings = @high_findings,
            medium_findings = @medium_findings,
            low_findings = @low_findings,
            total_rules_executed = @total_rules_executed,
            failed_rule_count = @failed_rule_count,
            duplicate_record_count = @duplicate_record_count,
            records_flagged_by_ai = @records_flagged_by_ai,
            ai_recommendations_generated = @ai_recommendations_generated,
            steward_actions_taken = @steward_actions_taken,
            automated_fixes_applied = @automated_fixes_applied,
            dq_health_score = @dq_health_score,
            dq_risk_score = @dq_risk_score,
            automation_readiness_score = @automation_readiness_score

        WHEN NOT MATCHED THEN
          INSERT (
            metric_date,
            organization_id,
            profile_run_id,
            source_table,
            column_mappings,
            domain,
            total_records,
            scored_record_count,
            records_with_findings,
            avg_record_score,
            avg_completeness_score,
            avg_validity_score,
            avg_standardization_score,
            avg_consistency_score,
            avg_uniqueness_score,
            records_below_threshold,
            total_findings,
            critical_findings,
            high_findings,
            medium_findings,
            low_findings,
            total_rules_executed,
            failed_rule_count,
            duplicate_record_count,
            records_flagged_by_ai,
            ai_recommendations_generated,
            steward_actions_taken,
            automated_fixes_applied,
            dq_health_score,
            dq_risk_score,
            automation_readiness_score,
            created_at
          )
          VALUES (
            @metric_date,
            @organization_id,
            @profile_run_id,
            @source_table,
            PARSE_JSON(@column_mappings_json),
            @domain,
            @total_records,
            @scored_record_count,
            @records_with_findings,
            @avg_record_score,
            @avg_completeness_score,
            @avg_validity_score,
            @avg_standardization_score,
            @avg_consistency_score,
            @avg_uniqueness_score,
            @records_below_threshold,
            @total_findings,
            @critical_findings,
            @high_findings,
            @medium_findings,
            @low_findings,
            @total_rules_executed,
            @failed_rule_count,
            @duplicate_record_count,
            @records_flagged_by_ai,
            @ai_recommendations_generated,
            @steward_actions_taken,
            @automated_fixes_applied,
            @dq_health_score,
            @dq_risk_score,
            @automation_readiness_score,
            @created_at
          )
        """

        params = [
            bigquery.ScalarQueryParameter("metric_date", "DATE", now.date()),
            bigquery.ScalarQueryParameter(
                "organization_id", "STRING", organization_id
            ),
            bigquery.ScalarQueryParameter(
                "profile_run_id", "STRING", result.profile_run_id
            ),
            bigquery.ScalarQueryParameter(
                "source_table", "STRING", result.source_table
            ),
            
            bigquery.ScalarQueryParameter(
                "column_mappings_json",
                "STRING",
                json.dumps(column_mappings or []),
            ),
            
            bigquery.ScalarQueryParameter("domain", "STRING", result.domain),
            bigquery.ScalarQueryParameter(
                "total_records", "INT64", result.total_records
            ),
            bigquery.ScalarQueryParameter(
                "scored_record_count", "INT64", result.scored_record_count
            ),
            bigquery.ScalarQueryParameter(
                "records_with_findings", "INT64", result.records_with_findings
            ),
            bigquery.ScalarQueryParameter(
                "avg_record_score", "FLOAT64", result.avg_record_score
            ),
            bigquery.ScalarQueryParameter(
                "avg_completeness_score",
                "FLOAT64",
                result.avg_completeness_score,
            ),
            bigquery.ScalarQueryParameter(
                "avg_validity_score", "FLOAT64", result.avg_validity_score
            ),
            bigquery.ScalarQueryParameter(
                "avg_standardization_score",
                "FLOAT64",
                result.avg_standardization_score,
            ),
            bigquery.ScalarQueryParameter(
                "avg_consistency_score",
                "FLOAT64",
                result.avg_consistency_score,
            ),
            bigquery.ScalarQueryParameter(
                "avg_uniqueness_score",
                "FLOAT64",
                result.avg_uniqueness_score,
            ),
            bigquery.ScalarQueryParameter(
                "records_below_threshold",
                "INT64",
                result.records_below_threshold,
            ),
            bigquery.ScalarQueryParameter(
                "total_findings", "INT64", result.total_findings
            ),
            bigquery.ScalarQueryParameter(
                "critical_findings", "INT64", result.critical_findings
            ),
            bigquery.ScalarQueryParameter(
                "high_findings", "INT64", result.high_findings
            ),
            bigquery.ScalarQueryParameter(
                "medium_findings", "INT64", result.medium_findings
            ),
            bigquery.ScalarQueryParameter(
                "low_findings", "INT64", result.low_findings
            ),
            bigquery.ScalarQueryParameter(
                "total_rules_executed", "INT64", total_rules_executed
            ),
            bigquery.ScalarQueryParameter(
                "failed_rule_count", "INT64", failed_rule_count
            ),
            bigquery.ScalarQueryParameter(
                "duplicate_record_count",
                "INT64",
                result.duplicate_record_count,
            ),
            bigquery.ScalarQueryParameter(
                "records_flagged_by_ai", "INT64", 0
            ),
            bigquery.ScalarQueryParameter(
                "ai_recommendations_generated", "INT64", 0
            ),
            bigquery.ScalarQueryParameter(
                "steward_actions_taken", "INT64", 0
            ),
            bigquery.ScalarQueryParameter(
                "automated_fixes_applied", "INT64", 0
            ),
            bigquery.ScalarQueryParameter(
                "dq_health_score", "FLOAT64", dq_health_score
            ),
            bigquery.ScalarQueryParameter(
                "dq_risk_score", "FLOAT64", dq_risk_score
            ),
            bigquery.ScalarQueryParameter(
                "automation_readiness_score",
                "FLOAT64",
                automation_readiness_score,
            ),
            bigquery.ScalarQueryParameter(
                "created_at", "TIMESTAMP", now
            ),
        ]

        started = time.perf_counter()

        logger.warning(
            "DQ COLUMN MAPPINGS PERSIST DEBUG "
            "organization_id=%s "
            "profile_run_id=%s "
            "column_mappings=%r",
            organization_id,
            result.profile_run_id,
            column_mappings,
        )

        self.client.query(
            sql,
            job_config=bigquery.QueryJobConfig(
                query_parameters=params
            ),
        ).result()

        logger.info(
            "BigQuery summary MERGE complete. "
            "operation=save_daily_summary "
            "profile_run_id=%s duration_ms=%.1f",
            result.profile_run_id,
            (time.perf_counter() - started) * 1000.0,
        )

    # ---------------------------------------------------------
    # AI DQ recommendation retrieval + persistence
    # ---------------------------------------------------------

    def get_profile_summary(
        self,
        *,
        organization_id: str,
        profile_run_id: str,
    ) -> dict | None:
        organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        sql = f"""
            SELECT
                organization_id,
                profile_run_id,
                source_table,
                column_mappings,
                domain,
                total_records,
                avg_record_score,
                total_findings,
                records_with_findings,
                duplicate_record_count
            FROM `{self.daily_summary_table_id}`
            WHERE organization_id = @organization_id
            AND profile_run_id = @profile_run_id
            ORDER BY created_at DESC
            LIMIT 1
            """

        params = [
            bigquery.ScalarQueryParameter(
                "organization_id",
                "STRING",
                organization_id,
            ),
            bigquery.ScalarQueryParameter(
                "profile_run_id",
                "STRING",
                profile_run_id,
            ),
        ]

        rows = list(
            self.client.query(
                sql,
                job_config=(
                    bigquery.QueryJobConfig(
                        query_parameters=params
                    )
                ),
            ).result()
        )

        if not rows:
            return None

        return dict(rows[0])


    def get_finding_aggregates(
        self,
        *,
        organization_id: str,
        profile_run_id: str,
    ) -> list[dict]:
        organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        sql = f"""
        SELECT
            rule_id,
            field_name,
            rule_category AS dimension,
            severity,
            COUNT(*) AS finding_count,
            COUNT(DISTINCT source_row_id)
                AS affected_record_count
        FROM `{self.findings_table_id}`
        WHERE organization_id = @organization_id
          AND profile_run_id = @profile_run_id
        GROUP BY
            rule_id,
            field_name,
            dimension,
            severity
        ORDER BY
            finding_count DESC,
            rule_id
        """

        params = [
            bigquery.ScalarQueryParameter(
                "organization_id",
                "STRING",
                organization_id,
            ),
            bigquery.ScalarQueryParameter(
                "profile_run_id",
                "STRING",
                profile_run_id,
            ),
        ]

        rows = self.client.query(
            sql,
            job_config=(
                bigquery.QueryJobConfig(
                    query_parameters=params
                )
            ),
        ).result()

        return [
            dict(row)
            for row in rows
        ]
    def get_finding_evidence(
        self,
        *,
        organization_id: str,
        profile_run_id: str,
        rule_id: str,
        field_name: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Return a bounded, tenant-scoped sample of deterministic DQ findings
        for steward-facing evidence.

        Evidence is sourced only from persisted DQ_FINDINGS. It is never
        generated or inferred by the AI recommendation layer.
        """
        organization_id = self._require_organization_id(
            organization_id
        )

        normalized_profile_run_id = str(
            profile_run_id or ""
        ).strip()

        normalized_rule_id = str(
            rule_id or ""
        ).strip().upper()

        normalized_field_name = str(
            field_name or ""
        ).strip().lower()

        if not normalized_profile_run_id:
            raise ValueError("profile_run_id is required.")

        if not normalized_rule_id:
            raise ValueError("rule_id is required.")

        if not normalized_field_name:
            raise ValueError("field_name is required.")

        bounded_limit = max(
            1,
            min(int(limit), 10),
        )

        sql = f"""
        SELECT
            finding_id,
            source_row_id,
            record_id,
            field_name,
            rule_id,
            dimension,
            severity,
            finding_message,
            observed_value,
            proposed_value
        FROM `{self.findings_table_id}`
        WHERE organization_id = @organization_id
        AND profile_run_id = @profile_run_id
        AND UPPER(TRIM(rule_id)) = @rule_id
        AND LOWER(TRIM(field_name)) = @field_name
        ORDER BY source_row_id, finding_id
        LIMIT @limit
        """

        params = [
            bigquery.ScalarQueryParameter(
                "organization_id",
                "STRING",
                organization_id,
            ),
            bigquery.ScalarQueryParameter(
                "profile_run_id",
                "STRING",
                normalized_profile_run_id,
            ),
            bigquery.ScalarQueryParameter(
                "rule_id",
                "STRING",
                normalized_rule_id,
            ),
            bigquery.ScalarQueryParameter(
                "field_name",
                "STRING",
                normalized_field_name,
            ),
            bigquery.ScalarQueryParameter(
                "limit",
                "INT64",
                bounded_limit,
            ),
        ]

        rows = self.client.query(
            sql,
            job_config=bigquery.QueryJobConfig(
                query_parameters=params
            ),
        ).result()

        return [
            dict(row)
            for row in rows
        ]
    
    def get_profile_rule_compliance_inputs(
        self,
        *,
        organization_id: str,
        profile_run_id: str,
    ) -> dict[str, Any]:
        """
        Return authoritative, tenant-scoped DQ rule execution evidence
        for Governance Policy -> DQ Rule compliance evaluation.

        Compliance is read from DQ_RULE_EXECUTION rather than inferred
        from DQ_FINDINGS, so zero-failure and skipped/not-evaluated rules
        are represented explicitly.
        """
        organization_id = self._require_organization_id(
            organization_id
        )

        normalized_profile_run_id = str(
            profile_run_id or ""
        ).strip()

        if not normalized_profile_run_id:
            raise ValueError(
                "profile_run_id is required."
            )

        profile = self.get_profile_summary(
            organization_id=organization_id,
            profile_run_id=normalized_profile_run_id,
        )

        if not profile:
            raise ValueError(
                "DQ profile summary was not found for "
                "the authenticated organization."
            )

        domain = str(
            profile.get("domain") or ""
        ).strip().upper()

        if not domain:
            raise ValueError(
                "DQ profile summary does not contain a domain."
            )

        total_records = max(
            0,
            int(profile.get("total_records") or 0),
        )

        sql = f"""
        SELECT
            organization_id,
            profile_run_id,
            domain,
            source_table,
            UPPER(TRIM(rule_id)) AS rule_id,
            UPPER(TRIM(dimension)) AS dimension,
            UPPER(TRIM(severity)) AS severity,
            evaluated_record_count,
            passed_record_count,
            failed_record_count,
            skipped_record_count,
            UPPER(TRIM(execution_status)) AS execution_status,
            compliance_rate,
            created_at
        FROM `{self.rule_execution_table_id}`
        WHERE organization_id = @organization_id
          AND profile_run_id = @profile_run_id
        ORDER BY rule_id
        """

        rows = list(
            self.client.query(
                sql,
                job_config=bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter(
                            "organization_id",
                            "STRING",
                            organization_id,
                        ),
                        bigquery.ScalarQueryParameter(
                            "profile_run_id",
                            "STRING",
                            normalized_profile_run_id,
                        ),
                    ]
                ),
            ).result()
        )

        rule_results: list[dict[str, Any]] = []

        for row in rows:
            item = dict(row)

            row_org = str(
                item.get("organization_id") or ""
            ).strip()
            row_profile_run_id = str(
                item.get("profile_run_id") or ""
            ).strip()
            row_domain = str(
                item.get("domain") or ""
            ).strip().upper()

            if row_org != organization_id:
                raise RuntimeError(
                    "DQ rule execution evidence returned "
                    "another organization."
                )

            if row_profile_run_id != normalized_profile_run_id:
                raise RuntimeError(
                    "DQ rule execution evidence returned "
                    "another profile run."
                )

            if row_domain != domain:
                raise RuntimeError(
                    "DQ rule execution evidence returned "
                    "another domain."
                )

            evaluated = max(
                0,
                int(item.get("evaluated_record_count") or 0),
            )
            passed = max(
                0,
                int(item.get("passed_record_count") or 0),
            )
            failed = max(
                0,
                int(item.get("failed_record_count") or 0),
            )
            skipped = max(
                0,
                int(item.get("skipped_record_count") or 0),
            )

            rule_results.append(
                {
                    "organization_id": organization_id,
                    "profile_run_id": normalized_profile_run_id,
                    "domain": domain,
                    "source_table": item.get("source_table"),
                    "rule_id": str(
                        item.get("rule_id") or ""
                    ).strip().upper(),
                    "dimension": str(
                        item.get("dimension") or ""
                    ).strip().upper(),
                    "severity": str(
                        item.get("severity") or ""
                    ).strip().upper(),
                    "total_records": total_records,
                    "evaluated_record_count": evaluated,
                    "passed_record_count": passed,
                    "failed_record_count": failed,
                    "skipped_record_count": skipped,
                    "affected_record_count": failed,
                    "compliant_record_count": passed,
                    "execution_status": str(
                        item.get("execution_status") or ""
                    ).strip().upper(),
                    "measured_compliance_rate": (
                        float(item["compliance_rate"])
                        if item.get("compliance_rate") is not None
                        else None
                    ),
                    "created_at": item.get("created_at"),
                }
            )

        return {
            "organization_id": organization_id,
            "profile_run_id": normalized_profile_run_id,
            "domain": domain,
            "total_records": total_records,
            "rule_count": len(rule_results),
            "rules": rule_results,
        }

    def save_ai_recommendations(
        self,
        *,
        organization_id: str,
        profile_run_id: str,
        domain: str,
        provider: str,
        overall_analysis: dict,
        recommendations: list[dict],
    ) -> None:
        organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        if not recommendations:
            return

        sql = f"""
        MERGE `{self.ai_recommendations_table_id}` target

        USING (
          SELECT
            @organization_id AS organization_id,
            @profile_run_id AS profile_run_id,
            @recommendation_id AS recommendation_id
        ) source

        ON target.organization_id =
               source.organization_id
           AND target.profile_run_id =
               source.profile_run_id
           AND target.recommendation_id =
               source.recommendation_id

        WHEN MATCHED THEN
          UPDATE SET
            recommendation_title =
                @recommendation_title,
            recommendation_summary =
                @recommendation_summary,
            suggested_action =
                @suggested_action,
            suggested_rule_type =
                @suggested_rule_type,
            suggested_sql =
                @suggested_sql,
            suggested_regex =
                @suggested_regex,
            suggested_threshold =
                @suggested_threshold,
            automation_recommendation =
                @automation_recommendation,
            automation_confidence =
                @automation_confidence,
            source_column = @source_column,
            status =
                @status

        WHEN NOT MATCHED THEN
          INSERT (
            created_at,
            organization_id,
            profile_run_id,
            recommendation_id,
            domain,
            rule_id,
            field_name,
            dimension,
            severity,
            finding_count,
            affected_record_count,
            affected_percent,
            recommendation_title,
            recommendation_summary,
            suggested_action,
            suggested_rule_type,
            suggested_sql,
            suggested_regex,
            suggested_threshold,
            automation_recommendation,
            automation_confidence,
            source_column,
            ai_provider,
            ai_model,
            status
          )
          VALUES (
            @created_at,
            @organization_id,
            @profile_run_id,
            @recommendation_id,
            @domain,
            @rule_id,
            @field_name,
            @dimension,
            @severity,
            @finding_count,
            @affected_record_count,
            @affected_percent,
            @recommendation_title,
            @recommendation_summary,
            @suggested_action,
            @suggested_rule_type,
            @suggested_sql,
            @suggested_regex,
            @suggested_threshold,
            @automation_recommendation,
            @automation_confidence,
            @source_column,
            @ai_provider,
            @ai_model,
            @status
          )
        """

        now = datetime.now(
            timezone.utc
        )

        for recommendation in recommendations:
            if (
                recommendation.get(
                    "organization_id"
                )
                != organization_id
            ):
                raise ValueError(
                    "AI recommendation belongs "
                    "to a different organization."
                )

            print(
                    "DQ AI RECOMMENDATION PERSIST DEBUG",
                    {
                        "profile_run_id": profile_run_id,
                        "recommendation_id": recommendation.get("recommendation_id"),
                        "rule_id": recommendation.get("rule_id"),
                        "field_name": recommendation.get("field_name"),
                        "source_column": recommendation.get("source_column"),
                        "implementation_type": recommendation.get("implementation_type"),
                    },
                )

            params = [
                bigquery.ScalarQueryParameter(
                    "created_at",
                    "TIMESTAMP",
                    now,
                ),
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "profile_run_id",
                    "STRING",
                    profile_run_id,
                ),
                bigquery.ScalarQueryParameter(
                    "recommendation_id",
                    "STRING",
                    recommendation[
                        "recommendation_id"
                    ],
                ),
                bigquery.ScalarQueryParameter(
                    "domain",
                    "STRING",
                    domain,
                ),
                bigquery.ScalarQueryParameter(
                    "rule_id",
                    "STRING",
                    recommendation["rule_id"],
                ),
                bigquery.ScalarQueryParameter(
                    "field_name",
                    "STRING",
                    recommendation.get(
                        "field_name"
                    ),
                ),

                bigquery.ScalarQueryParameter(
                        "source_column",
                        "STRING",
                        str(recommendation.get("source_column") or "").strip() or None,
                    ),
                bigquery.ScalarQueryParameter(
                    "dimension",
                    "STRING",
                    recommendation[
                        "dimension"
                    ],
                ),
                bigquery.ScalarQueryParameter(
                    "severity",
                    "STRING",
                    recommendation[
                        "severity"
                    ],
                ),
                bigquery.ScalarQueryParameter(
                    "finding_count",
                    "INT64",
                    recommendation.get(
                        "finding_count",
                        0,
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "affected_record_count",
                    "INT64",
                    recommendation.get(
                        "affected_record_count",
                        0,
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "affected_percent",
                    "FLOAT64",
                    recommendation.get(
                        "affected_percent",
                        0.0,
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "recommendation_title",
                    "STRING",
                    recommendation.get(
                        "recommendation_title"
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "recommendation_summary",
                    "STRING",
                    recommendation.get(
                        "business_impact"
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "suggested_action",
                    "STRING",
                    recommendation.get(
                        "recommended_remediation"
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "suggested_rule_type",
                    "STRING",
                    recommendation.get(
                        "implementation_type"
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "suggested_sql",
                    "STRING",
                    recommendation.get(
                        "suggested_sql"
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "suggested_regex",
                    "STRING",
                    recommendation.get(
                        "suggested_regex"
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "suggested_threshold",
                    "FLOAT64",
                    recommendation.get(
                        "suggested_threshold"
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "automation_recommendation",
                    "STRING",
                    recommendation.get(
                        "automation_recommendation"
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "automation_confidence",
                    "FLOAT64",
                    recommendation.get(
                        "automation_confidence"
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "ai_provider",
                    "STRING",
                    provider,
                ),
                bigquery.ScalarQueryParameter(
                    "ai_model",
                    "STRING",
                    "configured-provider-model",
                ),
                bigquery.ScalarQueryParameter(
                    "status",
                    "STRING",
                    "OPEN",
                ),
            ]

            self.client.query(
                sql,
                job_config=(
                    bigquery.QueryJobConfig(
                        query_parameters=params
                    )
                ),
            ).result()

    def get_ai_recommendation(
        self,
        *,
        organization_id: str,
        recommendation_id: str,
    ) -> dict[str, Any] | None:
        organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        recommendation_id = str(
            recommendation_id or ""
        ).strip()

        if not recommendation_id:
            raise ValueError(
                "recommendation_id is required."
            )

        sql = f"""
        SELECT
            *
        FROM `{self.ai_recommendations_table_id}`
        WHERE organization_id = @organization_id
        AND recommendation_id = @recommendation_id
        ORDER BY created_at DESC
        LIMIT 1
        """

        params = [
            bigquery.ScalarQueryParameter(
                "organization_id",
                "STRING",
                organization_id,
            ),
            bigquery.ScalarQueryParameter(
                "recommendation_id",
                "STRING",
                recommendation_id,
            ),
        ]

        rows = list(
            self.client.query(
                sql,
                job_config=bigquery.QueryJobConfig(
                    query_parameters=params
                ),
            ).result()
        )

        if not rows:
            return None

        result = dict(rows[0])

        returned_organization_id = str(
            result.get("organization_id")
            or ""
        ).strip()

        if returned_organization_id != organization_id:
            raise RuntimeError(
                "AI recommendation query returned "
                "a different organization than the "
                "authenticated tenant."
            )

        field_name = str(
            result.get("field_name") or ""
        ).strip()

        rule_id = str(
            result.get("rule_id") or ""
        ).strip()

        profile_run_id = str(
            result.get("profile_run_id") or ""
        ).strip()

        if field_name and rule_id and profile_run_id:
            result["evidence_samples"] = self.get_finding_evidence(
                organization_id=organization_id,
                profile_run_id=profile_run_id,
                rule_id=rule_id,
                field_name=field_name,
                limit=5,
            )
        else:
            result["evidence_samples"] = []

        return result

    def get_approved_ai_recommendations(
        self,
        *,
        organization_id: str,
        profile_run_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        organization_id = self._require_organization_id(
            organization_id
        )

        normalized_profile_run_id = str(
            profile_run_id or ""
        ).strip()

        if not normalized_profile_run_id:
            raise ValueError(
                "profile_run_id is required."
            )

        safe_limit = max(
            1,
            min(int(limit), 500),
        )

        self._ensure_profile_source_context_table()

        sql = f"""
            SELECT
                recommendation.*,
                source_context.source_type,
                source_context.connection_id,
                source_context.project_id,
                source_context.dataset_id,
                source_context.table_id,
                source_context.spreadsheet_id,
                source_context.drive_id,
                source_context.item_id,
                source_context.sheet_name,
                source_context.header_row
            FROM `{self.ai_recommendations_table_id}` AS recommendation
            LEFT JOIN `{self.profile_source_context_table_id}` AS source_context
              ON source_context.organization_id = recommendation.organization_id
             AND source_context.profile_run_id = recommendation.profile_run_id
            WHERE recommendation.organization_id = @organization_id
            AND recommendation.profile_run_id = @profile_run_id
            AND recommendation.remediation_approval_status = 'APPROVED'
            ORDER BY
                recommendation.remediation_approved_at DESC,
                recommendation.created_at DESC
            LIMIT @limit
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "profile_run_id",
                    "STRING",
                    normalized_profile_run_id,
                ),
                bigquery.ScalarQueryParameter(
                    "limit",
                    "INT64",
                    safe_limit,
                ),
            ]
        )

        rows = self.client.query(
            sql,
            job_config=job_config,
        ).result()

        results = [
            dict(row)
            for row in rows
        ]

        for row in results:
            returned_org = str(
                row.get("organization_id") or ""
            ).strip()

            returned_profile_run_id = str(
                row.get("profile_run_id") or ""
            ).strip()

            if returned_org != organization_id:
                raise RuntimeError(
                    "Approved DQ recommendation query "
                    "returned another organization."
                )

            if (
                returned_profile_run_id
                != normalized_profile_run_id
            ):
                raise RuntimeError(
                    "Approved DQ recommendation query "
                    "returned another profile run."
                )

            field_name = str(
                row.get("field_name") or ""
            ).strip()

            rule_id = str(
                row.get("rule_id") or ""
            ).strip()

            if field_name and rule_id:
                row["evidence_samples"] = self.get_finding_evidence(
                    organization_id=organization_id,
                    profile_run_id=normalized_profile_run_id,
                    rule_id=rule_id,
                    field_name=field_name,
                    limit=5,
                )
            else:
                row["evidence_samples"] = []

        return results

    def update_profile_ai_counts(
        self,
        *,
        organization_id: str,
        profile_run_id: str,
        recommendation_count: int,
    ) -> None:
        organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        sql = f"""
        UPDATE `{self.daily_summary_table_id}`
        SET
            ai_recommendations_generated =
                @recommendation_count,
            records_flagged_by_ai =
                @recommendation_count
        WHERE organization_id =
              @organization_id
          AND profile_run_id =
              @profile_run_id
        """

        params = [
            bigquery.ScalarQueryParameter(
                "recommendation_count",
                "INT64",
                recommendation_count,
            ),
            bigquery.ScalarQueryParameter(
                "organization_id",
                "STRING",
                organization_id,
            ),
            bigquery.ScalarQueryParameter(
                "profile_run_id",
                "STRING",
                profile_run_id,
            ),
        ]

        self.client.query(
            sql,
            job_config=(
                bigquery.QueryJobConfig(
                    query_parameters=params
                )
            ),
        ).result()
    # ---------------------------------------------------------
    # Tenant consistency
    # ---------------------------------------------------------

    def update_ai_recommendation_remediation(
        self,
        *,
        organization_id: str,
        recommendation_id: str,
        remediation_sql: str | None,
        remediation_regex: str | None,
        artifact_type: str,
        sql_dialect: str,
        safety_status: str,
        reasoning_summary: str | None,
        steward_approval_required: bool,
    ) -> None:
        organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        recommendation_id = str(
            recommendation_id or ""
        ).strip()

        if not recommendation_id:
            raise ValueError(
                "recommendation_id is required."
            )

        sql = f"""
        UPDATE `{self.ai_recommendations_table_id}`
        SET
            remediation_sql =
                @remediation_sql,
            remediation_regex =
                @remediation_regex,
            remediation_artifact_type =
                @artifact_type,
            remediation_sql_dialect =
                @sql_dialect,
            remediation_safety_status =
                @safety_status,
            remediation_reasoning_summary =
                @reasoning_summary,
            remediation_steward_approval_required =
                @steward_approval_required,
            remediation_generated_at =
                CURRENT_TIMESTAMP()
        WHERE organization_id = @organization_id
        AND recommendation_id = @recommendation_id
        """

        params = [
            bigquery.ScalarQueryParameter(
                "remediation_sql",
                "STRING",
                remediation_sql,
            ),
            bigquery.ScalarQueryParameter(
                "remediation_regex",
                "STRING",
                remediation_regex,
            ),
            bigquery.ScalarQueryParameter(
                "artifact_type",
                "STRING",
                artifact_type,
            ),
            bigquery.ScalarQueryParameter(
                "sql_dialect",
                "STRING",
                sql_dialect,
            ),
            bigquery.ScalarQueryParameter(
                "safety_status",
                "STRING",
                safety_status,
            ),
            bigquery.ScalarQueryParameter(
                "reasoning_summary",
                "STRING",
                reasoning_summary,
            ),
            bigquery.ScalarQueryParameter(
                "steward_approval_required",
                "BOOL",
                steward_approval_required,
            ),
            bigquery.ScalarQueryParameter(
                "organization_id",
                "STRING",
                organization_id,
            ),
            bigquery.ScalarQueryParameter(
                "recommendation_id",
                "STRING",
                recommendation_id,
            ),
        ]

        job = self.client.query(
            sql,
            job_config=bigquery.QueryJobConfig(
                query_parameters=params
            ),
        )

        job.result()

        if (
            job.num_dml_affected_rows is not None
            and job.num_dml_affected_rows != 1
        ):
            raise RuntimeError(
                "Expected exactly one tenant-scoped "
                "AI recommendation remediation update, "
                f"but updated "
                f"{job.num_dml_affected_rows} rows."
            )

    def get_latest_remediation_version_number(
        self,
        *,
        organization_id: str,
        recommendation_id: str,
    ) -> int:
        query = f"""
            SELECT COALESCE(MAX(version_number), 0) AS latest_version_number
            FROM `{self.project_id}.{self.dataset}.DQ_AI_REMEDIATION_VERSIONS`
            WHERE organization_id = @organization_id
            AND recommendation_id = @recommendation_id
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "recommendation_id",
                    "STRING",
                    recommendation_id,
                ),
            ]
        )

        rows = list(
            self.client.query(
                query,
                job_config=job_config,
            ).result()
        )

        if not rows:
            return 0

        return int(rows[0]["latest_version_number"] or 0)


    def save_remediation_version(
        self,
        *,
        organization_id: str,
        recommendation_id: str,
        profile_run_id: str | None,
        artifact_type: str | None,
        remediation_sql: str | None,
        remediation_regex: str | None,
        sql_dialect: str | None,
        safety_status: str | None,
        reasoning_summary: str | None,
        steward_approval_required: bool,
        generation_reason: str,
        generated_from_feedback_event_id: str | None = None,
        revision_guidance: str | None = None,
        ai_provider: str | None = None,
        ai_model: str | None = None,
        generated_by: str | None = None,
    ) -> dict[str, Any]:
        organization_id = self._require_organization_id(
            organization_id
        )

        normalized_recommendation_id = str(
            recommendation_id or ""
        ).strip()

        if not normalized_recommendation_id:
            raise ValueError(
                "recommendation_id is required."
            )

        normalized_generation_reason = str(
            generation_reason or ""
        ).strip().upper()

        allowed_generation_reasons = {
            "INITIAL_GENERATION",
            "STEWARD_REVISION",
            "MANUAL_REGENERATION",
        }

        if normalized_generation_reason not in allowed_generation_reasons:
            raise ValueError(
                "generation_reason must be "
                "INITIAL_GENERATION, STEWARD_REVISION, "
                "or MANUAL_REGENERATION."
            )

        # ---------------------------------------------------------
        # 1. Calculate next version tenant-safely
        # ---------------------------------------------------------

        version_sql = f"""
        SELECT
            COALESCE(MAX(version_number), 0) + 1
                AS next_version_number
        FROM `{self.project_id}.{self.dataset}.DQ_AI_REMEDIATION_VERSIONS`
        WHERE organization_id = @organization_id
        AND recommendation_id = @recommendation_id
        """

        version_job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "recommendation_id",
                    "STRING",
                    normalized_recommendation_id,
                ),
            ]
        )

        version_rows = list(
            self.client.query(
                version_sql,
                job_config=version_job_config,
            ).result()
        )

        next_version_number = 1

        if version_rows:
            next_version_number = int(
                version_rows[0][
                    "next_version_number"
                ]
                or 1
            )

        remediation_version_id = (
            f"dqrv_{uuid.uuid4().hex}"
        )

        now = datetime.now(timezone.utc)

        # ---------------------------------------------------------
        # 2. Insert immutable remediation version
        # ---------------------------------------------------------

        insert_sql = f"""
        INSERT INTO `{self.project_id}.{self.dataset}.DQ_AI_REMEDIATION_VERSIONS`
        (
            remediation_version_id,
            organization_id,
            recommendation_id,
            profile_run_id,
            version_number,
            artifact_type,
            remediation_sql,
            remediation_regex,
            sql_dialect,
            safety_status,
            reasoning_summary,
            steward_approval_required,
            generation_reason,
            generated_from_feedback_event_id,
            revision_guidance,
            ai_provider,
            ai_model,
            generated_by,
            generated_at,
            created_at
        )
        VALUES
        (
            @remediation_version_id,
            @organization_id,
            @recommendation_id,
            @profile_run_id,
            @version_number,
            @artifact_type,
            @remediation_sql,
            @remediation_regex,
            @sql_dialect,
            @safety_status,
            @reasoning_summary,
            @steward_approval_required,
            @generation_reason,
            @generated_from_feedback_event_id,
            @revision_guidance,
            @ai_provider,
            @ai_model,
            @generated_by,
            @generated_at,
            @created_at
        )
        """

        insert_job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "remediation_version_id",
                    "STRING",
                    remediation_version_id,
                ),
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "recommendation_id",
                    "STRING",
                    normalized_recommendation_id,
                ),
                bigquery.ScalarQueryParameter(
                    "profile_run_id",
                    "STRING",
                    profile_run_id,
                ),
                bigquery.ScalarQueryParameter(
                    "version_number",
                    "INT64",
                    next_version_number,
                ),
                bigquery.ScalarQueryParameter(
                    "artifact_type",
                    "STRING",
                    artifact_type,
                ),
                bigquery.ScalarQueryParameter(
                    "remediation_sql",
                    "STRING",
                    remediation_sql,
                ),
                bigquery.ScalarQueryParameter(
                    "remediation_regex",
                    "STRING",
                    remediation_regex,
                ),
                bigquery.ScalarQueryParameter(
                    "sql_dialect",
                    "STRING",
                    sql_dialect,
                ),
                bigquery.ScalarQueryParameter(
                    "safety_status",
                    "STRING",
                    safety_status,
                ),
                bigquery.ScalarQueryParameter(
                    "reasoning_summary",
                    "STRING",
                    reasoning_summary,
                ),
                bigquery.ScalarQueryParameter(
                    "steward_approval_required",
                    "BOOL",
                    steward_approval_required,
                ),

                bigquery.ScalarQueryParameter(
                    "generation_reason",
                    "STRING",
                    normalized_generation_reason,
                ),

                bigquery.ScalarQueryParameter(
                    "generated_from_feedback_event_id",
                    "STRING",
                    generated_from_feedback_event_id,
                ),
                bigquery.ScalarQueryParameter(
                    "revision_guidance",
                    "STRING",
                    revision_guidance,
                ),
                bigquery.ScalarQueryParameter(
                    "ai_provider",
                    "STRING",
                    ai_provider,
                ),
                bigquery.ScalarQueryParameter(
                    "ai_model",
                    "STRING",
                    ai_model,
                ),
                bigquery.ScalarQueryParameter(
                    "generated_by",
                    "STRING",
                    generated_by,
                ),
                bigquery.ScalarQueryParameter(
                    "generated_at",
                    "TIMESTAMP",
                    now,
                ),
                bigquery.ScalarQueryParameter(
                    "created_at",
                    "TIMESTAMP",
                    now,
                ),
            ]
        )

        self.client.query(
            insert_sql,
            job_config=insert_job_config,
        ).result()

        return {
            "remediation_version_id":
                remediation_version_id,
            "organization_id":
                organization_id,
            "recommendation_id":
                normalized_recommendation_id,
            "profile_run_id":
                profile_run_id,
            "version_number":
                next_version_number,
            "generation_reason":
                normalized_generation_reason,
            "generated_from_feedback_event_id":
                generated_from_feedback_event_id,
            "generated_at":
                now,
        }
    
    def reset_remediation_approval_for_new_version(
        self,
        *,
        organization_id: str,
        recommendation_id: str,
    ) -> None:
        organization_id = self._require_organization_id(
            organization_id
        )

        normalized_recommendation_id = str(
            recommendation_id or ""
        ).strip()

        if not normalized_recommendation_id:
            raise ValueError(
                "recommendation_id is required."
            )

        sql = f"""
        UPDATE `{self.project_id}.{self.dataset}.DQ_AI_RECOMMENDATIONS`
        SET
            remediation_approval_status = 'PENDING_REVIEW',
            remediation_approved_by = NULL,
            remediation_approved_at = NULL,
            remediation_rejection_reason = NULL
        WHERE organization_id = @organization_id
        AND recommendation_id = @recommendation_id
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "recommendation_id",
                    "STRING",
                    normalized_recommendation_id,
                ),
            ]
        )

        query_job = self.client.query(
            sql,
            job_config=job_config,
        )

        query_job.result()

        affected_rows = int(
            query_job.num_dml_affected_rows or 0
        )

        if affected_rows != 1:
            raise ValueError(
                "Unable to reset remediation approval state "
                "for the tenant-scoped recommendation."
            )

    @staticmethod
    def _validate_result_tenant(
        *,
        organization_id: str,
        result: QualityProfileResult,
    ) -> None:

        if (
            result.organization_id
            != organization_id
        ):
            raise ValueError(
                "DQ profile result does not "
                "belong to the authenticated "
                "organization."
            )

        for finding in result.findings:
            if (
                finding.organization_id
                != organization_id
            ):
                raise ValueError(
                    "DQ profile contains a "
                    "cross-tenant finding."
                )

        for score in result.record_scores:
            if (
                score.organization_id
                != organization_id
            ):
                raise ValueError(
                    "DQ profile contains a "
                    "cross-tenant record score."
                )

        for execution in result.rule_executions:
            if (
                execution.organization_id
                != organization_id
            ):
                raise ValueError(
                    "DQ profile contains a "
                    "cross-tenant rule execution."
                )

    # ---------------------------------------------------------
    # Derived scoring
    # ---------------------------------------------------------

    @staticmethod
    def _calculate_automation_readiness(
        result: QualityProfileResult,
    ) -> float:

        health = float(
            result.avg_record_score
        )

        severe_penalty = (
            result.critical_findings * 3
            + result.high_findings * 1.5
        )

        if result.total_records > 0:
            severe_penalty = (
                severe_penalty
                / result.total_records
            )

        below_threshold_rate = (
            (
                result.records_below_threshold
                / result.total_records
            )
            if result.total_records
            else 0.0
        )

        readiness = (
            health
            - min(
                20.0,
                severe_penalty,
            )
            - (
                below_threshold_rate
                * 20.0
            )
        )

        return round(
            max(
                0.0,
                min(
                    100.0,
                    readiness,
                ),
            ),
            2,
        )
