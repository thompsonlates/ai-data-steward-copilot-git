from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from google.cloud import bigquery

from app.services.quality_profiler_service import (
    QualityFinding,
    QualityProfileResult,
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

    # ---------------------------------------------------------
    # Main persistence entry point
    # ---------------------------------------------------------

    def save_profile_result(
        self,
        *,
        result: QualityProfileResult,
    ) -> None:

        organization_id = (
            self._require_organization_id(
                result.organization_id
            )
        )

        self._validate_result_tenant(
            organization_id=organization_id,
            result=result,
        )

        logger.info(
            "Persisting DQ profile result. "
            "organization_id=%s "
            "profile_run_id=%s "
            "domain=%s "
            "source_table=%s",
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

        self.save_daily_summary(
            organization_id=organization_id,
            result=result,
        )

        logger.info(
            "DQ profile persistence complete. "
            "organization_id=%s "
            "profile_run_id=%s "
            "findings=%s "
            "record_scores=%s",
            organization_id,
            result.profile_run_id,
            len(result.findings),
            len(result.record_scores),
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

        organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        rows = list(findings)

        if not rows:
            return

        for finding in rows:
            if (
                finding.organization_id
                != organization_id
            ):
                raise ValueError(
                    "DQ finding belongs to a "
                    "different organization."
                )

        sql = f"""
        MERGE `{self.findings_table_id}` AS target

        USING (
            SELECT
                @finding_id AS finding_id,
                @organization_id AS organization_id,
                @profile_run_id AS profile_run_id,
                @source_table AS source_table,
                @source_row_id AS source_row_id,
                @domain AS domain,
                @record_id AS record_id,
                @field_name AS field_name,
                @rule_id AS rule_id,
                @dimension AS dimension,
                @rule_category AS rule_category,
                @rule_type AS rule_type,
                @severity AS severity,
                @status AS status,
                @rule_weight AS rule_weight,
                @finding_message AS finding_message,
                @observed_value AS observed_value,
                @created_at AS created_at
        ) AS source

        ON target.organization_id =
               source.organization_id
           AND target.finding_id =
               source.finding_id

        WHEN MATCHED THEN
          UPDATE SET
            profile_run_id =
                source.profile_run_id,
            source_table =
                source.source_table,
            source_row_id =
                source.source_row_id,
            domain =
                source.domain,
            record_id =
                source.record_id,
            field_name =
                source.field_name,
            rule_id =
                source.rule_id,
            dimension =
                source.dimension,
            rule_category =
                source.rule_category,
            rule_type =
                source.rule_type,
            severity =
                source.severity,
            status =
                source.status,
            rule_weight =
                source.rule_weight,
            finding_message =
                source.finding_message,
            observed_value =
                source.observed_value

        WHEN NOT MATCHED THEN
          INSERT (
            finding_id,
            organization_id,
            profile_run_id,
            source_table,
            source_row_id,
            created_at,
            domain,
            record_id,
            field_name,
            rule_id,
            dimension,
            rule_category,
            rule_type,
            severity,
            status,
            rule_weight,
            finding_message,
            observed_value
          )
          VALUES (
            source.finding_id,
            source.organization_id,
            source.profile_run_id,
            source.source_table,
            source.source_row_id,
            source.created_at,
            source.domain,
            source.record_id,
            source.field_name,
            source.rule_id,
            source.dimension,
            source.rule_category,
            source.rule_type,
            source.severity,
            source.status,
            source.rule_weight,
            source.finding_message,
            source.observed_value
          )
        """

        for finding in rows:
            params = [
                bigquery.ScalarQueryParameter(
                    "finding_id",
                    "STRING",
                    finding.finding_id,
                ),
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "profile_run_id",
                    "STRING",
                    finding.profile_run_id,
                ),

                bigquery.ScalarQueryParameter(
                    "source_row_id",
                    "STRING",
                    finding.source_row_id,
                ),
                bigquery.ScalarQueryParameter(
                    "source_table",
                    "STRING",
                    finding.source_table,
                ),
                bigquery.ScalarQueryParameter(
                    "domain",
                    "STRING",
                    finding.domain,
                ),
                bigquery.ScalarQueryParameter(
                    "record_id",
                    "STRING",
                    finding.record_id,
                ),
                bigquery.ScalarQueryParameter(
                    "field_name",
                    "STRING",
                    finding.field_name,
                ),

                bigquery.ScalarQueryParameter(
                    "rule_id",
                    "STRING",
                    finding.rule_id,
                ),
                bigquery.ScalarQueryParameter(
                    "dimension",
                    "STRING",
                    finding.dimension,
                ),
                bigquery.ScalarQueryParameter(
                    "rule_category",
                    "STRING",
                    finding.dimension,
                ),
                bigquery.ScalarQueryParameter(
                    "rule_type",
                    "STRING",
                    "PROFILE_RULE",
                ),
                bigquery.ScalarQueryParameter(
                    "severity",
                    "STRING",
                    finding.severity,
                ),
                bigquery.ScalarQueryParameter(
                    "status",
                    "STRING",
                    "OPEN",
                ),
                bigquery.ScalarQueryParameter(
                    "rule_weight",
                    "FLOAT64",
                    1.0,
                ),
                bigquery.ScalarQueryParameter(
                    "finding_message",
                    "STRING",
                    finding.finding_message,
                ),
                bigquery.ScalarQueryParameter(
                    "observed_value",
                    "STRING",
                    finding.observed_value,
                ),
                bigquery.ScalarQueryParameter(
                    "created_at",
                    "TIMESTAMP",
                    datetime.now(
                        timezone.utc
                    ),
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

    def get_finding_aggregates(
        self,
        *,
        organization_id: str,
        profile_run_id: str,
    ) -> list[dict]:
        query = f"""
            SELECT
                rule_id,
                field_name,
                rule_category AS dimension,
                severity,
                COUNT(*) AS finding_count,
                COUNT(DISTINCT source_row_id)
                    AS affected_record_count
            FROM `{self.project_id}.{self.dataset_id}.DQ_FINDINGS`
            WHERE organization_id = @organization_id
            AND profile_run_id = @profile_run_id
            GROUP BY
                rule_id,
                field_name,
                dimension,
                severity
            ORDER BY finding_count DESC
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
                    profile_run_id,
                ),
            ]
        )

        rows = self.client.query(
            query,
            job_config=job_config,
        ).result()

        return [
            dict(row)
            for row in rows
        ]

    # ---------------------------------------------------------
    # Record scores
    # ---------------------------------------------------------

    def save_record_scores(
        self,
        *,
        organization_id: str,
        scores: Iterable[
            RecordQualityScore
        ],
    ) -> None:

        organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        rows = list(scores)

        if not rows:
            return

        for score in rows:
            if (
                score.organization_id
                != organization_id
            ):
                raise ValueError(
                    "DQ record score belongs to a "
                    "different organization."
                )

        sql = f"""
        MERGE `{self.record_score_table_id}` AS target

        USING (
            SELECT
                @organization_id AS organization_id,
                @profile_run_id AS profile_run_id,
                @source_table AS source_table,
                @source_row_id AS source_row_id,
                @domain AS domain,
                @record_id AS record_id
        ) AS source

        ON target.organization_id =
                source.organization_id
            AND target.profile_run_id =
                source.profile_run_id
            AND target.source_row_id =
                source.source_row_id

        WHEN MATCHED THEN
          UPDATE SET
            source_table =
                @source_table,
            record_score =
                @record_score,
            completeness_score =
                @completeness_score,
            source_row_id =
                @source_row_id,
            validity_score =
                @validity_score,
            standardization_score =
                @standardization_score,
            consistency_score =
                @consistency_score,
            uniqueness_score =
                @uniqueness_score,
            issue_count =
                @issue_count,
            critical_issue_count =
                @critical_issue_count,
            high_issue_count =
                @high_issue_count,
            medium_issue_count =
                @medium_issue_count,
            low_issue_count =
                @low_issue_count

        WHEN NOT MATCHED THEN
          INSERT (
            created_at,
            organization_id,
            profile_run_id,
            source_table,
            source_row_id,
            domain,
            record_id,
            record_score,
            completeness_score,
            validity_score,
            standardization_score,
            consistency_score,
            uniqueness_score,
            issue_count,
            critical_issue_count,
            high_issue_count,
            medium_issue_count,
            low_issue_count
          )
          VALUES (
            @created_at,
            @organization_id,
            @profile_run_id,
            @source_table,
            @source_row_id,
            @domain,
            @record_id,
            @record_score,
            @completeness_score,
            @validity_score,
            @standardization_score,
            @consistency_score,
            @uniqueness_score,
            @issue_count,
            @critical_issue_count,
            @high_issue_count,
            @medium_issue_count,
            @low_issue_count
          )
        """

        for score in rows:
            params = [
                bigquery.ScalarQueryParameter(
                    "created_at",
                    "TIMESTAMP",
                    datetime.now(
                        timezone.utc
                    ),
                ),
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "profile_run_id",
                    "STRING",
                    score.profile_run_id,
                ),
                bigquery.ScalarQueryParameter(
                    "source_table",
                    "STRING",
                    score.source_table,
                ),
                bigquery.ScalarQueryParameter(
                    "domain",
                    "STRING",
                    score.domain,
                ),
                bigquery.ScalarQueryParameter(
                    "record_id",
                    "STRING",
                    score.record_id,
                ),

                bigquery.ScalarQueryParameter(
                    "source_row_id",
                    "STRING",
                    score.source_row_id,
                ),
                bigquery.ScalarQueryParameter(
                    "record_score",
                    "FLOAT64",
                    score.overall_score,
                ),
                bigquery.ScalarQueryParameter(
                    "completeness_score",
                    "FLOAT64",
                    score.completeness_score,
                ),
                bigquery.ScalarQueryParameter(
                    "validity_score",
                    "FLOAT64",
                    score.validity_score,
                ),
                bigquery.ScalarQueryParameter(
                    "standardization_score",
                    "FLOAT64",
                    score.standardization_score,
                ),
                bigquery.ScalarQueryParameter(
                    "consistency_score",
                    "FLOAT64",
                    score.consistency_score,
                ),
                bigquery.ScalarQueryParameter(
                    "uniqueness_score",
                    "FLOAT64",
                    score.uniqueness_score,
                ),
                bigquery.ScalarQueryParameter(
                    "issue_count",
                    "INT64",
                    score.issue_count,
                ),
                bigquery.ScalarQueryParameter(
                    "critical_issue_count",
                    "INT64",
                    score.critical_issue_count,
                ),
                bigquery.ScalarQueryParameter(
                    "high_issue_count",
                    "INT64",
                    score.high_issue_count,
                ),
                bigquery.ScalarQueryParameter(
                    "medium_issue_count",
                    "INT64",
                    score.medium_issue_count,
                ),
                bigquery.ScalarQueryParameter(
                    "low_issue_count",
                    "INT64",
                    score.low_issue_count,
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
    # Daily summary
    # ---------------------------------------------------------

    def save_daily_summary(
        self,
        *,
        organization_id: str,
        result: QualityProfileResult,
    ) -> None:

        organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        if (
            result.organization_id
            != organization_id
        ):
            raise ValueError(
                "DQ summary belongs to a "
                "different organization."
            )

        total_rules_executed = (
            len({
                finding.rule_id
                for finding
                in result.findings
            })
        )

        failed_rule_count = (
            total_rules_executed
        )

        dq_health_score = (
            result.avg_record_score
        )

        dq_risk_score = round(
            max(
                0.0,
                min(
                    100.0,
                    100.0
                    - dq_health_score,
                ),
            ),
            2,
        )

        automation_readiness_score = (
            self._calculate_automation_readiness(
                result
            )
        )

        sql = f"""
        MERGE `{self.daily_summary_table_id}` AS target

        USING (
            SELECT
                @organization_id AS organization_id,
                @metric_date AS metric_date,
                @domain AS domain,
                @profile_run_id AS profile_run_id,
                @source_table AS source_table
        ) AS source

        ON target.organization_id =
               source.organization_id
           AND target.metric_date =
               source.metric_date
           AND target.domain =
               source.domain
           AND target.profile_run_id =
               source.profile_run_id

        WHEN MATCHED THEN
          UPDATE SET
            source_table =
                @source_table,
            total_records =
                @total_records,
            scored_record_count =
                @scored_record_count,
            records_with_findings =
                @records_with_findings,
            avg_record_score =
                @avg_record_score,
            avg_completeness_score =
                @avg_completeness_score,
            avg_validity_score =
                @avg_validity_score,
            avg_standardization_score =
                @avg_standardization_score,
            avg_consistency_score =
                @avg_consistency_score,
            avg_uniqueness_score =
                @avg_uniqueness_score,
            records_below_threshold =
                @records_below_threshold,
            total_findings =
                @total_findings,
            critical_findings =
                @critical_findings,
            high_findings =
                @high_findings,
            medium_findings =
                @medium_findings,
            low_findings =
                @low_findings,
            total_rules_executed =
                @total_rules_executed,
            failed_rule_count =
                @failed_rule_count,
            duplicate_record_count =
                @duplicate_record_count,
            records_flagged_by_ai =
                @records_flagged_by_ai,
            ai_recommendations_generated =
                @ai_recommendations_generated,
            steward_actions_taken =
                @steward_actions_taken,
            automated_fixes_applied =
                @automated_fixes_applied,
            dq_health_score =
                @dq_health_score,
            dq_risk_score =
                @dq_risk_score,
            automation_readiness_score =
                @automation_readiness_score

        WHEN NOT MATCHED THEN
          INSERT (
            metric_date,
            organization_id,
            profile_run_id,
            source_table,
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

        now = datetime.now(
            timezone.utc
        )

        params = [
            bigquery.ScalarQueryParameter(
                "metric_date",
                "DATE",
                now.date(),
            ),
            bigquery.ScalarQueryParameter(
                "organization_id",
                "STRING",
                organization_id,
            ),
            bigquery.ScalarQueryParameter(
                "profile_run_id",
                "STRING",
                result.profile_run_id,
            ),
            bigquery.ScalarQueryParameter(
                "source_table",
                "STRING",
                result.source_table,
            ),
            bigquery.ScalarQueryParameter(
                "domain",
                "STRING",
                result.domain,
            ),
            bigquery.ScalarQueryParameter(
                "total_records",
                "INT64",
                result.total_records,
            ),
            bigquery.ScalarQueryParameter(
                "scored_record_count",
                "INT64",
                result.scored_record_count,
            ),
            bigquery.ScalarQueryParameter(
                "records_with_findings",
                "INT64",
                result.records_with_findings,
            ),
            bigquery.ScalarQueryParameter(
                "avg_record_score",
                "FLOAT64",
                result.avg_record_score,
            ),
            bigquery.ScalarQueryParameter(
                "avg_completeness_score",
                "FLOAT64",
                result.avg_completeness_score,
            ),
            bigquery.ScalarQueryParameter(
                "avg_validity_score",
                "FLOAT64",
                result.avg_validity_score,
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
                "total_findings",
                "INT64",
                result.total_findings,
            ),
            bigquery.ScalarQueryParameter(
                "critical_findings",
                "INT64",
                result.critical_findings,
            ),
            bigquery.ScalarQueryParameter(
                "high_findings",
                "INT64",
                result.high_findings,
            ),
            bigquery.ScalarQueryParameter(
                "medium_findings",
                "INT64",
                result.medium_findings,
            ),
            bigquery.ScalarQueryParameter(
                "low_findings",
                "INT64",
                result.low_findings,
            ),
            bigquery.ScalarQueryParameter(
                "total_rules_executed",
                "INT64",
                total_rules_executed,
            ),
            bigquery.ScalarQueryParameter(
                "failed_rule_count",
                "INT64",
                failed_rule_count,
            ),
            bigquery.ScalarQueryParameter(
                "duplicate_record_count",
                "INT64",
                result.duplicate_record_count,
            ),
            bigquery.ScalarQueryParameter(
                "records_flagged_by_ai",
                "INT64",
                0,
            ),
            bigquery.ScalarQueryParameter(
                "ai_recommendations_generated",
                "INT64",
                0,
            ),
            bigquery.ScalarQueryParameter(
                "steward_actions_taken",
                "INT64",
                0,
            ),
            bigquery.ScalarQueryParameter(
                "automated_fixes_applied",
                "INT64",
                0,
            ),
            bigquery.ScalarQueryParameter(
                "dq_health_score",
                "FLOAT64",
                dq_health_score,
            ),
            bigquery.ScalarQueryParameter(
                "dq_risk_score",
                "FLOAT64",
                dq_risk_score,
            ),
            bigquery.ScalarQueryParameter(
                "automation_readiness_score",
                "FLOAT64",
                automation_readiness_score,
            ),
            bigquery.ScalarQueryParameter(
                "created_at",
                "TIMESTAMP",
                now,
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