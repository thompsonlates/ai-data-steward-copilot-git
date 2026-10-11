from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


from google.cloud import bigquery

import uuid

from tomlkit import table

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
        self.profile_config_table_id = (
            f"{project_id}.{dataset}.DQ_PROFILE_CONFIG"
        )
        self.profile_rule_candidate_table_id = (
            f"{project_id}.{dataset}.DQ_PROFILE_RULE_CANDIDATES"
        )
        self.profile_rule_config_table_id = (
            f"{project_id}.{dataset}.DQ_PROFILE_RULE_CONFIG"
        )
        self.profile_rule_exception_table_id = (
            f"{project_id}.{dataset}.DQ_PROFILE_RULE_EXCEPTIONS"
        )


        self.profile_stage_prefix = "DQ_PROFILE_STAGE"
        self.profile_stage_expiration_days = 7


    def _ensure_profile_config_table(self) -> None:
        create_sql = f"""
        CREATE TABLE IF NOT EXISTS `{self.profile_config_table_id}` (
            organization_id STRING NOT NULL,
            profile_id STRING NOT NULL,
            profile_name STRING NOT NULL,
            domain STRING NOT NULL,
            source_name STRING NOT NULL,
            business_key_field STRING NOT NULL,
            status STRING NOT NULL,
            created_by STRING NOT NULL,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
        CLUSTER BY organization_id, profile_id
        """
        self.client.query(create_sql).result()

    def _ensure_profile_rule_candidate_table(self) -> None:
        create_sql = f"""
        CREATE TABLE IF NOT EXISTS `{self.profile_rule_candidate_table_id}` (
            candidate_id STRING NOT NULL,
            organization_id STRING NOT NULL,
            profile_id STRING NOT NULL,
            profile_run_id STRING NOT NULL,

            domain STRING NOT NULL,
            field_name STRING NOT NULL,

            rule_name STRING NOT NULL,
            rule_type STRING NOT NULL,
            operator STRING NOT NULL,

            comparison_value_json STRING,
            remediation_action STRING NOT NULL,
            replacement_value_json STRING,

            candidate_source STRING NOT NULL,
            steward_instruction STRING,
            discovery_summary STRING,
            discovery_confidence FLOAT64,
            ai_explanation STRING NOT NULL,

            status STRING NOT NULL,

            preview_records_evaluated INT64,
            preview_affected_count INT64,

            created_by STRING NOT NULL,
            created_at TIMESTAMP NOT NULL,

            previewed_at TIMESTAMP,

            approved_by STRING,
            approved_at TIMESTAMP,

            rejected_by STRING,
            rejected_at TIMESTAMP,
            rejection_reason STRING,
            steward_guidance STRING,
            rejection_evidence_json STRING,

            activated_rule_id STRING,
            activated_rule_version INT64
        )
        PARTITION BY DATE(created_at)
        CLUSTER BY organization_id, profile_id, candidate_id
        """

        self.client.query(create_sql).result()
        table = self.client.get_table(
            self.profile_rule_candidate_table_id
        )

        fields = {
            field.name: field
            for field in table.schema
        }

        schema_updates = {
            "candidate_source": "STRING",
            "discovery_summary": "STRING",
            "discovery_confidence": "FLOAT64",
            "rejected_by": "STRING",
            "rejected_at": "TIMESTAMP",
            "rejection_reason": "STRING",
            "steward_guidance": "STRING",
            "rejection_evidence_json": "STRING",
        }

        for column_name, column_type in schema_updates.items():
            if column_name not in fields:
                self.client.query(
                    f"""
                    ALTER TABLE `{self.profile_rule_candidate_table_id}`
                    ADD COLUMN {column_name} {column_type}
                    """
                ).result()

    def _ensure_profile_rule_config_table(self) -> None:
        create_sql = f"""
        CREATE TABLE IF NOT EXISTS `{self.profile_rule_config_table_id}` (
            organization_id STRING NOT NULL,
            profile_id STRING NOT NULL,

            rule_id STRING NOT NULL,
            version INT64 NOT NULL,
            logical_key STRING NOT NULL,

            scope STRING NOT NULL,
            family STRING NOT NULL,
            evaluator STRING NOT NULL,

            status STRING NOT NULL,
            severity STRING NOT NULL,
            description STRING,

            domain STRING NOT NULL,
            field_name STRING NOT NULL,

            parameters_json STRING,

            deterministic BOOL NOT NULL,
            remediation_available BOOL NOT NULL,
            remediation_type STRING,

            origin STRING NOT NULL,
            source_candidate_id STRING,

            created_by STRING NOT NULL,
            created_at TIMESTAMP NOT NULL,

            approved_by STRING NOT NULL,
            approved_at TIMESTAMP NOT NULL,

            activated_by STRING NOT NULL,
            activated_at TIMESTAMP NOT NULL,

            effective_from TIMESTAMP NOT NULL,
            effective_to TIMESTAMP
        )
        PARTITION BY DATE(created_at)
        CLUSTER BY organization_id, profile_id, rule_id
        """

        self.client.query(create_sql).result()

    def get_profile_rule_by_candidate(
        self,
        *,
        organization_id: str,
        profile_id: str,
        source_candidate_id: str,
    ) -> dict[str, Any] | None:
        organization_id = self._require_organization_id(
            organization_id
        )
        profile_id = self._require_profile_id(profile_id)

        source_candidate_id = str(
            source_candidate_id or ""
        ).strip()

        if not source_candidate_id.startswith("prc_"):
            raise ValueError(
                "source_candidate_id must use the prc_ "
                "identifier standard."
            )

        self._ensure_profile_rule_config_table()

        sql = f"""
        SELECT *
        FROM `{self.profile_rule_config_table_id}`
        WHERE organization_id = @organization_id
        AND profile_id = @profile_id
        AND source_candidate_id = @source_candidate_id
        ORDER BY version DESC
        LIMIT 1
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
                            "profile_id",
                            "STRING",
                            profile_id,
                        ),
                        bigquery.ScalarQueryParameter(
                            "source_candidate_id",
                            "STRING",
                            source_candidate_id,
                        ),
                    ]
                ),
            ).result()
        )

        if not rows:
            return None

        rule = dict(rows[0])

        if (
            str(rule.get("organization_id") or "").strip()
            != organization_id
            or str(rule.get("profile_id") or "").strip()
            != profile_id
            or str(rule.get("source_candidate_id") or "").strip()
            != source_candidate_id
        ):
            raise RuntimeError(
                "Governed profile rule crossed its expected "
                "tenant/profile/candidate boundary."
            )

        return rule

    def list_active_profile_rules(
        self,
        *,
        organization_id: str,
        profile_id: str,
        field_name: str | None = None,
    ) -> list[dict[str, Any]]:
        organization_id = self._require_organization_id(
            organization_id
        )
        profile_id = self._require_profile_id(profile_id)

        normalized_field = (
            str(field_name or "").strip().lower()
            if field_name is not None
            else None
        )

        self._ensure_profile_rule_config_table()

        field_filter = ""
        query_parameters = [
            bigquery.ScalarQueryParameter(
                "organization_id",
                "STRING",
                organization_id,
            ),
            bigquery.ScalarQueryParameter(
                "profile_id",
                "STRING",
                profile_id,
            ),
        ]

        if normalized_field:
            field_filter = "AND field_name = @field_name"
            query_parameters.append(
                bigquery.ScalarQueryParameter(
                    "field_name",
                    "STRING",
                    normalized_field,
                )
            )

        sql = f"""
        SELECT *
        FROM `{self.profile_rule_config_table_id}`
        WHERE organization_id = @organization_id
        AND profile_id = @profile_id
        AND status = 'ACTIVE'
        {field_filter}
        ORDER BY field_name, logical_key, version DESC
        """

        rows = list(
            self.client.query(
                sql,
                job_config=bigquery.QueryJobConfig(
                    query_parameters=query_parameters
                ),
            ).result()
        )

        rules = [dict(row) for row in rows]

        for rule in rules:
            if (
                str(
                    rule.get("organization_id") or ""
                ).strip()
                != organization_id
                or str(
                    rule.get("profile_id") or ""
                ).strip()
                != profile_id
            ):
                raise RuntimeError(
                    "Governed profile rule crossed its expected "
                    "tenant/profile boundary."
                )

        return rules

    def _ensure_profile_rule_exception_table(self) -> None:
        create_sql = f"""
        CREATE TABLE IF NOT EXISTS
        `{self.profile_rule_exception_table_id}` (
            exception_id STRING NOT NULL,
            organization_id STRING NOT NULL,
            profile_id STRING NOT NULL,

            rule_id STRING NOT NULL,
            rule_version INT64 NOT NULL,
            field_name STRING NOT NULL,

            match_type STRING NOT NULL,
            match_value_json STRING NOT NULL,

            reason STRING NOT NULL,
            status STRING NOT NULL,

            created_by STRING NOT NULL,
            created_at TIMESTAMP NOT NULL,

            approved_by STRING NOT NULL,
            approved_at TIMESTAMP NOT NULL,

            deactivated_by STRING,
            deactivated_at TIMESTAMP,
            deactivation_reason STRING,

            effective_from TIMESTAMP NOT NULL,
            effective_to TIMESTAMP
        )
        PARTITION BY DATE(created_at)
        CLUSTER BY organization_id, profile_id, rule_id, exception_id
        """
        self.client.query(create_sql).result()

    def create_active_profile_rule_exception(
        self,
        *,
        organization_id: str,
        profile_id: str,
        rule_id: str,
        rule_version: int,
        field_name: str,
        match_type: str,
        match_value: Any,
        reason: str,
        approved_by: str,
    ) -> dict[str, Any]:
        organization_id = self._require_organization_id(organization_id)
        profile_id = self._require_profile_id(profile_id)

        normalized_rule_id = str(rule_id or "").strip()
        normalized_field_name = str(field_name or "").strip()
        normalized_match_type = str(match_type or "").strip().upper()
        normalized_reason = str(reason or "").strip()
        normalized_approved_by = str(approved_by or "").strip()

        if not normalized_rule_id.startswith("PROFILE_"):
            raise ValueError(
                "rule_id must reference a governed PROFILE_ rule."
            )

        try:
            normalized_rule_version = int(rule_version)
        except (TypeError, ValueError) as exc:
            raise ValueError("rule_version must be a positive integer.") from exc

        if normalized_rule_version < 1:
            raise ValueError("rule_version must be a positive integer.")

        if not normalized_field_name:
            raise ValueError("field_name is required.")

        # V1 intentionally supports only exact-value exceptions.
        if normalized_match_type != "EXACT_VALUE":
            raise ValueError(
                "Unsupported profile rule exception match_type. "
                "V1 supports EXACT_VALUE only."
            )

        if match_value is None:
            raise ValueError(
                "match_value is required for an EXACT_VALUE exception."
            )

        if not normalized_reason:
            raise ValueError("reason is required.")

        if not normalized_approved_by:
            raise ValueError("approved_by is required.")

        self._ensure_profile_rule_config_table()
        self._ensure_profile_rule_exception_table()

        # The exception may only reference the exact governed rule version
        # inside the same tenant/profile boundary.
        rule_sql = f"""
        SELECT *
        FROM `{self.profile_rule_config_table_id}`
        WHERE organization_id = @organization_id
        AND profile_id = @profile_id
        AND rule_id = @rule_id
        AND version = @rule_version
        AND status = 'ACTIVE'
        LIMIT 1
        """

        rule_rows = list(
            self.client.query(
                rule_sql,
                job_config=bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter(
                            "organization_id",
                            "STRING",
                            organization_id,
                        ),
                        bigquery.ScalarQueryParameter(
                            "profile_id",
                            "STRING",
                            profile_id,
                        ),
                        bigquery.ScalarQueryParameter(
                            "rule_id",
                            "STRING",
                            normalized_rule_id,
                        ),
                        bigquery.ScalarQueryParameter(
                            "rule_version",
                            "INT64",
                            normalized_rule_version,
                        ),
                    ]
                ),
            ).result()
        )

        if not rule_rows:
            raise ValueError(
                "ACTIVE governed profile rule/version was not found "
                "inside the requested tenant/profile boundary."
            )

        governed_rule = dict(rule_rows[0])

        governed_field = str(
            governed_rule.get("field_name") or ""
        ).strip()

        if governed_field != normalized_field_name:
            raise ValueError(
                "Exception field_name does not match the governed rule."
            )

        match_value_json = json.dumps(
            match_value,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

        # Do not create duplicate ACTIVE exceptions for the same exact
        # governed rule version/value.
        duplicate_sql = f"""
        SELECT *
        FROM `{self.profile_rule_exception_table_id}`
        WHERE organization_id = @organization_id
        AND profile_id = @profile_id
        AND rule_id = @rule_id
        AND rule_version = @rule_version
        AND field_name = @field_name
        AND match_type = 'EXACT_VALUE'
        AND match_value_json = @match_value_json
        AND status = 'ACTIVE'
        AND (effective_to IS NULL OR effective_to > CURRENT_TIMESTAMP())
        ORDER BY created_at DESC
        LIMIT 1
        """

        duplicate_rows = list(
            self.client.query(
                duplicate_sql,
                job_config=bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter(
                            "organization_id",
                            "STRING",
                            organization_id,
                        ),
                        bigquery.ScalarQueryParameter(
                            "profile_id",
                            "STRING",
                            profile_id,
                        ),
                        bigquery.ScalarQueryParameter(
                            "rule_id",
                            "STRING",
                            normalized_rule_id,
                        ),
                        bigquery.ScalarQueryParameter(
                            "rule_version",
                            "INT64",
                            normalized_rule_version,
                        ),
                        bigquery.ScalarQueryParameter(
                            "field_name",
                            "STRING",
                            normalized_field_name,
                        ),
                        bigquery.ScalarQueryParameter(
                            "match_value_json",
                            "STRING",
                            match_value_json,
                        ),
                    ]
                ),
            ).result()
        )

        if duplicate_rows:
            return dict(duplicate_rows[0])

        exception_id = f"pre_{uuid.uuid4().hex[:20]}"

        insert_sql = f"""
        INSERT INTO `{self.profile_rule_exception_table_id}` (
            exception_id,
            organization_id,
            profile_id,
            rule_id,
            rule_version,
            field_name,
            match_type,
            match_value_json,
            reason,
            status,
            created_by,
            created_at,
            approved_by,
            approved_at,
            effective_from
        )
        VALUES (
            @exception_id,
            @organization_id,
            @profile_id,
            @rule_id,
            @rule_version,
            @field_name,
            'EXACT_VALUE',
            @match_value_json,
            @reason,
            'ACTIVE',
            @approved_by,
            CURRENT_TIMESTAMP(),
            @approved_by,
            CURRENT_TIMESTAMP(),
            CURRENT_TIMESTAMP()
        )
        """

        insert_job = self.client.query(
            insert_sql,
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter(
                        "exception_id", "STRING", exception_id
                    ),
                    bigquery.ScalarQueryParameter(
                        "organization_id", "STRING", organization_id
                    ),
                    bigquery.ScalarQueryParameter(
                        "profile_id", "STRING", profile_id
                    ),
                    bigquery.ScalarQueryParameter(
                        "rule_id", "STRING", normalized_rule_id
                    ),
                    bigquery.ScalarQueryParameter(
                        "rule_version",
                        "INT64",
                        normalized_rule_version,
                    ),
                    bigquery.ScalarQueryParameter(
                        "field_name",
                        "STRING",
                        normalized_field_name,
                    ),
                    bigquery.ScalarQueryParameter(
                        "match_value_json",
                        "STRING",
                        match_value_json,
                    ),
                    bigquery.ScalarQueryParameter(
                        "reason", "STRING", normalized_reason
                    ),
                    bigquery.ScalarQueryParameter(
                        "approved_by",
                        "STRING",
                        normalized_approved_by,
                    ),
                ]
            ),
        )

        insert_job.result()

        persisted = self.get_profile_rule_exception(
            organization_id=organization_id,
            profile_id=profile_id,
            exception_id=exception_id,
        )

        if persisted is None:
            raise RuntimeError(
                "Profile rule exception insert completed but the "
                "persisted exception could not be reloaded."
            )

        return persisted


    def get_profile_rule_exception(
        self,
        *,
        organization_id: str,
        profile_id: str,
        exception_id: str,
    ) -> dict[str, Any] | None:
        organization_id = self._require_organization_id(organization_id)
        profile_id = self._require_profile_id(profile_id)

        normalized_exception_id = str(exception_id or "").strip()

        if not normalized_exception_id.startswith("pre_"):
            raise ValueError(
                "exception_id must use the pre_ identifier standard."
            )

        self._ensure_profile_rule_exception_table()

        sql = f"""
        SELECT *
        FROM `{self.profile_rule_exception_table_id}`
        WHERE organization_id = @organization_id
        AND profile_id = @profile_id
        AND exception_id = @exception_id
        LIMIT 1
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
                            "profile_id",
                            "STRING",
                            profile_id,
                        ),
                        bigquery.ScalarQueryParameter(
                            "exception_id",
                            "STRING",
                            normalized_exception_id,
                        ),
                    ]
                ),
            ).result()
        )

        if not rows:
            # Distinguish a genuinely unknown exception from an attempt
            # to access an existing exception through the wrong
            # tenant/profile boundary.
            boundary_sql = f"""
            SELECT organization_id, profile_id
            FROM `{self.profile_rule_exception_table_id}`
            WHERE exception_id = @exception_id
            LIMIT 1
            """

            boundary_rows = list(
                self.client.query(
                    boundary_sql,
                    job_config=bigquery.QueryJobConfig(
                        query_parameters=[
                            bigquery.ScalarQueryParameter(
                                "exception_id",
                                "STRING",
                                normalized_exception_id,
                            ),
                        ]
                    ),
                ).result()
            )

            if boundary_rows:
                raise ValueError(
                    "Profile rule exception exists outside the requested "
                    "tenant/profile boundary."
                )

            return None

        result = dict(rows[0])

        # Defense in depth.
        if (
            str(result.get("organization_id") or "").strip()
            != organization_id
            or str(result.get("profile_id") or "").strip()
            != profile_id
        ):
            raise RuntimeError(
                "Profile rule exception crossed tenant/profile boundary."
            )

        return result


    def list_active_profile_rule_exceptions(
        self,
        *,
        organization_id: str,
        profile_id: str,
        rule_id: str | None = None,
        rule_version: int | None = None,
    ) -> list[dict[str, Any]]:
        organization_id = self._require_organization_id(organization_id)
        profile_id = self._require_profile_id(profile_id)

        normalized_rule_id = (
            str(rule_id or "").strip() if rule_id is not None else None
        )

        normalized_rule_version = None

        if rule_version is not None:
            try:
                normalized_rule_version = int(rule_version)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "rule_version must be a positive integer."
                ) from exc

            if normalized_rule_version < 1:
                raise ValueError(
                    "rule_version must be a positive integer."
                )

        if normalized_rule_id is not None:
            if not normalized_rule_id.startswith("PROFILE_"):
                raise ValueError(
                    "rule_id must reference a governed PROFILE_ rule."
                )

        if (
            normalized_rule_version is not None
            and normalized_rule_id is None
        ):
            raise ValueError(
                "rule_id is required when rule_version is supplied."
            )

        self._ensure_profile_rule_exception_table()

        rule_filter = ""
        parameters = [
            bigquery.ScalarQueryParameter(
                "organization_id", "STRING", organization_id
            ),
            bigquery.ScalarQueryParameter(
                "profile_id", "STRING", profile_id
            ),
        ]

        if normalized_rule_id is not None:
            rule_filter += "\nAND rule_id = @rule_id"
            parameters.append(
                bigquery.ScalarQueryParameter(
                    "rule_id", "STRING", normalized_rule_id
                )
            )

        if normalized_rule_version is not None:
            rule_filter += "\nAND rule_version = @rule_version"
            parameters.append(
                bigquery.ScalarQueryParameter(
                    "rule_version",
                    "INT64",
                    normalized_rule_version,
                )
            )

        sql = f"""
        SELECT *
        FROM `{self.profile_rule_exception_table_id}`
        WHERE organization_id = @organization_id
        AND profile_id = @profile_id
        AND status = 'ACTIVE'
        AND effective_from <= CURRENT_TIMESTAMP()
        AND (effective_to IS NULL OR effective_to > CURRENT_TIMESTAMP())
        {rule_filter}
        ORDER BY created_at ASC, exception_id ASC
        """

        rows = list(
            self.client.query(
                sql,
                job_config=bigquery.QueryJobConfig(
                    query_parameters=parameters
                ),
            ).result()
        )

        results = [dict(row) for row in rows]

        for result in results:
            if (
                str(result.get("organization_id") or "").strip()
                != organization_id
                or str(result.get("profile_id") or "").strip()
                != profile_id
            ):
                raise RuntimeError(
                    "Profile rule exception crossed "
                    "tenant/profile boundary."
                )

        return results


    def deactivate_profile_rule_exception(
            self,
            *,
            organization_id: str,
            profile_id: str,
            exception_id: str,
            deactivated_by: str,
            reason: str,
        ) -> dict[str, Any]:
            organization_id = self._require_organization_id(organization_id)
            profile_id = self._require_profile_id(profile_id)

            normalized_exception_id = str(exception_id or "").strip()
            normalized_deactivated_by = str(deactivated_by or "").strip()
            normalized_reason = str(reason or "").strip()

            if not normalized_exception_id.startswith("pre_"):
                raise ValueError(
                    "exception_id must use the pre_ identifier standard."
                )

            if not normalized_deactivated_by:
                raise ValueError("deactivated_by is required.")

            if not normalized_reason:
                raise ValueError("deactivation reason is required.")

            self._ensure_profile_rule_exception_table()

            sql = f"""
            UPDATE `{self.profile_rule_exception_table_id}`
            SET
                status = 'INACTIVE',
                deactivated_by = @deactivated_by,
                deactivated_at = CURRENT_TIMESTAMP(),
                deactivation_reason = @deactivation_reason,
                effective_to = CURRENT_TIMESTAMP()
            WHERE organization_id = @organization_id
            AND profile_id = @profile_id
            AND exception_id = @exception_id
            AND status = 'ACTIVE'
            AND effective_to IS NULL
            """

            job = self.client.query(
                sql,
                job_config=bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter(
                            "organization_id",
                            "STRING",
                            organization_id,
                        ),
                        bigquery.ScalarQueryParameter(
                            "profile_id",
                            "STRING",
                            profile_id,
                        ),
                        bigquery.ScalarQueryParameter(
                            "exception_id",
                            "STRING",
                            normalized_exception_id,
                        ),
                        bigquery.ScalarQueryParameter(
                            "deactivated_by",
                            "STRING",
                            normalized_deactivated_by,
                        ),
                        bigquery.ScalarQueryParameter(
                            "deactivation_reason",
                            "STRING",
                            normalized_reason,
                        ),
                    ]
                ),
            )

            job.result()

            if job.num_dml_affected_rows != 1:
                raise ValueError(
                    "ACTIVE profile rule exception was not found "
                    "inside the requested tenant/profile boundary."
                )

            persisted = self.get_profile_rule_exception(
                organization_id=organization_id,
                profile_id=profile_id,
                exception_id=normalized_exception_id,
            )

            if persisted is None:
                raise RuntimeError(
                    "Profile rule exception was deactivated but could "
                    "not be reloaded."
                )

            return persisted

    def create_active_profile_rule(
            self,
            *,
            organization_id: str,
            profile_id: str,
            source_candidate_id: str,
            logical_key: str,
            family: str,
            evaluator: str,
            domain: str,
            field_name: str,
            parameters: dict[str, Any] | None,
            severity: str,
            description: str,
            remediation_available: bool,
            remediation_type: str | None,
            approved_by: str,
        ) -> dict[str, Any]:
            organization_id = self._require_organization_id(
                organization_id
            )
            profile_id = self._require_profile_id(profile_id)

            normalized_candidate_id = str(
                source_candidate_id or ""
            ).strip()

            if not normalized_candidate_id.startswith("prc_"):
                raise ValueError(
                    "source_candidate_id must use the prc_ "
                    "identifier standard."
                )

            normalized_logical_key = str(
                logical_key or ""
            ).strip().upper()

            normalized_family = str(
                family or ""
            ).strip().upper()

            normalized_evaluator = str(
                evaluator or ""
            ).strip().upper()

            normalized_domain = str(
                domain or ""
            ).strip().upper()

            normalized_field = str(
                field_name or ""
            ).strip().lower()

            normalized_severity = str(
                severity or "MEDIUM"
            ).strip().upper()

            normalized_description = str(
                description or ""
            ).strip()

            normalized_remediation_type = (
                str(remediation_type or "").strip().upper()
                or None
            )

            normalized_approved_by = str(
                approved_by or ""
            ).strip()

            if not normalized_logical_key:
                raise ValueError("logical_key is required.")

            if not normalized_family:
                raise ValueError("family is required.")

            if not normalized_evaluator:
                raise ValueError("evaluator is required.")

            if not normalized_domain:
                raise ValueError("domain is required.")

            if not normalized_field:
                raise ValueError("field_name is required.")

            if not normalized_approved_by:
                raise ValueError("approved_by is required.")

            if normalized_severity not in {
                "LOW",
                "MEDIUM",
                "HIGH",
                "CRITICAL",
            }:
                raise ValueError(
                    "severity must be LOW, MEDIUM, HIGH, or CRITICAL."
                )

            if remediation_available and not normalized_remediation_type:
                raise ValueError(
                    "remediation_available requires remediation_type."
                )

            safe_parameters = (
                parameters
                if isinstance(parameters, dict)
                else {}
            )

            parameters_json = json.dumps(
                safe_parameters,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )

            self._ensure_profile_rule_config_table()

            # A candidate may activate only one governed rule.
            existing_sql = f"""
            SELECT *
            FROM `{self.profile_rule_config_table_id}`
            WHERE organization_id = @organization_id
            AND profile_id = @profile_id
            AND source_candidate_id = @source_candidate_id
            LIMIT 1
            """

            existing_rows = list(
                self.client.query(
                    existing_sql,
                    job_config=bigquery.QueryJobConfig(
                        query_parameters=[
                            bigquery.ScalarQueryParameter(
                                "organization_id",
                                "STRING",
                                organization_id,
                            ),
                            bigquery.ScalarQueryParameter(
                                "profile_id",
                                "STRING",
                                profile_id,
                            ),
                            bigquery.ScalarQueryParameter(
                                "source_candidate_id",
                                "STRING",
                                normalized_candidate_id,
                            ),
                        ]
                    ),
                ).result()
            )

            if existing_rows:
                raise ValueError(
                    "Profile rule candidate has already activated "
                    "a governed rule."
                )

            # Rule identity is generated by the platform, never supplied
            # by the browser or invented by the approval route.
            rule_id = f"PROFILE_{uuid.uuid4().hex[:20].upper()}"

            # Versions are scoped to the governed logical rule for this
            # tenant/profile. The first approved version is 1.
            version_sql = f"""
            SELECT COALESCE(MAX(version), 0) + 1 AS next_version
            FROM `{self.profile_rule_config_table_id}`
            WHERE organization_id = @organization_id
            AND profile_id = @profile_id
            AND logical_key = @logical_key
            """

            version_rows = list(
                self.client.query(
                    version_sql,
                    job_config=bigquery.QueryJobConfig(
                        query_parameters=[
                            bigquery.ScalarQueryParameter(
                                "organization_id",
                                "STRING",
                                organization_id,
                            ),
                            bigquery.ScalarQueryParameter(
                                "profile_id",
                                "STRING",
                                profile_id,
                            ),
                            bigquery.ScalarQueryParameter(
                                "logical_key",
                                "STRING",
                                normalized_logical_key,
                            ),
                        ]
                    ),
                ).result()
            )

            if not version_rows:
                raise RuntimeError(
                    "Unable to determine governed profile rule version."
                )

            version = int(version_rows[0]["next_version"])

            retire_sql = f"""
            UPDATE `{self.profile_rule_config_table_id}`
            SET
                status = 'INACTIVE',
                effective_to = CURRENT_TIMESTAMP()
            WHERE organization_id = @organization_id
            AND profile_id = @profile_id
            AND logical_key = @logical_key
            AND status = 'ACTIVE'
            """

            retire_job = self.client.query(
                retire_sql,
                job_config=bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter(
                            "organization_id",
                            "STRING",
                            organization_id,
                        ),
                        bigquery.ScalarQueryParameter(
                            "profile_id",
                            "STRING",
                            profile_id,
                        ),
                        bigquery.ScalarQueryParameter(
                            "logical_key",
                            "STRING",
                            normalized_logical_key,
                        ),
                    ]
                ),
            )
            retire_job.result()

            insert_sql = f"""
            INSERT INTO `{self.profile_rule_config_table_id}` (
                organization_id,
                profile_id,
                rule_id,
                version,
                logical_key,
                scope,
                family,
                evaluator,
                status,
                severity,
                description,
                domain,
                field_name,
                parameters_json,
                deterministic,
                remediation_available,
                remediation_type,
                origin,
                source_candidate_id,
                created_by,
                created_at,
                approved_by,
                approved_at,
                activated_by,
                activated_at,
                effective_from,
                effective_to
            )
            VALUES (
                @organization_id,
                @profile_id,
                @rule_id,
                @version,
                @logical_key,
                'PROFILE',
                @family,
                @evaluator,
                'ACTIVE',
                @severity,
                @description,
                @domain,
                @field_name,
                @parameters_json,
                TRUE,
                @remediation_available,
                @remediation_type,
                'STEWARD_APPROVED',
                @source_candidate_id,
                @approved_by,
                CURRENT_TIMESTAMP(),
                @approved_by,
                CURRENT_TIMESTAMP(),
                @approved_by,
                CURRENT_TIMESTAMP(),
                CURRENT_TIMESTAMP(),
                NULL
            )
            """

            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter(
                        "organization_id",
                        "STRING",
                        organization_id,
                    ),
                    bigquery.ScalarQueryParameter(
                        "profile_id",
                        "STRING",
                        profile_id,
                    ),
                    bigquery.ScalarQueryParameter(
                        "rule_id",
                        "STRING",
                        rule_id,
                    ),
                    bigquery.ScalarQueryParameter(
                        "version",
                        "INT64",
                        version,
                    ),
                    bigquery.ScalarQueryParameter(
                        "logical_key",
                        "STRING",
                        normalized_logical_key,
                    ),
                    bigquery.ScalarQueryParameter(
                        "family",
                        "STRING",
                        normalized_family,
                    ),
                    bigquery.ScalarQueryParameter(
                        "evaluator",
                        "STRING",
                        normalized_evaluator,
                    ),
                    bigquery.ScalarQueryParameter(
                        "severity",
                        "STRING",
                        normalized_severity,
                    ),
                    bigquery.ScalarQueryParameter(
                        "description",
                        "STRING",
                        normalized_description or None,
                    ),
                    bigquery.ScalarQueryParameter(
                        "domain",
                        "STRING",
                        normalized_domain,
                    ),
                    bigquery.ScalarQueryParameter(
                        "field_name",
                        "STRING",
                        normalized_field,
                    ),
                    bigquery.ScalarQueryParameter(
                        "parameters_json",
                        "STRING",
                        parameters_json,
                    ),
                    bigquery.ScalarQueryParameter(
                        "remediation_available",
                        "BOOL",
                        bool(remediation_available),
                    ),
                    bigquery.ScalarQueryParameter(
                        "remediation_type",
                        "STRING",
                        normalized_remediation_type,
                    ),
                    bigquery.ScalarQueryParameter(
                        "source_candidate_id",
                        "STRING",
                        normalized_candidate_id,
                    ),
                    bigquery.ScalarQueryParameter(
                        "approved_by",
                        "STRING",
                        normalized_approved_by,
                    ),
                ]
            )

            query_job = self.client.query(
                insert_sql,
                job_config=job_config,
            )
            query_job.result()

            if int(query_job.num_dml_affected_rows or 0) != 1:
                raise RuntimeError(
                    "Unable to persist the ACTIVE governed profile rule."
                )

            persisted_sql = f"""
            SELECT *
            FROM `{self.profile_rule_config_table_id}`
            WHERE organization_id = @organization_id
            AND profile_id = @profile_id
            AND rule_id = @rule_id
            AND version = @version
            LIMIT 1
            """

            persisted_rows = list(
                self.client.query(
                    persisted_sql,
                    job_config=bigquery.QueryJobConfig(
                        query_parameters=[
                            bigquery.ScalarQueryParameter(
                                "organization_id",
                                "STRING",
                                organization_id,
                            ),
                            bigquery.ScalarQueryParameter(
                                "profile_id",
                                "STRING",
                                profile_id,
                            ),
                            bigquery.ScalarQueryParameter(
                                "rule_id",
                                "STRING",
                                rule_id,
                            ),
                            bigquery.ScalarQueryParameter(
                                "version",
                                "INT64",
                                version,
                            ),
                        ]
                    ),
                ).result()
            )

            if not persisted_rows:
                raise RuntimeError(
                    "Governed profile rule was inserted but "
                    "could not be reloaded."
                )

            persisted_rule = dict(persisted_rows[0])

            if (
                str(persisted_rule.get("organization_id") or "").strip()
                != organization_id
                or str(persisted_rule.get("profile_id") or "").strip()
                != profile_id
            ):
                raise RuntimeError(
                    "Governed profile rule crossed tenant/profile boundary."
                )

            return persisted_rule


    def _ensure_profile_source_context_table(self) -> None:
            create_sql = f"""
            CREATE TABLE IF NOT EXISTS `{self.profile_source_context_table_id}` (
                organization_id STRING NOT NULL,
                profile_run_id STRING NOT NULL,
                profile_id STRING,
                source_type STRING NOT NULL,
                connection_id STRING NOT NULL,
                project_id STRING,
                dataset_id STRING,
                table_id STRING,
                database_name STRING,
                schema_name STRING,
                table_name STRING,
                spreadsheet_id STRING,
                drive_id STRING,
                item_id STRING,
                sheet_name STRING,
                header_row INT64,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                PRIMARY KEY (organization_id, profile_run_id) NOT ENFORCED
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

            for column_name in (
                "profile_id",
                "project_id", "dataset_id", "table_id",
                "database_name", "schema_name", "table_name",
            ):
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
            profile_id: str | None = None,
            source_type: str,
            connection_id: str,
            sheet_name: str | None = None,
            header_row: int | None = None,
            project_id: str | None = None,
            dataset_id: str | None = None,
            table_id: str | None = None,
            database_name: str | None = None,
            schema_name: str | None = None,
            table_name: str | None = None,
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
            normalized_profile_id = (
                self._require_profile_id(profile_id)
                if profile_id
                else None
            )

            if normalized_source_type not in {
                "GOOGLE_SHEETS",
                "ONEDRIVE_EXCEL",
                "BIGQUERY",
                "SNOWFLAKE",
                "AZURE_SQL",
            }:
                raise ValueError("Unsupported profile source context type.")
            if not normalized_connection_id:
                raise ValueError("connection_id is required.")
            normalized_project_id = str(project_id or "").strip() or None
            normalized_dataset_id = str(dataset_id or "").strip() or None
            normalized_table_id = str(table_id or "").strip() or None
            normalized_database_name = str(database_name or "").strip() or None
            normalized_schema_name = str(schema_name or "").strip() or None
            normalized_table_name = str(table_name or "").strip() or None

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
            if normalized_source_type == "SNOWFLAKE" and not all(
                [normalized_database_name, normalized_schema_name, normalized_table_name]
            ):
                raise ValueError(
                    "database_name, schema_name, and table_name are required for Snowflake."
                )
            if normalized_source_type == "AZURE_SQL" and not all(
                [normalized_database_name, normalized_schema_name, normalized_table_name]
            ):
                raise ValueError(
                    "database_name, schema_name, and table_name are required for Azure SQL."
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
                profile_id = @profile_id,
                project_id = @project_id,
                dataset_id = @dataset_id,
                table_id = @table_id,
                database_name = @database_name,
                schema_name = @schema_name,
                table_name = @table_name,
                spreadsheet_id = @spreadsheet_id,
                drive_id = @drive_id,
                item_id = @item_id,
                sheet_name = @sheet_name,
                header_row = @header_row,
                updated_at = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT (
                organization_id, profile_run_id, profile_id, source_type, connection_id,
                project_id, dataset_id, table_id,
                database_name, schema_name, table_name,
                spreadsheet_id, drive_id, item_id, sheet_name, header_row,
                created_at, updated_at
            ) VALUES (
                @organization_id, @profile_run_id, @profile_id, @source_type, @connection_id,
                @project_id, @dataset_id, @table_id,
                @database_name, @schema_name, @table_name,
                @spreadsheet_id, @drive_id, @item_id, @sheet_name, @header_row,
                CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()
            )
            """
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("organization_id", "STRING", organization_id),
                    bigquery.ScalarQueryParameter("profile_run_id", "STRING", profile_run_id),
                    bigquery.ScalarQueryParameter("profile_id", "STRING", normalized_profile_id),
                    bigquery.ScalarQueryParameter("source_type", "STRING", normalized_source_type),
                    bigquery.ScalarQueryParameter("connection_id", "STRING", normalized_connection_id),
                    bigquery.ScalarQueryParameter("project_id", "STRING", normalized_project_id),
                    bigquery.ScalarQueryParameter("dataset_id", "STRING", normalized_dataset_id),
                    bigquery.ScalarQueryParameter("table_id", "STRING", normalized_table_id),
                    bigquery.ScalarQueryParameter("database_name", "STRING", normalized_database_name),
                    bigquery.ScalarQueryParameter("schema_name", "STRING", normalized_schema_name),
                    bigquery.ScalarQueryParameter("table_name", "STRING", normalized_table_name),
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
        profile_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Return the exact tenant-bound source used for a profile run."""
        normalized_org = self._require_organization_id(organization_id)
        normalized_run = self._require_profile_run_id(profile_run_id)
        normalized_profile = self._require_profile_id(profile_id) if profile_id is not None else None
        self._ensure_profile_source_context_table()

        profile_filter = ""

        if normalized_profile:
            profile_filter = """
            AND profile_id = @profile_id
            """

        sql = f"""
        SELECT *
        FROM `{self.profile_source_context_table_id}`
        WHERE organization_id = @organization_id
          AND profile_run_id = @profile_run_id
          {profile_filter}
        ORDER BY updated_at DESC
        LIMIT 1
        """

        query_parameters = [
            bigquery.ScalarQueryParameter(
                "organization_id", "STRING", normalized_org
            ),
            bigquery.ScalarQueryParameter(
                "profile_run_id", "STRING", normalized_run
            ),
        ]

        if normalized_profile:
            query_parameters.append(
                bigquery.ScalarQueryParameter(
                    "profile_id", "STRING", normalized_profile
                )
            )

        rows = list(
            self.client.query(
                sql,
                job_config=bigquery.QueryJobConfig(
                    query_parameters=query_parameters
                ),
            ).result()
        )
        if not rows:
            return None
        result = dict(rows[0])
        if str(result.get("organization_id") or "").strip() != normalized_org:
            raise RuntimeError("Profile source context crossed tenant boundary.")
        return result

    def get_profile(
        self,
        *,
        organization_id: str,
        profile_id: str,
    ) -> dict[str, Any] | None:
        organization_id = self._require_organization_id(
            organization_id
        )
        profile_id = self._require_profile_id(profile_id)

        self._ensure_profile_config_table()

        sql = f"""
        SELECT *
        FROM `{self.profile_config_table_id}`
        WHERE organization_id = @organization_id
        AND profile_id = @profile_id
        LIMIT 1
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
                            "profile_id",
                            "STRING",
                            profile_id,
                        ),
                    ]
                ),
            ).result()
        )

        if not rows:
            return None

        result = dict(rows[0])

        # Defense in depth.
        if result.get("organization_id") != organization_id:
            raise RuntimeError(
                "Profile configuration crossed tenant boundary."
            )

        return result

    def find_active_profile(
            self,
            *,
            organization_id: str,
            domain: str,
            source_name: str,
            business_key_field: str,
        ) -> dict[str, Any] | None:
            """
            Resolve the canonical ACTIVE durable profile for a tenant/source.
            """
            organization_id = self._require_organization_id(
                organization_id
            )

            normalized_domain = str(
                domain or ""
            ).strip().upper()

            normalized_source = str(
                source_name or ""
            ).strip()

            normalized_business_key = str(
                business_key_field or ""
            ).strip().lower()

            if not normalized_domain:
                raise ValueError("domain is required.")

            if not normalized_source:
                raise ValueError("source_name is required.")

            if not normalized_business_key:
                raise ValueError(
                    "business_key_field is required."
                )

            self._ensure_profile_config_table()

            sql = f"""
            SELECT *
            FROM `{self.profile_config_table_id}`
            WHERE organization_id = @organization_id
            AND domain = @domain
            AND source_name = @source_name
            AND business_key_field = @business_key_field
            AND status = 'ACTIVE'
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
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
                                "domain",
                                "STRING",
                                normalized_domain,
                            ),
                            bigquery.ScalarQueryParameter(
                                "source_name",
                                "STRING",
                                normalized_source,
                            ),
                            bigquery.ScalarQueryParameter(
                                "business_key_field",
                                "STRING",
                                normalized_business_key,
                            ),
                        ]
                    ),
                ).result()
            )

            if not rows:
                return None

            result = dict(rows[0])

            if result.get("organization_id") != organization_id:
                raise RuntimeError(
                    "Profile configuration crossed tenant boundary."
                )

            return result

    def create_profile(
        self,
        *,
        organization_id: str,
        profile_name: str,
        domain: str,
        source_name: str,
        business_key_field: str,
        created_by: str,
    ) -> dict[str, Any]:
        organization_id = self._require_organization_id(
            organization_id
        )

        normalized_name = str(profile_name or "").strip()
        normalized_domain = str(domain or "").strip().upper()
        normalized_source = str(source_name or "").strip()
        normalized_business_key = str(
            business_key_field or ""
        ).strip().lower()
        normalized_created_by = str(created_by or "").strip()

        if not normalized_name:
            raise ValueError("profile_name is required.")
        if not normalized_domain:
            raise ValueError("domain is required.")
        if not normalized_source:
            raise ValueError("source_name is required.")
        if not normalized_business_key:
            raise ValueError("business_key_field is required.")
        if not normalized_created_by:
            raise ValueError("created_by is required.")

        profile_id = f"prof_{uuid.uuid4().hex[:20]}"

        self._ensure_profile_config_table()

        sql = f"""
        INSERT INTO `{self.profile_config_table_id}` (
            organization_id,
            profile_id,
            profile_name,
            domain,
            source_name,
            business_key_field,
            status,
            created_by,
            created_at,
            updated_at
        )
        VALUES (
            @organization_id,
            @profile_id,
            @profile_name,
            @domain,
            @source_name,
            @business_key_field,
            'ACTIVE',
            @created_by,
            CURRENT_TIMESTAMP(),
            CURRENT_TIMESTAMP()
        )
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "organization_id", "STRING", organization_id
                ),
                bigquery.ScalarQueryParameter(
                    "profile_id", "STRING", profile_id
                ),
                bigquery.ScalarQueryParameter(
                    "profile_name", "STRING", normalized_name
                ),
                bigquery.ScalarQueryParameter(
                    "domain", "STRING", normalized_domain
                ),
                bigquery.ScalarQueryParameter(
                    "source_name", "STRING", normalized_source
                ),
                bigquery.ScalarQueryParameter(
                    "business_key_field",
                    "STRING",
                    normalized_business_key,
                ),
                bigquery.ScalarQueryParameter(
                    "created_by", "STRING", normalized_created_by
                ),
            ]
        )

        self.client.query(sql, job_config=job_config).result()

        return {
            "organization_id": organization_id,
            "profile_id": profile_id,
            "profile_name": normalized_name,
            "domain": normalized_domain,
            "source_name": normalized_source,
            "business_key_field": normalized_business_key,
            "status": "ACTIVE",
        }

    def create_profile_rule_candidate(
        self,
        *,
        organization_id: str,
        profile_id: str,
        profile_run_id: str,
        domain: str,
        field_name: str,
        rule_name: str,
        rule_type: str,
        operator: str,
        comparison_value_json: str | None,
        remediation_action: str,
        replacement_value_json: str | None,
        steward_instruction: str,
        ai_explanation: str,
        candidate_source: str = "STEWARD_INSTRUCTION",
        discovery_summary: str | None = None,
        discovery_confidence: float | None = None,
        created_by: str,
    ) -> dict[str, Any]:
        organization_id = self._require_organization_id(
            organization_id
        )
        profile_id = self._require_profile_id(profile_id)
        profile_run_id = self._require_profile_run_id(
            profile_run_id
        )

        normalized_domain = str(domain or "").strip().upper()
        normalized_field = str(field_name or "").strip()
        normalized_rule_name = str(rule_name or "").strip()
        normalized_rule_type = str(rule_type or "").strip().upper()
        normalized_operator = str(operator or "").strip().upper()
        normalized_action = str(
            remediation_action or ""
        ).strip().upper()
        normalized_instruction = str(
            steward_instruction or ""
        ).strip()
        normalized_explanation = str(
            ai_explanation or ""
        ).strip()
        normalized_created_by = str(created_by or "").strip()
        normalized_candidate_source = str(
            candidate_source or ""
        ).strip().upper()

        if normalized_candidate_source not in {
            "STEWARD_INSTRUCTION",
            "AI_PATTERN_DISCOVERY",
        }:
            raise ValueError(
                "Unsupported profile rule candidate source."
            )
        normalized_discovery_summary = str(discovery_summary or "").strip()
        if discovery_confidence is not None:
            safe_discovery_confidence = float(
                discovery_confidence
            )

            if not 0.0 <= safe_discovery_confidence <= 1.0:
                raise ValueError(
                    "discovery_confidence must be "
                    "between 0.0 and 1.0."
                )
        else:
            safe_discovery_confidence = None

        if not normalized_domain:
            raise ValueError("domain is required.")
        if not normalized_field:
            raise ValueError("field_name is required.")
        if not normalized_rule_name:
            raise ValueError("rule_name is required.")
        if not normalized_rule_type:
            raise ValueError("rule_type is required.")

        if normalized_operator not in {
            "CONTAINS",
            "EQUALS",
            "REGEX",
            "IN",
        }:
            raise ValueError(
                "Unsupported profile rule operator."
            )

        if normalized_action not in {
            "STANDARDIZE_VALUE",
            "REPLACE_VALUE",
            "DELETE_VALUE",
            "FLAG_FOR_REVIEW",
        }:
            raise ValueError(
                "Unsupported profile rule remediation action."
            )

        # DELETE_VALUE has one governed meaning:
        # clear the targeted field value to NULL.
        # It must never represent row/record deletion.
        if (
            normalized_action == "DELETE_VALUE"
            and replacement_value_json is not None
        ):
            raise ValueError(
                "DELETE_VALUE must resolve the targeted "
                "field value to NULL."
            )

        if not normalized_instruction:
            raise ValueError(
                "steward_instruction is required."
            )
        if not normalized_explanation:
            raise ValueError(
                "ai_explanation is required."
            )
        if not normalized_created_by:
            raise ValueError("created_by is required.")

        # Fail closed: the durable profile must belong
        # to this authenticated tenant.
        profile = self.get_profile(
            organization_id=organization_id,
            profile_id=profile_id,
        )

        if not profile:
            raise ValueError(
                "Profile rule candidate references an "
                "unknown tenant-scoped profile."
            )

        if str(profile.get("status") or "").upper() != "ACTIVE":
            raise ValueError(
                "Profile rule candidate requires an ACTIVE profile."
            )

        if (
            str(profile.get("domain") or "").strip().upper()
            != normalized_domain
        ):
            raise ValueError(
                "Profile rule candidate domain does not "
                "match the governed profile."
            )

        # Also prove that this dqp_ execution belongs to
        # this exact durable prof_ identity.
        source_context = self.get_profile_source_context(
            organization_id=organization_id,
            profile_run_id=profile_run_id,
            profile_id=profile_id,
        )

        if not source_context:
            raise ValueError(
                "Profile rule candidate references an "
                "unknown profile execution context."
            )

        candidate_id = (
            f"prc_{uuid.uuid4().hex[:20]}"
        )

        self._ensure_profile_rule_candidate_table()

        sql = f"""
        INSERT INTO `{self.profile_rule_candidate_table_id}` (
            candidate_id,
            organization_id,
            profile_id,
            profile_run_id,
            domain,
            field_name,
            rule_name,
            rule_type,
            operator,
            comparison_value_json,
            remediation_action,
            replacement_value_json,
            steward_instruction,
            candidate_source,
            discovery_summary,
            discovery_confidence,
            ai_explanation,
            status,
            created_by,
            created_at
        )
        VALUES (
            @candidate_id,
            @organization_id,
            @profile_id,
            @profile_run_id,
            @domain,
            @field_name,
            @rule_name,
            @rule_type,
            @operator,
            @comparison_value_json,
            @remediation_action,
            @replacement_value_json,
            @steward_instruction,
            @candidate_source,
            @discovery_summary,
            @discovery_confidence,
            @ai_explanation,
            'CANDIDATE',
            @created_by,
            CURRENT_TIMESTAMP()
        )
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "candidate_id", "STRING", candidate_id
                ),
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "profile_id", "STRING", profile_id
                ),
                bigquery.ScalarQueryParameter(
                    "profile_run_id",
                    "STRING",
                    profile_run_id,
                ),
                bigquery.ScalarQueryParameter(
                    "domain", "STRING", normalized_domain
                ),
                bigquery.ScalarQueryParameter(
                    "field_name", "STRING", normalized_field
                ),
                bigquery.ScalarQueryParameter(
                    "rule_name",
                    "STRING",
                    normalized_rule_name,
                ),
                bigquery.ScalarQueryParameter(
                    "rule_type",
                    "STRING",
                    normalized_rule_type,
                ),
                bigquery.ScalarQueryParameter(
                    "operator",
                    "STRING",
                    normalized_operator,
                ),
                bigquery.ScalarQueryParameter(
                    "comparison_value_json",
                    "STRING",
                    comparison_value_json,
                ),
                bigquery.ScalarQueryParameter(
                    "remediation_action",
                    "STRING",
                    normalized_action,
                ),
                bigquery.ScalarQueryParameter(
                    "replacement_value_json",
                    "STRING",
                    replacement_value_json,
                ),
                bigquery.ScalarQueryParameter(
                    "steward_instruction",
                    "STRING",
                    normalized_instruction,
                ),
                bigquery.ScalarQueryParameter(
                    "ai_explanation",
                    "STRING",
                    normalized_explanation,
                ),
                bigquery.ScalarQueryParameter(
                    "created_by",
                    "STRING",
                    normalized_created_by,
                ),
                bigquery.ScalarQueryParameter(
                    "candidate_source",
                    "STRING",
                    normalized_candidate_source,
                ),
                bigquery.ScalarQueryParameter(
                    "discovery_summary",
                    "STRING",
                    normalized_discovery_summary,
                ),
                bigquery.ScalarQueryParameter(
                    "discovery_confidence",
                    "FLOAT",
                    safe_discovery_confidence,
                ),
            ]
        )

        self.client.query(
            sql,
            job_config=job_config,
        ).result()

        persisted_candidate = self.get_profile_rule_candidate(
            organization_id=organization_id,
            candidate_id=candidate_id,
        )

        if not persisted_candidate:
            raise RuntimeError(
                "Profile rule candidate was inserted but "
                "could not be reloaded."
            )

        return persisted_candidate

    def get_profile_rule_candidate(
        self,
        *,
        organization_id: str,
        candidate_id: str,
    ) -> dict[str, Any] | None:
        organization_id = self._require_organization_id(
            organization_id
        )

        normalized_candidate_id = str(
            candidate_id or ""
        ).strip()

        if not normalized_candidate_id:
            raise ValueError("candidate_id is required.")

        if not normalized_candidate_id.startswith("prc_"):
            raise ValueError(
                "candidate_id must use the prc_ "
                "identifier standard."
            )

        self._ensure_profile_rule_candidate_table()

        sql = f"""
        SELECT *
        FROM `{self.profile_rule_candidate_table_id}`
        WHERE organization_id = @organization_id
        AND candidate_id = @candidate_id
        LIMIT 1
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
                            "candidate_id",
                            "STRING",
                            normalized_candidate_id,
                        ),
                    ]
                ),
            ).result()
        )

        if not rows:
            return None

        result = dict(rows[0])

        if (
            str(result.get("organization_id") or "").strip()
            != organization_id
        ):
            raise RuntimeError(
                "Profile rule candidate crossed tenant boundary."
            )

        return result

    def mark_profile_rule_candidate_previewed(
        self,
        *,
        organization_id: str,
        candidate_id: str,
        records_evaluated: int,
        affected_record_count: int,
    ) -> dict[str, Any]:
        organization_id = self._require_organization_id(
            organization_id
        )

        candidate = self.get_profile_rule_candidate(
            organization_id=organization_id,
            candidate_id=candidate_id,
        )

        if not candidate:
            raise ValueError(
                "Profile rule candidate was not found."
            )

        current_status = str(
            candidate.get("status") or ""
        ).strip().upper()

        if current_status not in {
            "CANDIDATE",
            "PREVIEWED",
        }:
            raise ValueError(
                "Only a candidate rule may be previewed."
            )

        safe_records = max(
            0,
            int(records_evaluated),
        )
        safe_affected = max(
            0,
            int(affected_record_count),
        )

        if safe_affected > safe_records:
            raise ValueError(
                "affected_record_count cannot exceed "
                "records_evaluated."
            )

        sql = f"""
        UPDATE `{self.profile_rule_candidate_table_id}`
        SET
            status = 'PREVIEWED',
            preview_records_evaluated = @records_evaluated,
            preview_affected_count = @affected_record_count,
            previewed_at = CURRENT_TIMESTAMP()
        WHERE organization_id = @organization_id
        AND candidate_id = @candidate_id
        AND status IN ('CANDIDATE', 'PREVIEWED')
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "records_evaluated",
                    "INT64",
                    safe_records,
                ),
                bigquery.ScalarQueryParameter(
                    "affected_record_count",
                    "INT64",
                    safe_affected,
                ),
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "candidate_id",
                    "STRING",
                    candidate_id,
                ),
            ]
        )

        query_job = self.client.query(
            sql,
            job_config=job_config,
        )
        query_job.result()

        if int(query_job.num_dml_affected_rows or 0) != 1:
            raise ValueError(
                "Unable to persist the tenant-scoped "
                "profile rule preview."
            )

        result = self.get_profile_rule_candidate(
            organization_id=organization_id,
            candidate_id=candidate_id,
        )

        if not result:
            raise RuntimeError(
                "Previewed profile rule candidate disappeared."
            )

        return result

    def reject_profile_rule_candidate(
        self,
        *,
        organization_id: str,
        candidate_id: str,
        rejected_by: str,
        rejection_reason: str,
        steward_guidance: str | None = None,
        rejection_evidence: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        organization_id = self._require_organization_id(
            organization_id
        )

        normalized_rejected_by = str(
            rejected_by or ""
        ).strip()

        normalized_reason = str(
            rejection_reason or ""
        ).strip()

        normalized_guidance = (
            str(steward_guidance or "").strip()
            or None
        )

        if not normalized_rejected_by:
            raise ValueError(
                "rejected_by is required."
            )

        if not normalized_reason:
            raise ValueError(
                "rejection_reason is required."
            )

        candidate = self.get_profile_rule_candidate(
            organization_id=organization_id,
            candidate_id=candidate_id,
        )

        if not candidate:
            raise ValueError(
                "Profile rule candidate was not found."
            )

        if (
            str(
                candidate.get("status") or ""
            ).strip().upper()
            != "PREVIEWED"
        ):
            raise ValueError(
                "Profile rule candidate must be previewed "
                "before steward rejection."
            )

        safe_evidence = (
            rejection_evidence
            if isinstance(rejection_evidence, list)
            else []
        )

        evidence_json = json.dumps(
            safe_evidence,
            ensure_ascii=False,
            default=str,
        )

        sql = f"""
        UPDATE `{self.profile_rule_candidate_table_id}`
        SET
            status = 'REJECTED',
            rejected_by = @rejected_by,
            rejected_at = CURRENT_TIMESTAMP(),
            rejection_reason = @rejection_reason,
            steward_guidance = @steward_guidance,
            rejection_evidence_json = @rejection_evidence_json
        WHERE organization_id = @organization_id
        AND candidate_id = @candidate_id
        AND status = 'PREVIEWED'
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "rejected_by",
                    "STRING",
                    normalized_rejected_by,
                ),
                bigquery.ScalarQueryParameter(
                    "rejection_reason",
                    "STRING",
                    normalized_reason,
                ),
                bigquery.ScalarQueryParameter(
                    "steward_guidance",
                    "STRING",
                    normalized_guidance,
                ),
                bigquery.ScalarQueryParameter(
                    "rejection_evidence_json",
                    "STRING",
                    evidence_json,
                ),
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "candidate_id",
                    "STRING",
                    candidate_id,
                ),
            ]
        )

        query_job = self.client.query(
            sql,
            job_config=job_config,
        )
        query_job.result()

        if int(
            query_job.num_dml_affected_rows or 0
        ) != 1:
            raise ValueError(
                "Unable to reject the tenant-scoped "
                "profile rule candidate."
            )

        result = self.get_profile_rule_candidate(
            organization_id=organization_id,
            candidate_id=candidate_id,
        )

        if not result:
            raise RuntimeError(
                "Rejected profile rule candidate disappeared."
            )

        return result

    def approve_profile_rule_candidate(
        self,
        *,
        organization_id: str,
        candidate_id: str,
        approved_by: str,
        activated_rule_id: str,
        activated_rule_version: int,
    ) -> dict[str, Any]:
        organization_id = self._require_organization_id(
            organization_id
        )

        normalized_approved_by = str(
            approved_by or ""
        ).strip()
        normalized_rule_id = str(
            activated_rule_id or ""
        ).strip()

        if not normalized_approved_by:
            raise ValueError("approved_by is required.")

        if not normalized_rule_id:
            raise ValueError(
                "activated_rule_id is required."
            )

        safe_version = int(activated_rule_version)

        if safe_version < 1:
            raise ValueError(
                "activated_rule_version must be at least 1."
            )

        candidate = self.get_profile_rule_candidate(
            organization_id=organization_id,
            candidate_id=candidate_id,
        )

        if not candidate:
            raise ValueError(
                "Profile rule candidate was not found."
            )

        if str(candidate.get("status") or "").upper() != "PREVIEWED":
            raise ValueError(
                "Profile rule candidate must be previewed "
                "before steward approval."
            )

        sql = f"""
        UPDATE `{self.profile_rule_candidate_table_id}`
        SET
            status = 'APPROVED',
            approved_by = @approved_by,
            approved_at = CURRENT_TIMESTAMP(),
            activated_rule_id = @activated_rule_id,
            activated_rule_version = @activated_rule_version
        WHERE organization_id = @organization_id
        AND candidate_id = @candidate_id
        AND status = 'PREVIEWED'
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "approved_by",
                    "STRING",
                    normalized_approved_by,
                ),
                bigquery.ScalarQueryParameter(
                    "activated_rule_id",
                    "STRING",
                    normalized_rule_id,
                ),
                bigquery.ScalarQueryParameter(
                    "activated_rule_version",
                    "INT64",
                    safe_version,
                ),
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "candidate_id",
                    "STRING",
                    candidate_id,
                ),
            ]
        )

        query_job = self.client.query(
            sql,
            job_config=job_config,
        )
        query_job.result()

        if int(query_job.num_dml_affected_rows or 0) != 1:
            raise ValueError(
                "Unable to approve the tenant-scoped "
                "profile rule candidate."
            )

        result = self.get_profile_rule_candidate(
            organization_id=organization_id,
            candidate_id=candidate_id,
        )

        if not result:
            raise RuntimeError(
                "Approved profile rule candidate disappeared."
            )

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
    def _require_profile_id(profile_id: str) -> str:
        normalized = str(profile_id or "").strip()

        if not normalized:
            raise ValueError("profile_id is required.")

        if not normalized.startswith("prof_"):
            raise ValueError(
                "profile_id must use the prof_ identifier standard."
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

    def get_remediation_evidence(
        self,
        *,
        organization_id: str,
        profile_run_id: str,
        rule_id: str,
        field_name: str,
        expected_count: int,
        max_rows: int = 500,
    ) -> list[dict[str, Any]]:
        """
        Return complete tenant-scoped persisted finding evidence required to
        construct a deterministic governed remediation artifact.

        Unlike get_finding_evidence(), this method is not for AI prompting or
        steward-facing sampling. It is server-side remediation evidence and
        fails closed when the persisted evidence is incomplete or ambiguous.
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

        safe_expected_count = int(expected_count)
        safe_max_rows = int(max_rows)

        if not normalized_profile_run_id:
            raise ValueError("profile_run_id is required.")

        if not normalized_rule_id:
            raise ValueError("rule_id is required.")

        if not normalized_field_name:
            raise ValueError("field_name is required.")

        if safe_expected_count < 1:
            raise ValueError(
                "expected_count must be at least 1."
            )

        if safe_max_rows < 1:
            raise ValueError(
                "max_rows must be at least 1."
            )

        if safe_expected_count > safe_max_rows:
            raise ValueError(
                "Deterministic remediation exceeds the governed "
                f"evidence limit of {safe_max_rows} findings."
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
            observed_value,
            proposed_value
        FROM `{self.findings_table_id}`
        WHERE organization_id = @organization_id
        AND profile_run_id = @profile_run_id
        AND UPPER(TRIM(rule_id)) = @rule_id
        AND LOWER(TRIM(field_name)) = @field_name
        ORDER BY source_row_id, finding_id
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
        ]

        rows = [
            dict(row)
            for row in self.client.query(
                sql,
                job_config=bigquery.QueryJobConfig(
                    query_parameters=params
                ),
            ).result()
        ]

        if len(rows) != safe_expected_count:
            raise ValueError(
                "Deterministic remediation evidence is incomplete. "
                f"Expected {safe_expected_count} persisted findings "
                f"but found {len(rows)}."
            )

        for row in rows:
            observed_value = row.get("observed_value")
            proposed_value = row.get("proposed_value")

            if observed_value is None:
                raise ValueError(
                    "Deterministic remediation evidence contains a "
                    "finding without observed_value."
                )

            if proposed_value is None:
                raise ValueError(
                    "Deterministic remediation evidence contains a "
                    "finding without proposed_value."
                )

        observed_to_proposed: dict[str, str] = {}

        for row in rows:
            observed = str(row["observed_value"])
            proposed = str(row["proposed_value"])

            existing = observed_to_proposed.get(observed)

            if existing is not None and existing != proposed:
                raise ValueError(
                    "Deterministic remediation evidence contains "
                    "conflicting proposed values for the same "
                    "observed value."
                )

            observed_to_proposed[observed] = proposed

        return rows

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
                source_context.database_name,
                source_context.schema_name,
                source_context.table_name,
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
        transformation_spec: dict[str, Any] | None = None,
        artifact_hash: str | None = None,
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
            remediation_transformation_spec =
                PARSE_JSON(@transformation_spec),
            remediation_artifact_hash =
                @artifact_hash,
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
            bigquery.ScalarQueryParameter(
                "transformation_spec",
                "STRING",
                (
                    json.dumps(
                        transformation_spec,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                    if transformation_spec is not None
                    else None
                ),
            ),
            bigquery.ScalarQueryParameter(
                "artifact_hash",
                "STRING",
                artifact_hash,
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
        transformation_spec: dict[str, Any] | None = None,
        artifact_hash: str | None = None,
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
