from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from google.cloud import bigquery

PROJECT_ID = "api-project-503305938314"
DATASET = "ai_data_steward_mvp"

EXPLAIN_TABLE = "Match_Explanations_Log"
FEEDBACK_TABLE = "Match_Feedback_Events"


class BigQueryLogger:
    def __init__(self):
        self.client = bigquery.Client(project=PROJECT_ID)
        self.explain_table_id = f"{PROJECT_ID}.{DATASET}.{EXPLAIN_TABLE}"
        self.feedback_table_id = f"{PROJECT_ID}.{DATASET}.{FEEDBACK_TABLE}"
        self._table_columns_cache: dict[str, set[str]] = {}

    def _get_table_columns(self, table_id: str) -> set[str]:
        cached = self._table_columns_cache.get(table_id)
        if cached is not None:
            return cached

        table = self.client.get_table(table_id)
        columns = {field.name for field in table.schema}
        self._table_columns_cache[table_id] = columns
        return columns

    def _filter_row_to_table_schema(
        self,
        table_id: str,
        row: dict[str, Any],
    ) -> dict[str, Any]:
        valid_columns = self._get_table_columns(table_id)
        return {k: v for k, v in row.items() if k in valid_columns}

    def _normalize_override_flag(self, value: Any) -> Optional[str]:
        if value is None:
            return None

        if isinstance(value, bool):
            return "Y" if value else "N"

        text = str(value).strip().upper()
        if text in {"Y", "YES", "TRUE", "1"}:
            return "Y"
        if text in {"N", "NO", "FALSE", "0"}:
            return "N"

        return None

    def log_explanation(self, row: dict[str, Any]) -> None:
        """
        Append-only insert for explanation events.
        Filters payload to the current BigQuery table schema so new app fields
        do not break inserts before the table is updated.
        """
        safe_row = dict(row)

        if "created_at" not in safe_row or safe_row["created_at"] is None:
            safe_row["created_at"] = datetime.now(timezone.utc).isoformat()

        safe_row = self._filter_row_to_table_schema(
            self.explain_table_id,
            safe_row,
        )

        errors = self.client.insert_rows_json(self.explain_table_id, [safe_row])
        if errors:
            raise Exception(f"BigQuery explanation insert failed: {errors}")

    def log_feedback_event(
        self,
        explanation_id: str,
        steward_decision: str,
        steward_user: str,
        steward_override_flag: Any,
        request_id: Optional[str] = None,
        decision_id: Optional[str] = None,
        domain: Optional[str] = None,
        policy_version: Optional[str] = None,
        override_reason_code: Optional[str] = None,
        override_reason_note: Optional[str] = None,
        submitted_at: Optional[str] = None,
        audit_packet_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Append-only insert for steward decision / feedback events.
        """
        feedback_at = submitted_at or datetime.now(timezone.utc).isoformat()

        row = {
            "feedback_id": f"fb_{uuid.uuid4().hex[:12]}",
            "decision_id": decision_id,
            "request_id": request_id,
            "audit_packet_id": audit_packet_id,
            "domain": domain,
            "policy_version": policy_version,
            "explanation_id": explanation_id,
            "steward_user": steward_user,
            "steward_decision": steward_decision,
            "steward_override_flag": self._normalize_override_flag(
                steward_override_flag
            ),
            "override_reason_code": override_reason_code,
            "override_reason_note": override_reason_note,
            "feedback_at": feedback_at,
            "submitted_at": feedback_at,
        }

        safe_row = self._filter_row_to_table_schema(
            self.feedback_table_id,
            row,
        )

        errors = self.client.insert_rows_json(self.feedback_table_id, [safe_row])
        if errors:
            raise Exception(f"BigQuery feedback insert failed: {errors}")

        return safe_row

    def update_explanation_with_feedback(
        self,
        explanation_id: str,
        steward_decision: str,
        steward_user: str,
        steward_override_flag: Any,
        feedback_at: str,
        override_reason_code: Optional[str] = None,
        override_reason_note: Optional[str] = None,
        decision_id: Optional[str] = None,
        request_id: Optional[str] = None,
        domain: Optional[str] = None,
        policy_version: Optional[str] = None,
        audit_packet_id: Optional[str] = None,
    ) -> None:
        """
        Optional helper if you ever choose to denormalize feedback back into
        Match_Explanations_Log. Not required for your current join-based metrics approach.
        """
        normalized_override_flag = self._normalize_override_flag(
            steward_override_flag
        )

        sql = f"""
        MERGE `{self.explain_table_id}` T
        USING (
          SELECT
            @explanation_id AS explanation_id,
            @steward_decision AS steward_decision,
            @override_reason_code AS override_reason_code,
            @override_reason_note AS override_reason_note,
            @steward_user AS steward_user,
            @steward_override_flag AS steward_override_flag,
            @decision_id AS decision_id,
            @request_id AS request_id,
            @domain AS domain,
            @policy_version AS policy_version,
            @audit_packet_id AS audit_packet_id,
            TIMESTAMP(@feedback_at) AS feedback_at
        ) S
        ON T.explanation_id = S.explanation_id
        WHEN MATCHED THEN
          UPDATE SET
            steward_decision = S.steward_decision,
            override_reason_code = S.override_reason_code,
            override_reason_note = S.override_reason_note,
            steward_user = S.steward_user,
            steward_override_flag = S.steward_override_flag,
            decision_id = S.decision_id,
            request_id = COALESCE(T.request_id, S.request_id),
            domain = COALESCE(T.domain, S.domain),
            policy_version = COALESCE(T.policy_version, S.policy_version),
            audit_packet_id = COALESCE(T.audit_packet_id, S.audit_packet_id),
            feedback_at = S.feedback_at
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "explanation_id",
                    "STRING",
                    explanation_id,
                ),
                bigquery.ScalarQueryParameter(
                    "steward_decision",
                    "STRING",
                    steward_decision,
                ),
                bigquery.ScalarQueryParameter(
                    "override_reason_code",
                    "STRING",
                    override_reason_code,
                ),
                bigquery.ScalarQueryParameter(
                    "override_reason_note",
                    "STRING",
                    override_reason_note,
                ),
                bigquery.ScalarQueryParameter(
                    "steward_user",
                    "STRING",
                    steward_user,
                ),
                bigquery.ScalarQueryParameter(
                    "steward_override_flag",
                    "STRING",
                    normalized_override_flag,
                ),
                bigquery.ScalarQueryParameter(
                    "decision_id",
                    "STRING",
                    decision_id,
                ),
                bigquery.ScalarQueryParameter(
                    "request_id",
                    "STRING",
                    request_id,
                ),
                bigquery.ScalarQueryParameter(
                    "domain",
                    "STRING",
                    domain,
                ),
                bigquery.ScalarQueryParameter(
                    "policy_version",
                    "STRING",
                    policy_version,
                ),
                bigquery.ScalarQueryParameter(
                    "audit_packet_id",
                    "STRING",
                    audit_packet_id,
                ),
                bigquery.ScalarQueryParameter(
                    "feedback_at",
                    "STRING",
                    feedback_at,
                ),
            ]
        )

        self.client.query(sql, job_config=job_config).result()