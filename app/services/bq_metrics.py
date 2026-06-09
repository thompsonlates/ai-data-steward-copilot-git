import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.cloud import bigquery


class BigQueryMetrics:
    def __init__(
        self,
        project_id: Optional[str] = None,
        dataset: Optional[str] = None,
        explain_table: Optional[str] = None,
        feedback_table: Optional[str] = None,
        dq_dashboard_view: Optional[str] = None,
    ):
        self.project_id = (
            project_id
            or os.getenv("BQ_PROJECT_ID")
            or os.getenv("PROJECT_ID")
            or "api-project-503305938314"
        )
        self.dataset = (
            dataset
            or os.getenv("BQ_DATASET")
            or os.getenv("DATASET")
            or "ai_data_steward_mvp"
        )
        self.explain_table = (
            explain_table
            or os.getenv("BQ_EXPLAIN_TABLE")
            or "Match_Explanations_Log"
        )
        self.feedback_table = (
            feedback_table
            or os.getenv("BQ_FEEDBACK_TABLE")
            or "Match_Feedback_Events"
        )
        self.dq_dashboard_view = (
            dq_dashboard_view
            or os.getenv("BQ_DQ_DASHBOARD_VIEW")
            or "DQ_INTELLIGENCE_DASHBOARD_VW"
        )

        self.client = bigquery.Client(project=self.project_id)
        self.explain_table_id = (
            f"{self.project_id}.{self.dataset}.{self.explain_table}"
        )
        self.feedback_table_id = (
            f"{self.project_id}.{self.dataset}.{self.feedback_table}"
        )
        self.dq_dashboard_view_id = (
            f"{self.project_id}.{self.dataset}.{self.dq_dashboard_view}"
        )

        self._table_columns_cache: Dict[str, set[str]] = {}

    def _get_table_columns(self, table_id: str) -> set[str]:
        cached = self._table_columns_cache.get(table_id)
        if cached is not None:
            return cached

        table = self.client.get_table(table_id)
        cols = {field.name for field in table.schema}
        self._table_columns_cache[table_id] = cols
        return cols

    def _has_column(self, table_id: str, column_name: str) -> bool:
        return column_name in self._get_table_columns(table_id)

    def _explain_action_expr(self) -> str:
        if self._has_column(self.explain_table_id, "final_recommended_action"):
            return "COALESCE(e.final_recommended_action, e.recommended_action)"
        return "e.recommended_action"

    def _feedback_override_reason_expr(self, alias: str = "lf") -> str:
        feedback_cols = self._get_table_columns(self.feedback_table_id)

        candidates = []
        if "override_reason_code" in feedback_cols:
            candidates.append(f"NULLIF(CAST({alias}.override_reason_code AS STRING), '')")
        if "override_reason_note" in feedback_cols:
            candidates.append(f"NULLIF(CAST({alias}.override_reason_note AS STRING), '')")
        if "override_reason" in feedback_cols:
            candidates.append(f"NULLIF(CAST({alias}.override_reason AS STRING), '')")
        if "steward_override_reason" in feedback_cols:
            candidates.append(
                f"NULLIF(CAST({alias}.steward_override_reason AS STRING), '')"
            )

        if not candidates:
            return "'UNKNOWN_REASON'"

        return f"COALESCE({', '.join(candidates)}, 'UNKNOWN_REASON')"

    def _feedback_override_flag_expr(self, alias: str = "lf") -> str:
        feedback_cols = self._get_table_columns(self.feedback_table_id)

        if "steward_override_flag" in feedback_cols:
            return f"""
            CASE
              WHEN CAST({alias}.steward_override_flag AS STRING) IN ('Y', 'y', 'TRUE', 'true', '1') THEN TRUE
              WHEN CAST({alias}.steward_override_flag AS STRING) IN ('N', 'n', 'FALSE', 'false', '0') THEN FALSE
              ELSE SAFE_CAST({alias}.steward_override_flag AS BOOL)
            END
            """

        return "FALSE"

    def _feedback_timestamp_expr(self, alias: str = "f") -> str:
        feedback_cols = self._get_table_columns(self.feedback_table_id)

        if "feedback_at" in feedback_cols:
            return f"TIMESTAMP({alias}.feedback_at)"
        if "submitted_at" in feedback_cols:
            return f"TIMESTAMP({alias}.submitted_at)"

        return "CURRENT_TIMESTAMP()"

    def get_dq_dashboard_overview(
        self,
        days: int = 30,
        domain: Optional[str] = None,
    ) -> Dict[str, Any]:
        query = f"""
        SELECT
          metric_date,
          domain,
          total_records,
          avg_record_score,
          records_below_threshold,
          records_below_threshold_rate,
          dq_health_score,
          dq_risk_score,
          automation_readiness_score,
          avg_completeness_score,
          avg_validity_score,
          avg_standardization_score,
          avg_consistency_score,
          avg_uniqueness_score,
          total_findings,
          critical_findings,
          high_findings,
          medium_findings,
          low_findings,
          open_findings_count,
          accepted_findings_count,
          resolved_findings_count,
          waived_findings_count,
          records_with_findings,
          failed_rule_count,
          duplicate_record_count,
          scored_critical_issue_count,
          scored_high_issue_count,
          scored_medium_issue_count,
          scored_low_issue_count,
          scored_total_issue_count,
          records_flagged_by_ai,
          ai_recommendations_generated,
          open_ai_recommendations,
          accepted_ai_recommendations,
          rejected_ai_recommendations,
          implemented_ai_recommendations,
          high_priority_recommendations,
          medium_priority_recommendations,
          low_priority_recommendations,
          avg_ai_recommendation_confidence,
          ai_rules_flagged_count,
          steward_actions_taken,
          automated_fixes_applied,
          scored_record_count,
          min_record_score,
          max_record_score,
          very_low_score_count,
          low_score_count,
          medium_score_count,
          high_score_count,
          total_rules_executed,
          rules_triggered,
          configured_rule_count,
          active_rule_count,
          inactive_rule_count,
          configured_critical_rules,
          configured_high_rules,
          configured_medium_rules,
          configured_low_rules,
          validity_rule_count,
          completeness_rule_count,
          standardization_rule_count,
          uniqueness_rule_count,
          consistency_rule_count,
          avg_rule_weight,
          findings_per_record_rate,
          duplicate_rate,
          ai_flagged_record_rate,
          automated_fix_rate,
          steward_action_rate,
          findings_resolution_rate,
          ai_recommendation_acceptance_rate,
          ai_recommendation_implementation_rate,
          impacted_record_rate,
          summary_created_at
        FROM `{self.dq_dashboard_view_id}`
        WHERE metric_date >= DATE_SUB(CURRENT_DATE(), INTERVAL @days DAY)
          AND (@domain IS NULL OR domain = @domain)
        ORDER BY metric_date DESC, domain
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("days", "INT64", days),
                bigquery.ScalarQueryParameter("domain", "STRING", domain),
            ]
        )

        query_job = self.client.query(query, job_config=job_config)
        results = query_job.result()

        rows: List[Dict[str, Any]] = []
        for row in results:
            item = dict(row.items())

            if item.get("summary_created_at") is not None:
                item["summary_created_at"] = item["summary_created_at"].isoformat()

            for key, value in list(item.items()):
                if hasattr(value, "item"):
                    item[key] = value.item()

            rows.append(item)

        latest = rows[0] if rows else None

        return {
            "days": days,
            "domain": domain,
            "rows": rows,
            "latest": latest,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def overview(self, days: int) -> Dict[str, Any]:
        action_expr = self._explain_action_expr()
        feedback_reason_expr = self._feedback_override_reason_expr("lf")
        feedback_flag_expr = self._feedback_override_flag_expr("lf")
        feedback_ts_expr = self._feedback_timestamp_expr("f")

        query = f"""
        WITH base_explain AS (
          SELECT *
          FROM `{self.explain_table_id}`
          WHERE created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
        ),

        base_explain_bucketed AS (
          SELECT
            e.*,
            {action_expr} AS effective_recommended_action,
            CASE
              WHEN e.match_score < 0.80 THEN '0.00–0.79'
              WHEN e.match_score < 0.90 THEN '0.80–0.89'
              WHEN e.match_score < 0.95 THEN '0.90–0.94'
              WHEN e.match_score < 0.98 THEN '0.95–0.97'
              ELSE '0.98–1.00'
            END AS score_bucket
          FROM base_explain e
        ),

        base_feedback AS (
          SELECT *
          FROM `{self.feedback_table_id}` f
          WHERE {feedback_ts_expr.replace('f.', '')} >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
        ),

        latest_feedback AS (
          SELECT AS VALUE
            ARRAY_AGG(f ORDER BY {self._feedback_timestamp_expr("f")} DESC LIMIT 1)[OFFSET(0)]
          FROM base_feedback f
          GROUP BY f.explanation_id
        ),

        reviewed AS (
          SELECT
            e.explanation_id,
            e.match_score,
            e.score_bucket,
            e.ai_decision,
            e.risk_flag,
            e.effective_recommended_action AS recommended_action,
            e.ai_confidence,
            e.created_at,
            e.triggered_rules,
            lf.steward_decision,
            {feedback_reason_expr} AS override_reason,
            lf.steward_user,
            {feedback_flag_expr} AS steward_override_flag,
            {self._feedback_timestamp_expr("lf")} AS feedback_ts
          FROM base_explain_bucketed e
          JOIN latest_feedback lf
            ON lf.explanation_id = e.explanation_id
        ),

        decision_counts AS (
          SELECT ai_decision AS k, COUNT(1) AS v
          FROM base_explain_bucketed
          GROUP BY ai_decision
        ),

        risk_counts AS (
          SELECT risk_flag AS k, COUNT(1) AS v
          FROM base_explain_bucketed
          GROUP BY risk_flag
        ),

        action_counts AS (
          SELECT effective_recommended_action AS k, COUNT(1) AS v
          FROM base_explain_bucketed
          GROUP BY effective_recommended_action
        ),

        score_bucket_counts AS (
          SELECT score_bucket AS k, COUNT(1) AS v
          FROM base_explain_bucketed
          GROUP BY score_bucket
          ORDER BY k
        ),

        conf AS (
          SELECT AVG(ai_confidence) AS avg_conf
          FROM base_explain_bucketed
        ),

        overrides AS (
          SELECT
            SAFE_DIVIDE(
              SUM(CASE WHEN steward_override_flag THEN 1 ELSE 0 END),
              NULLIF(COUNT(1), 0)
            ) AS override_rate
          FROM reviewed
        ),

        latency AS (
          SELECT
            AVG(TIMESTAMP_DIFF(r.feedback_ts, r.created_at, MINUTE)) AS avg_minutes
          FROM reviewed r
        ),

        top_rules AS (
          SELECT rule AS name, COUNT(1) AS cnt
          FROM base_explain_bucketed, UNNEST(triggered_rules) AS rule
          GROUP BY rule
          ORDER BY cnt DESC
          LIMIT 10
        ),

        top_reasons AS (
          SELECT
            TRIM(LOWER(NULLIF(override_reason, ''))) AS name,
            COUNT(1) AS cnt
          FROM reviewed
          WHERE NULLIF(override_reason, '') IS NOT NULL
          GROUP BY name
          ORDER BY cnt DESC
          LIMIT 10
        ),

        norm_reviewed AS (
          SELECT
            *,
            CASE
              WHEN UPPER(ai_decision) IN ('AUTO_MERGE', 'APPROVE_MERGE', 'MATCH') THEN 'MATCH'
              WHEN UPPER(ai_decision) IN ('BLOCK_MERGE', 'REJECT_MERGE', 'NO_MATCH') THEN 'NO_MATCH'
              WHEN UPPER(ai_decision) IN ('REVIEW_REQUIRED', 'REVIEW') THEN 'REVIEW'
              ELSE 'REVIEW'
            END AS ai_norm,
            CASE
              WHEN steward_decision IS NULL THEN 'REVIEW'
              WHEN UPPER(steward_decision) IN ('AUTO_MERGE', 'APPROVE_MERGE', 'MATCH') THEN 'MATCH'
              WHEN UPPER(steward_decision) IN ('BLOCK_MERGE', 'REJECT_MERGE', 'NO_MATCH') THEN 'NO_MATCH'
              WHEN UPPER(steward_decision) IN ('REVIEW_REQUIRED', 'REVIEW') THEN 'REVIEW'
              ELSE 'REVIEW'
            END AS steward_norm
          FROM reviewed
        ),

        confusion AS (
          SELECT
            COUNT(1) AS total_reviewed,
            SUM(CASE WHEN ai_norm = 'MATCH' AND steward_norm = 'MATCH' THEN 1 ELSE 0 END) AS true_positives,
            SUM(CASE WHEN ai_norm = 'NO_MATCH' AND steward_norm = 'NO_MATCH' THEN 1 ELSE 0 END) AS true_negatives,
            SUM(CASE WHEN ai_norm = 'MATCH' AND steward_norm = 'NO_MATCH' THEN 1 ELSE 0 END) AS false_positives,
            SUM(CASE WHEN ai_norm = 'NO_MATCH' AND steward_norm = 'MATCH' THEN 1 ELSE 0 END) AS false_negatives,
            SUM(CASE WHEN ai_norm = 'REVIEW' OR steward_norm = 'REVIEW' THEN 1 ELSE 0 END) AS review_or_other
          FROM norm_reviewed
        ),

        rates AS (
          SELECT
            total_reviewed,
            true_positives,
            true_negatives,
            false_positives,
            false_negatives,
            review_or_other,
            SAFE_DIVIDE(false_positives, NULLIF(total_reviewed, 0)) AS fp_rate,
            SAFE_DIVIDE(false_negatives, NULLIF(total_reviewed, 0)) AS fn_rate,
            SAFE_DIVIDE(true_positives, NULLIF(true_positives + false_positives, 0)) AS precision,
            SAFE_DIVIDE(true_positives, NULLIF(true_positives + false_negatives, 0)) AS recall
          FROM confusion
        ),

        top_fp_rules AS (
          SELECT rule AS name, COUNT(1) AS cnt
          FROM norm_reviewed r, UNNEST(r.triggered_rules) AS rule
          WHERE r.ai_norm = 'MATCH' AND r.steward_norm = 'NO_MATCH'
          GROUP BY rule
          ORDER BY cnt DESC
          LIMIT 10
        ),

        top_fn_rules AS (
          SELECT rule AS name, COUNT(1) AS cnt
          FROM norm_reviewed r, UNNEST(r.triggered_rules) AS rule
          WHERE r.ai_norm = 'NO_MATCH' AND r.steward_norm = 'MATCH'
          GROUP BY rule
          ORDER BY cnt DESC
          LIMIT 10
        ),

        fpfn_by_bucket AS (
          SELECT
            score_bucket AS bucket,
            COUNT(1) AS reviewed,
            SUM(CASE WHEN ai_norm = 'MATCH' AND steward_norm = 'NO_MATCH' THEN 1 ELSE 0 END) AS false_positives,
            SUM(CASE WHEN ai_norm = 'NO_MATCH' AND steward_norm = 'MATCH' THEN 1 ELSE 0 END) AS false_negatives,
            SAFE_DIVIDE(
              SUM(CASE WHEN ai_norm = 'MATCH' AND steward_norm = 'NO_MATCH' THEN 1 ELSE 0 END),
              NULLIF(COUNT(1), 0)
            ) AS fp_rate,
            SAFE_DIVIDE(
              SUM(CASE WHEN ai_norm = 'NO_MATCH' AND steward_norm = 'MATCH' THEN 1 ELSE 0 END),
              NULLIF(COUNT(1), 0)
            ) AS fn_rate
          FROM norm_reviewed
          GROUP BY bucket
          ORDER BY bucket
        ),

        latest_learning_timeline AS (
          SELECT
            e.explanation_id,
            e.ai_decision,
            e.effective_recommended_action AS recommended_action,
            lf.steward_decision,
            {feedback_flag_expr} AS steward_override_flag,
            {feedback_reason_expr} AS override_reason,
            lf.steward_user,
            {self._feedback_timestamp_expr("lf")} AS feedback_at
          FROM latest_feedback lf
          JOIN base_explain_bucketed e
            ON e.explanation_id = lf.explanation_id
          ORDER BY {self._feedback_timestamp_expr("lf")} DESC
          LIMIT 7
        )

        SELECT
          (SELECT COUNT(1) FROM base_explain_bucketed) AS total_explanations,
          (SELECT avg_conf FROM conf) AS avg_ai_confidence,
          (SELECT override_rate FROM overrides) AS override_rate,
          (SELECT avg_minutes FROM latency) AS avg_time_to_feedback_minutes,

          (SELECT ARRAY_AGG(STRUCT(k, v)) FROM decision_counts) AS decisions_kv,
          (SELECT ARRAY_AGG(STRUCT(k, v)) FROM risk_counts) AS risks_kv,
          (SELECT ARRAY_AGG(STRUCT(k, v)) FROM action_counts) AS actions_kv,
          (SELECT ARRAY_AGG(STRUCT(k, v)) FROM score_bucket_counts) AS score_buckets_kv,

          (SELECT ARRAY_AGG(STRUCT(name, cnt)) FROM top_rules) AS top_triggered_rules,
          (SELECT ARRAY_AGG(STRUCT(name, cnt)) FROM top_reasons) AS top_override_reasons,

          (SELECT total_reviewed FROM rates) AS total_reviewed,
          (SELECT true_positives FROM rates) AS true_positives,
          (SELECT true_negatives FROM rates) AS true_negatives,
          (SELECT false_positives FROM rates) AS false_positives,
          (SELECT false_negatives FROM rates) AS false_negatives,
          (SELECT review_or_other FROM rates) AS review_or_other,
          (SELECT fp_rate FROM rates) AS fp_rate,
          (SELECT fn_rate FROM rates) AS fn_rate,
          (SELECT precision FROM rates) AS precision,
          (SELECT recall FROM rates) AS recall,

          (SELECT ARRAY_AGG(STRUCT(name, cnt)) FROM top_fp_rules) AS top_fp_rules,
          (SELECT ARRAY_AGG(STRUCT(name, cnt)) FROM top_fn_rules) AS top_fn_rules,

          (
            SELECT ARRAY_AGG(
              STRUCT(bucket, reviewed, false_positives, false_negatives, fp_rate, fn_rate)
            )
            FROM fpfn_by_bucket
          ) AS fpfn_by_score_bucket,

          (
            SELECT ARRAY_AGG(
              STRUCT(
                explanation_id,
                ai_decision,
                recommended_action,
                steward_decision,
                steward_override_flag,
                override_reason,
                steward_user,
                feedback_at
              )
            )
            FROM latest_learning_timeline
          ) AS learning_timeline
        """

        job = self.client.query(
            query,
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("days", "INT64", days)
                ]
            ),
        )
        rows = [dict(r) for r in job.result()]
        r = rows[0] if rows else {}

        def kv_to_dict(kv_list):
            out: Dict[str, int] = {}
            for item in kv_list or []:
                k = item.get("k")
                v = item.get("v")
                if k is not None:
                    out[str(k)] = int(v or 0)
            return out

        def to_topitems(items):
            out = []
            for it in items or []:
                name = it.get("name")
                cnt = it.get("cnt")
                if name is None:
                    continue
                out.append({"name": str(name), "count": int(cnt or 0)})
            return out

        def to_learning_timeline(items):
            out = []
            for it in items or []:
                out.append(
                    {
                        "explanation_id": str(it.get("explanation_id") or ""),
                        "ai_decision": str(it.get("ai_decision") or ""),
                        "recommended_action": str(it.get("recommended_action") or ""),
                        "steward_decision": str(it.get("steward_decision") or ""),
                        "steward_override_flag": str(it.get("steward_override_flag") or ""),
                        "override_reason": str(it.get("override_reason") or ""),
                        "steward_user": str(it.get("steward_user") or ""),
                        "feedback_at": str(it.get("feedback_at") or ""),
                    }
                )
            return out

        def to_bucket_rows(items):
            out = []
            for it in items or []:
                bucket = it.get("bucket")
                if bucket is None:
                    continue
                out.append(
                    {
                        "bucket": str(bucket),
                        "reviewed": int(it.get("reviewed") or 0),
                        "false_positives": int(it.get("false_positives") or 0),
                        "false_negatives": int(it.get("false_negatives") or 0),
                        "fp_rate": float(it["fp_rate"]) if it.get("fp_rate") is not None else None,
                        "fn_rate": float(it["fn_rate"]) if it.get("fn_rate") is not None else None,
                    }
                )
            return out

        def f_or_none(x):
            return float(x) if x is not None else None

        def i0(x):
            return int(x or 0)

        return {
            "total_explanations": i0(r.get("total_explanations")),
            "avg_ai_confidence": f_or_none(r.get("avg_ai_confidence")),
            "override_rate": f_or_none(r.get("override_rate")),
            "avg_time_to_feedback_minutes": f_or_none(
                r.get("avg_time_to_feedback_minutes")
            ),
            "decisions": kv_to_dict(r.get("decisions_kv")),
            "risk_flags": kv_to_dict(r.get("risks_kv")),
            "recommended_actions": kv_to_dict(r.get("actions_kv")),
            "score_buckets": kv_to_dict(r.get("score_buckets_kv")),
            "top_triggered_rules": to_topitems(r.get("top_triggered_rules")),
            "top_override_reasons": to_topitems(r.get("top_override_reasons")),
            "total_reviewed": i0(r.get("total_reviewed")),
            "true_positives": i0(r.get("true_positives")),
            "true_negatives": i0(r.get("true_negatives")),
            "false_positives": i0(r.get("false_positives")),
            "false_negatives": i0(r.get("false_negatives")),
            "review_or_other": i0(r.get("review_or_other")),
            "fp_rate": f_or_none(r.get("fp_rate")),
            "fn_rate": f_or_none(r.get("fn_rate")),
            "precision": f_or_none(r.get("precision")),
            "recall": f_or_none(r.get("recall")),
            "top_fp_rules": to_topitems(r.get("top_fp_rules")),
            "top_fn_rules": to_topitems(r.get("top_fn_rules")),
            "fpfn_by_score_bucket": to_bucket_rows(r.get("fpfn_by_score_bucket")),
            "learning_timeline": to_learning_timeline(r.get("learning_timeline")),
        }

    def get_recommended_action(self, explanation_id: str) -> Optional[str]:
        action_expr = (
            "COALESCE(final_recommended_action, recommended_action)"
            if self._has_column(self.explain_table_id, "final_recommended_action")
            else "recommended_action"
        )

        query = f"""
        SELECT {action_expr} AS recommended_action
        FROM `{self.explain_table_id}`
        WHERE explanation_id = @explanation_id
        ORDER BY created_at DESC
        LIMIT 1
        """
        job = self.client.query(
            query,
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter(
                        "explanation_id", "STRING", explanation_id
                    )
                ]
            ),
        )
        rows = list(job.result())
        if not rows:
            return None
        return rows[0]["recommended_action"]

    def _to_plain(self, value):
        if value is None:
            return None
        if isinstance(value, list):
            return [self._to_plain(v) for v in value]
        if isinstance(value, tuple):
            return [self._to_plain(v) for v in value]
        if hasattr(value, "keys"):
            return {k: self._to_plain(value[k]) for k in value.keys()}
        return value

    def get_override_learning_context(
        self,
        domain: str,
        triggered_rules: list[str],
        match_score: float | None = None,
        record_a_source_system: str | None = None,
        record_b_source_system: str | None = None,
        limit: int = 5,
    ) -> dict:
        empty_result = {
            "summary_patterns": [],
            "score_band_patterns": [],
            "source_system_patterns": [],
            "decision_patterns": [],
            "precedent_cases": [],
        }

        if not triggered_rules:
            return empty_result

        source_pair_1 = None
        source_pair_2 = None
        if record_a_source_system and record_b_source_system:
            source_pair_1 = f"{record_a_source_system}|{record_b_source_system}"
            source_pair_2 = f"{record_b_source_system}|{record_a_source_system}"

        action_expr = self._explain_action_expr()
        feedback_reason_expr = self._feedback_override_reason_expr("lf")
        feedback_flag_expr = self._feedback_override_flag_expr("lf")
        feedback_ts_expr = self._feedback_timestamp_expr("f")

        query = f"""
        WITH latest_feedback AS (
          SELECT AS VALUE
            ARRAY_AGG(f ORDER BY {feedback_ts_expr} DESC LIMIT 1)[OFFSET(0)]
          FROM `{self.feedback_table_id}` f
          GROUP BY f.explanation_id
        ),

        base AS (
          SELECT
            e.explanation_id,
            e.domain,
            e.record_a_id,
            e.record_b_id,
            e.record_a_source_system,
            e.record_b_source_system,
            e.match_score,
            e.ai_decision,
            e.ai_confidence,
            e.risk_flag,
            {action_expr} AS recommended_action,
            e.triggered_rules,
            e.created_at,
            lf.steward_decision,
            {feedback_flag_expr} AS steward_override_flag,
            {feedback_reason_expr} AS override_reason,
            CONCAT(
              COALESCE(e.record_a_source_system, 'UNKNOWN'),
              '|',
              COALESCE(e.record_b_source_system, 'UNKNOWN')
            ) AS source_pair
          FROM `{self.explain_table_id}` e
          JOIN latest_feedback lf
            ON lf.explanation_id = e.explanation_id
          WHERE e.domain = @domain
            AND EXISTS (
              SELECT 1
              FROM UNNEST(e.triggered_rules) rule
              WHERE rule IN UNNEST(@triggered_rules)
            )
        ),

        filtered AS (
          SELECT
            *,
            CASE
              WHEN match_score < 0.80 THEN '0.00-0.79'
              WHEN match_score < 0.90 THEN '0.80-0.89'
              WHEN match_score < 0.95 THEN '0.90-0.94'
              WHEN match_score < 0.98 THEN '0.95-0.97'
              ELSE '0.98-1.00'
            END AS score_band
          FROM base
          WHERE @match_score IS NULL
             OR ABS(match_score - @match_score) <= 0.05
             OR (
                  CASE
                    WHEN match_score < 0.80 THEN '0.00-0.79'
                    WHEN match_score < 0.90 THEN '0.80-0.89'
                    WHEN match_score < 0.95 THEN '0.90-0.94'
                    WHEN match_score < 0.98 THEN '0.95-0.97'
                    ELSE '0.98-1.00'
                  END
                ) = (
                  CASE
                    WHEN @match_score < 0.80 THEN '0.00-0.79'
                    WHEN @match_score < 0.90 THEN '0.80-0.89'
                    WHEN @match_score < 0.95 THEN '0.90-0.94'
                    WHEN @match_score < 0.98 THEN '0.95-0.97'
                    ELSE '0.98-1.00'
                  END
                )
        ),

        summary_patterns AS (
          SELECT
            rule AS triggered_rule,
            override_reason AS override_reason_code,
            COUNT(1) AS override_count
          FROM filtered, UNNEST(triggered_rules) AS rule
          WHERE steward_override_flag = TRUE
          GROUP BY triggered_rule, override_reason_code
          ORDER BY override_count DESC
          LIMIT @limit
        ),

        score_band_patterns AS (
          SELECT
            score_band,
            steward_decision,
            COUNT(1) AS decision_count,
            AVG(match_score) AS avg_match_score,
            AVG(ai_confidence) AS avg_ai_confidence
          FROM filtered
          GROUP BY score_band, steward_decision
          ORDER BY decision_count DESC
          LIMIT @limit
        ),

        source_system_patterns AS (
          SELECT
            source_pair,
            steward_decision,
            COUNT(1) AS decision_count,
            AVG(match_score) AS avg_match_score
          FROM filtered
          WHERE @source_pair_1 IS NULL
             OR source_pair IN (@source_pair_1, @source_pair_2)
          GROUP BY source_pair, steward_decision
          ORDER BY decision_count DESC
          LIMIT @limit
        ),

        decision_patterns AS (
          SELECT
            ai_decision,
            steward_decision,
            COUNT(1) AS pattern_count,
            AVG(match_score) AS avg_match_score,
            AVG(ai_confidence) AS avg_ai_confidence
          FROM filtered
          GROUP BY ai_decision, steward_decision
          ORDER BY pattern_count DESC
          LIMIT @limit
        ),

        precedent_cases AS (
          SELECT
            explanation_id,
            record_a_id,
            record_b_id,
            record_a_source_system,
            record_b_source_system,
            match_score,
            ai_decision,
            ai_confidence,
            risk_flag,
            recommended_action,
            steward_decision,
            steward_override_flag,
            override_reason,
            triggered_rules,
            created_at
          FROM filtered
          ORDER BY
            ABS(match_score - COALESCE(@match_score, match_score)) ASC,
            created_at DESC
          LIMIT @limit
        )

        SELECT
          (SELECT ARRAY_AGG(STRUCT(triggered_rule, override_reason_code, override_count)) FROM summary_patterns) AS summary_patterns,
          (SELECT ARRAY_AGG(STRUCT(score_band, steward_decision, decision_count, avg_match_score, avg_ai_confidence)) FROM score_band_patterns) AS score_band_patterns,
          (SELECT ARRAY_AGG(STRUCT(source_pair, steward_decision, decision_count, avg_match_score)) FROM source_system_patterns) AS source_system_patterns,
          (SELECT ARRAY_AGG(STRUCT(ai_decision, steward_decision, pattern_count, avg_match_score, avg_ai_confidence)) FROM decision_patterns) AS decision_patterns,
          (SELECT ARRAY_AGG(STRUCT(
              explanation_id,
              record_a_id,
              record_b_id,
              record_a_source_system,
              record_b_source_system,
              match_score,
              ai_decision,
              ai_confidence,
              risk_flag,
              recommended_action,
              steward_decision,
              steward_override_flag,
              override_reason,
              triggered_rules,
              created_at
          )) FROM precedent_cases) AS precedent_cases
        """

        job = self.client.query(
            query,
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("domain", "STRING", domain),
                    bigquery.ArrayQueryParameter(
                        "triggered_rules", "STRING", triggered_rules
                    ),
                    bigquery.ScalarQueryParameter("match_score", "FLOAT64", match_score),
                    bigquery.ScalarQueryParameter(
                        "source_pair_1", "STRING", source_pair_1
                    ),
                    bigquery.ScalarQueryParameter(
                        "source_pair_2", "STRING", source_pair_2
                    ),
                    bigquery.ScalarQueryParameter("limit", "INT64", limit),
                ]
            ),
        )

        rows = [dict(row) for row in job.result()]
        result = rows[0] if rows else {}

        return {
          "summary_patterns": self._to_plain(result.get("summary_patterns") or []),
          "score_band_patterns": self._to_plain(result.get("score_band_patterns") or []),
          "source_system_patterns": self._to_plain(result.get("source_system_patterns") or []),
          "decision_patterns": self._to_plain(result.get("decision_patterns") or []),
          "precedent_cases": self._to_plain(result.get("precedent_cases") or []),
        }