from __future__ import annotations

import json
import logging
import os
import re
import uuid
from typing import Any, Dict, List

from app.repositories.quality_profiler_repository import (
    QualityProfilerRepository,
)
from app.services.llm_service import LLMService
from app.services.prompt_builder import (
    build_dq_ai_recommendation_prompt,
    build_dq_remediation_prompt,
)


logger = logging.getLogger(__name__)


VALID_DIMENSIONS = {
    "COMPLETENESS",
    "VALIDITY",
    "UNIQUENESS",
    "STANDARDIZATION",
    "CONSISTENCY",
}

VALID_SEVERITIES = {
    "CRITICAL",
    "HIGH",
    "MEDIUM",
    "LOW",
}

VALID_IMPLEMENTATION_TYPES = {
    "SQL",
    "REGEX",
    "RULE_CONFIG",
    "ENRICHMENT",
    "MANUAL_REVIEW",
}

VALID_AUTOMATION_RECOMMENDATIONS = {
    "AUTO_FIX_CANDIDATE",
    "ENRICH_IF_AUTHORITATIVE_SOURCE_AVAILABLE",
    "STEWARD_REVIEW_REQUIRED",
    "REVIEW_REQUIRED",
}

FORBIDDEN_SQL_PATTERN = re.compile(
    r"\b("
    r"DELETE|DROP|TRUNCATE|MERGE|"
    r"ALTER|CREATE|INSERT|UPDATE"
    r")\b",
    flags=re.IGNORECASE,
)


class DqAiRecommendationService:
    """
    End-to-end DQ AI remediation orchestration.

    Flow:
        persisted DQ profile
        -> aggregate findings
        -> deterministic candidate fixes
        -> Claude enrichment
        -> strict validation
        -> tenant-aware persistence
    """

    def __init__(
        self,
        *,
        repository: QualityProfilerRepository | None = None,
        provider: str | None = None,
    ) -> None:
        self.repository = (
            repository
            or QualityProfilerRepository()
        )

        self.provider_name = (
            provider
            or os.getenv(
                "QUALITY_INTELLIGENCE_LLM_PROVIDER"
            )
            or "claude"
        ).strip().lower()

    # ---------------------------------------------------------
    # Public orchestration
    # ---------------------------------------------------------

    def analyze_profile(
        self,
        *,
        organization_id: str,
        profile_run_id: str,
    ) -> Dict[str, Any]:
        organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        profile_run_id = str(
            profile_run_id or ""
        ).strip()

        if not profile_run_id:
            raise ValueError(
                "profile_run_id is required."
            )

        # -----------------------------------------------------
        # 1. Load persisted profile summary.
        # -----------------------------------------------------

        profile = (
            self.repository.get_profile_summary(
                organization_id=organization_id,
                profile_run_id=profile_run_id,
            )
        )

        if not profile:
            raise ValueError(
                "DQ profile was not found for "
                "the authenticated organization."
            )

        domain = str(
            profile.get("domain")
            or ""
        ).strip().upper()

        total_records = int(
            profile.get("total_records")
            or 0
        )

        avg_record_score = float(
            profile.get("avg_record_score")
            or 0.0
        )

        # Preserve the persisted physical-source -> canonical-field
        # mapping so deterministic recommendations can carry the exact
        # source column used by governed execution.
        column_mappings = self._normalize_column_mappings(
            profile.get("column_mappings")
        )

        # -----------------------------------------------------
        # 2. Aggregate persisted findings.
        # -----------------------------------------------------

        finding_rows = (
            self.repository.get_finding_aggregates(
                organization_id=organization_id,
                profile_run_id=profile_run_id,
            )
        )

        if not finding_rows:
            return {
                "success": True,
                "profile_run_id": profile_run_id,
                "domain": domain,
                "recommendation_count": 0,
                "overall_analysis": {
                    "dq_health_summary": (
                        "No persisted DQ findings "
                        "require AI remediation."
                    ),
                    "highest_priority_issue": None,
                    "recommended_next_action": (
                        "Continue monitoring."
                    ),
                    "confidence": 1.0,
                },
                "recommendations": [],
            }

        # -----------------------------------------------------
        # 3. Build deterministic candidate recommendations.
        # -----------------------------------------------------

        deterministic_recommendations = (
            self._build_deterministic_recommendations(
                organization_id=organization_id,
                profile_run_id=profile_run_id,
                domain=domain,
                total_records=total_records,
                finding_rows=finding_rows,
                column_mappings=column_mappings,
            )
        )
        analysis_recommendations = (
            deterministic_recommendations[:10]
        )

        # -----------------------------------------------------
        # 4. Build tenant-safe Claude prompt.
        # -----------------------------------------------------

        prompt = (
            build_dq_ai_recommendation_prompt(
                organization_id=organization_id,
                profile_run_id=profile_run_id,
                domain=domain,
                total_records=total_records,
                avg_record_score=avg_record_score,
                deterministic_recommendations=(
                    analysis_recommendations
                ),
            )
        )

        # -----------------------------------------------------
        # 5. Call Claude through existing LLMService.
        # -----------------------------------------------------

        raw_response = (
            LLMService(
                provider=self.provider_name
            ).ask(
                prompt,
                organization_id=organization_id,
            )
        )

        # -----------------------------------------------------
        # 6. Parse + validate JSON.
        # -----------------------------------------------------

        try:
            parsed = self._extract_json_object(
            raw_response
        )

            parsed["profile_run_id"] = profile_run_id
            parsed["domain"] = domain

            raw_recommendations = parsed.get(
                "recommendations",
                [],
            )

            if isinstance(
                raw_recommendations,
                list,
            ):
                parsed_recommendation_count = len(
                    raw_recommendations
                )

                parsed_recommendation_ids = [
                    str(
                        item.get(
                            "recommendation_id"
                        )
                        or ""
                    ).strip()
                    for item in raw_recommendations
                    if isinstance(
                        item,
                        dict,
                    )
                ]
            else:
                parsed_recommendation_count = -1
                parsed_recommendation_ids = []

            logger.warning(
                "DQ AI PARSED DEBUG "
                "profile_run_id=%s "
                "domain=%s "
                "top_level_keys=%s "
                "recommendations_type=%s "
                "recommendation_count=%s "
                "recommendation_ids=%s",
                profile_run_id,
                domain,
                list(parsed.keys()),
                type(
                    raw_recommendations
                ).__name__,
                parsed_recommendation_count,
                parsed_recommendation_ids,
            )

            logger.warning(
                "DQ AI RAW RESPONSE "
                "profile_run_id=%s "
                "raw_response_preview=%r",
                profile_run_id,
                str(raw_response)[:15000],
            )

        except Exception:
            logger.exception(
                "Failed to parse DQ AI response. "
                "organization_id=%s "
                "profile_run_id=%s "
                "domain=%s "
                "raw_response_preview=%r",
                organization_id,
                profile_run_id,
                domain,
                str(raw_response)[:15000],
            )
            raise

        validated = self._validate_ai_response(
            payload=parsed,
            organization_id=organization_id,
            profile_run_id=profile_run_id,
            domain=domain,
            deterministic_recommendations=(
                analysis_recommendations
            ),
        )

        self.repository.save_ai_recommendations(
            organization_id=organization_id,
            profile_run_id=profile_run_id,
            domain=domain,
            recommendations=validated["recommendations"],
            provider=self.provider_name,
            overall_analysis=validated["overall_analysis"],
        )

        # -----------------------------------------------------
        # 8. Update existing dashboard summary counters.
        # -----------------------------------------------------

        self.repository.update_profile_ai_counts(
            organization_id=organization_id,
            profile_run_id=profile_run_id,
            recommendation_count=len(
                validated["recommendations"]
            ),
        )

        logger.info(
            "DQ AI analysis completed. "
            "organization_id=%s "
            "profile_run_id=%s "
            "domain=%s "
            "recommendations=%s "
            "provider=%s",
            organization_id,
            profile_run_id,
            domain,
            len(
                validated["recommendations"]
            ),
            self.provider_name,
        )

        return {
            "success": True,
            "profile_run_id": profile_run_id,
            "domain": domain,
            "recommendation_count": len(
                validated["recommendations"]
            ),
            "overall_analysis": (
                validated["overall_analysis"]
            ),
            "recommendations": (
                validated["recommendations"]
            ),
        }
    # ---------------------------------------------------------
    # Generate implementation-ready remediation
    # ---------------------------------------------------------

    def generate_remediation(
        self,
        *,
        organization_id: str,
        recommendation_id: str,
        source_table: str = "source_table",
        sql_dialect: str = "BIGQUERY",
        artifact_preference: str = "AUTO",
    ) -> Dict[str, Any]:
        """
        Generate one implementation-ready remediation artifact for an
        existing tenant-scoped DQ AI recommendation.

        The LLM proposes the artifact. This service then applies deterministic
        server-side safety validation before anything is persisted.
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

        source_table = str(
            source_table or "source_table"
        ).strip()

        if not source_table:
            raise ValueError(
                "source_table is required."
            )

        sql_dialect = str(
            sql_dialect or "BIGQUERY"
        ).strip().upper()

        if sql_dialect not in {
            "BIGQUERY",
            "SNOWFLAKE",
            "DATABRICKS",
            "GENERIC",
            "AZURE_SQL",
        }:
            raise ValueError(
                "Unsupported SQL dialect."
            )

        artifact_preference = str(
            artifact_preference or "AUTO"
        ).strip().upper()

        if artifact_preference not in {
            "AUTO",
            "SQL",
            "REGEX",
        }:
            raise ValueError(
                "Unsupported artifact_preference."
            )

        # -----------------------------------------------------
        # 1. Load existing recommendation tenant-safely.
        # -----------------------------------------------------

        recommendation = (
            self.repository.get_ai_recommendation(
                organization_id=organization_id,
                recommendation_id=recommendation_id,
            )
        )

        if not recommendation:
            raise ValueError(
                "DQ AI recommendation was not found "
                "for this organization."
            )

        recommendation_org_id = str(
            recommendation.get("organization_id")
            or ""
        ).strip()

        if recommendation_org_id != organization_id:
            raise ValueError(
                "DQ AI recommendation belongs to "
                "a different organization."
            )

        profile_run_id = str(
            recommendation.get("profile_run_id")
            or ""
        ).strip() or None

        domain = str(
            recommendation.get("domain")
            or ""
        ).strip().upper()

        rule_id = str(
            recommendation.get("rule_id")
            or ""
        ).strip().upper()

        field_name = (
            str(
                recommendation.get("field_name")
                or ""
            ).strip()
            or None
        )

        latest_change_request = None
        revision_guidance = None

        approval_status = str(
            recommendation.get(
                "remediation_approval_status"
            )
            or ""
        ).strip().upper()

        if approval_status == "CHANGES_REQUESTED":
            latest_change_request = (
                self.repository
                .get_latest_remediation_change_request(
                    organization_id=organization_id,
                    recommendation_id=recommendation_id,
                )
            )

            if latest_change_request:
                revision_guidance = str(
                    latest_change_request.get("note")
                    or ""
                ).strip()

                if revision_guidance:
                    logger.info(
                        "Applying steward remediation revision guidance. "
                        "organization_id=%s "
                        "recommendation_id=%s",
                        organization_id,
                        recommendation_id,
                    )


        # -----------------------------------------------------
        # 2. Build tenant-safe remediation prompt.
        # -----------------------------------------------------

        prompt = build_dq_remediation_prompt(
            organization_id=organization_id,
            revision_guidance=revision_guidance,
            recommendation=recommendation,
            source_table=source_table,
            sql_dialect=sql_dialect,
            artifact_preference=artifact_preference,
        )

        # -----------------------------------------------------
        # 3. Generate remediation through configured LLM.
        # -----------------------------------------------------

        raw_response = (
            LLMService(
                provider=self.provider_name
            ).ask(
                prompt,
                organization_id=organization_id,
            )
        )

        # -----------------------------------------------------
        # 4. Parse strict JSON response.
        # -----------------------------------------------------

        try:
            parsed = self._extract_json_object(
                raw_response
            )

        except Exception:
            logger.exception(
                "Failed to parse DQ remediation response. "
                "organization_id=%s "
                "recommendation_id=%s "
                "raw_response_preview=%r",
                organization_id,
                recommendation_id,
                str(raw_response)[:15000],
            )
            raise

        if not isinstance(parsed, dict):
            raise ValueError(
                "DQ remediation response must be "
                "a JSON object."
            )

        # -----------------------------------------------------
        # 5. Validate recommendation identity.
        # -----------------------------------------------------

        returned_recommendation_id = str(
            parsed.get("recommendation_id")
            or recommendation_id
        ).strip()

        if (
            returned_recommendation_id
            != recommendation_id
        ):
            raise ValueError(
                "Generated remediation returned an "
                "unexpected recommendation_id."
            )

        # The prompt intentionally strips tenant identifiers.
        # If an organization_id is nevertheless returned, it
        # must match the authenticated tenant.
        returned_org_id = str(
            parsed.get("organization_id")
            or organization_id
        ).strip()

        if returned_org_id != organization_id:
            raise ValueError(
                "Generated remediation belongs to "
                "a different organization."
            )

        # -----------------------------------------------------
        # 6. Normalize artifact contract returned by prompt.
        # -----------------------------------------------------

        artifact_type = str(
            parsed.get("artifact_type")
            or parsed.get("implementation_type")
            or ""
        ).strip().upper()

        if artifact_type not in {
            "SQL",
            "REGEX",
            "MANUAL_REVIEW",
        }:
            raise ValueError(
                "Generated remediation contains an "
                "unsupported artifact_type."
            )

        # When a steward explicitly requests SQL, the LLM may
        # return either:
        #   - SQL: a controlled UPDATE, including regex predicates
        #     inside the SQL WHERE clause when appropriate, or
        #   - MANUAL_REVIEW: when no deterministic correction is safe.
        #
        # A standalone REGEX artifact is not accepted for an explicit
        # SQL request because governed execution requires SQL.
        if (
            artifact_preference == "SQL"
            and artifact_type == "REGEX"
        ):
            raise ValueError(
                "SQL remediation was explicitly requested, but "
                "the generated artifact was standalone REGEX. "
                "Return SQL with any regex embedded in its WHERE "
                "clause, or MANUAL_REVIEW when correction is not "
                "deterministic."
            )

        if (
            artifact_preference == "REGEX"
            and artifact_type == "SQL"
        ):
            raise ValueError(
                "REGEX remediation was explicitly requested, but "
                "the generated artifact was SQL. "
                "Return REGEX or MANUAL_REVIEW for this source."
            )

        remediation_sql = str(
            parsed.get("remediation_sql")
            or parsed.get("suggested_sql")
            or ""
        ).strip() or None

        remediation_regex = str(
            parsed.get("remediation_regex")
            or parsed.get("suggested_regex")
            or ""
        ).strip() or None

        reasoning_summary = str(
            parsed.get("reasoning_summary")
            or parsed.get("remediation_summary")
            or parsed.get("recommended_remediation")
            or recommendation.get(
                "recommended_remediation"
            )
            or recommendation.get(
                "suggested_action"
            )
            or ""
        ).strip() or None

        # -----------------------------------------------------
        # 6.5 Deterministic SQL-backed standardization override
        # -----------------------------------------------------

        if (
            rule_id == "FULL_NAME_STANDARDIZATION"
            and str(sql_dialect or "").strip().upper() == "AZURE_SQL"
        ):
            source_column = str(
                recommendation.get("source_column") or ""
            ).strip()

            finding_count_raw = (
                recommendation.get("finding_count")
                or 0
            )

            try:
                finding_count = int(finding_count_raw)
            except (TypeError, ValueError):
                finding_count = 0

            if not source_column:
                raise ValueError(
                    "FULL_NAME_STANDARDIZATION remediation requires "
                    "the persisted physical source_column."
                )

            if finding_count <= 0:
                raise ValueError(
                    "FULL_NAME_STANDARDIZATION remediation requires "
                    "a valid persisted finding_count."
                )

            remediation_evidence = (
                self.repository.get_remediation_evidence(
                    organization_id=organization_id,
                    profile_run_id=profile_run_id,
                    rule_id=rule_id,
                    field_name=field_name,
                    expected_count=finding_count,
                )
            )

            remediation_sql = (
                self._build_azure_observed_to_proposed_update(
                    source_table=source_table,
                    source_column=source_column,
                    evidence_rows=remediation_evidence,
                )
            )

            artifact_type = "SQL"
            remediation_regex = None

            reasoning_summary = (
                "Deterministic Azure SQL remediation was constructed "
                "server-side from complete persisted "
                "observed_value-to-proposed_value finding evidence. "
                "The AI model did not receive or construct the row-level "
                "customer values. The UPDATE is bounded to persisted "
                "affected values and requires steward approval before "
                "governed execution."
            )

        # -----------------------------------------------------
        # 7. Enforce artifact-specific server-side safety.
        # -----------------------------------------------------

        safety_status = "REVIEW_REQUIRED"
        steward_approval_required = True

        if artifact_type == "SQL":
            if not remediation_sql:
                raise ValueError(
                    "SQL remediation must include "
                    "remediation_sql."
                )

            if remediation_regex:
                raise ValueError(
                    "SQL remediation must not also "
                    "include remediation_regex."
                )

            remediation_sql = (
                self._validate_generated_remediation_sql(
                    sql=remediation_sql,
                    source_table=source_table,
                )
            )

            safety_status = "REVIEW_REQUIRED"

        elif artifact_type == "REGEX":
            if not remediation_regex:
                raise ValueError(
                    "REGEX remediation must include "
                    "remediation_regex."
                )

            if remediation_sql:
                raise ValueError(
                    "REGEX remediation must not also "
                    "include remediation_sql."
                )

            remediation_regex = (
                self._validate_generated_remediation_regex(
                    remediation_regex
                )
            )

            # Regex is a validation/standardization artifact.
            # It still requires steward approval before use.
            safety_status = "SAFE_DIAGNOSTIC"

        else:
            # MANUAL_REVIEW intentionally carries no executable
            # SQL or regex.
            remediation_sql = None
            remediation_regex = None
            safety_status = "REVIEW_REQUIRED"

        # -----------------------------------------------------
        # 7.5 PHONE_NUMBER_JUNK_VALUE hard safety rules.
        # -----------------------------------------------------

        if (
            rule_id == "PHONE_NUMBER_JUNK_VALUE"
            and artifact_type == "SQL"
        ):
            phone_sql_block_reason = (
                self._phone_junk_sql_block_reason(
                    sql=remediation_sql or "",
                    recommendation=recommendation,
                )
            )

            if phone_sql_block_reason:
                logger.warning(
                    "Blocking unsafe phone junk remediation SQL. "
                    "organization_id=%s recommendation_id=%s reason=%s",
                    organization_id,
                    recommendation_id,
                    phone_sql_block_reason,
                )

                artifact_type = "MANUAL_REVIEW"
                remediation_sql = None
                remediation_regex = None
                safety_status = "REVIEW_REQUIRED"

                safety_note = (
                    "Executable phone-number remediation was blocked by "
                    "deterministic server-side safety validation: "
                    f"{phone_sql_block_reason} Manual steward review is "
                    "required; no SQL artifact was persisted."
                )

                reasoning_summary = (
                    f"{reasoning_summary} {safety_note}".strip()
                    if reasoning_summary
                    else safety_note
                )

        # -----------------------------------------------------
        # 8. GTIN-specific hard safety rules.
        # -----------------------------------------------------

        if rule_id == "GTIN_CHECKSUM_VALIDITY":
            # A recomputed check digit does not prove the base
            # digits are commercially correct. Unless authoritative
            # base digits are supplied separately, do not emit an
            # executable correction.
            if artifact_type == "SQL":
                logger.warning(
                    "Blocking executable GTIN checksum remediation. "
                    "organization_id=%s recommendation_id=%s",
                    organization_id,
                    recommendation_id,
                )

                artifact_type = "MANUAL_REVIEW"
                remediation_sql = None
                remediation_regex = None
                safety_status = "REVIEW_REQUIRED"

                checksum_note = (
                    "GTIN checksum correction requires authoritative "
                    "confirmation of the preceding digits; recalculating "
                    "the final check digit alone does not prove the GTIN "
                    "is commercially correct."
                )

                reasoning_summary = (
                    f"{reasoning_summary} {checksum_note}".strip()
                    if reasoning_summary
                    else checksum_note
                )

        if (
            domain == "PRODUCT"
            and (
                (field_name or "").strip().lower() == "gtin"
                or rule_id.startswith("GTIN_")
            )
            and artifact_type == "SQL"
        ):
            # Even deterministic-looking GTIN updates remain a
            # steward-controlled proposal.
            steward_approval_required = True
            safety_status = "REVIEW_REQUIRED"

        # -----------------------------------------------------
        # 9. Persist immutable remediation version first.
        # -----------------------------------------------------

        generated_from_feedback_event_id = None

        if (
            approval_status == "CHANGES_REQUESTED"
            and latest_change_request
        ):
            generated_from_feedback_event_id = str(
                latest_change_request.get(
                    "feedback_event_id"
                )
                or ""
            ).strip() or None

        latest_version_number = (
            self.repository.get_latest_remediation_version_number(
                organization_id=organization_id,
                recommendation_id=recommendation_id,
            )
        )

        if latest_version_number == 0:
            generation_reason = "INITIAL_GENERATION"
        elif approval_status == "CHANGES_REQUESTED":
            generation_reason = "STEWARD_REVISION"
        else:
            generation_reason = "MANUAL_REGENERATION"

        version_result = (
            self.repository.save_remediation_version(
                organization_id=organization_id,
                recommendation_id=recommendation_id,
                profile_run_id=profile_run_id,
                artifact_type=artifact_type,
                remediation_sql=remediation_sql,
                remediation_regex=remediation_regex,
                sql_dialect=sql_dialect,
                safety_status=safety_status,
                reasoning_summary=reasoning_summary,
                steward_approval_required=(
                    steward_approval_required
                ),
                generation_reason=generation_reason,
                generated_from_feedback_event_id=(
                    generated_from_feedback_event_id
                ),
                revision_guidance=revision_guidance,
                ai_provider=self.provider_name,
                ai_model=None,
                generated_by=None,
            )
        )

        # -----------------------------------------------------
        # 10. Update current recommendation state.
        # -----------------------------------------------------

        self.repository.update_ai_recommendation_remediation(
            organization_id=organization_id,
            recommendation_id=recommendation_id,
            remediation_sql=remediation_sql,
            remediation_regex=remediation_regex,
            artifact_type=artifact_type,
            sql_dialect=sql_dialect,
            safety_status=safety_status,
            reasoning_summary=reasoning_summary,
            steward_approval_required=(
                steward_approval_required
            ),
        )

        # -----------------------------------------------------
        # 10.4 New version requires a fresh steward decision.
        # -----------------------------------------------------

        if approval_status in {
            "APPROVED",
            "REJECTED",
            "CHANGES_REQUESTED",
        }:
            self.repository.reset_remediation_approval_for_new_version(
                organization_id=organization_id,
                recommendation_id=recommendation_id,
            )

        logger.info(
            "DQ remediation generated. "
            "organization_id=%s "
            "recommendation_id=%s "
            "remediation_version_id=%s "
            "version_number=%s "
            "artifact_type=%s "
            "safety_status=%s "
            "has_sql=%s "
            "has_regex=%s "
            "provider=%s",
            organization_id,
            recommendation_id,
            version_result.get(
                "remediation_version_id"
            ),
            version_result.get(
                "version_number"
            ),
            artifact_type,
            safety_status,
            bool(remediation_sql),
            bool(remediation_regex),
            self.provider_name,
        )

        # -----------------------------------------------------
        # 10.5 Return API-ready result.
        # -----------------------------------------------------

        return {
            "success": True,
            "organization_id": organization_id,
            "recommendation_id":
                recommendation_id,
            "profile_run_id":
                profile_run_id,
            "domain":
                domain,
            "rule_id":
                rule_id,
            "field_name":
                field_name,
            "remediation_version_id":
                version_result.get(
                    "remediation_version_id"
                ),
            "version_number":
                version_result.get(
                    "version_number"
                ),
            "artifact_type":
                artifact_type,
            "remediation_sql":
                remediation_sql,
            "remediation_regex":
                remediation_regex,
            "sql_dialect":
                sql_dialect,
            "safety_status":
                safety_status,
            "reasoning_summary":
                reasoning_summary,
            "steward_approval_required":
                steward_approval_required,
        }

    @staticmethod
    def _azure_sql_literal(value: Any) -> str:
        """
        Render a SQL Server / Azure SQL string literal.

        Values come only from persisted deterministic DQ evidence.
        """
        return "'" + str(value).replace("'", "''") + "'"


    @classmethod
    def _build_azure_observed_to_proposed_update(
        cls,
        *,
        source_table: str,
        source_column: str,
        evidence_rows: list[dict[str, Any]],
    ) -> str:
        """
        Build one bounded Azure SQL UPDATE from complete persisted
        observed_value -> proposed_value evidence.
        """
        normalized_table = str(source_table or "").strip()
        normalized_column = str(source_column or "").strip()

        if not normalized_table:
            raise ValueError(
                "Azure SQL remediation source_table is required."
            )

        if not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*",
            normalized_table,
        ):
            raise ValueError(
                "Azure SQL remediation requires a schema-qualified "
                "source table."
            )

        if not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*",
            normalized_column,
        ):
            raise ValueError(
                "Azure SQL remediation source_column is invalid."
            )

        mappings: dict[str, str] = {}

        for row in evidence_rows:
            observed = row.get("observed_value")
            proposed = row.get("proposed_value")

            if observed is None or proposed is None:
                raise ValueError(
                    "Azure SQL deterministic remediation requires "
                    "complete observed_value -> proposed_value evidence."
                )

            observed_text = str(observed)
            proposed_text = str(proposed)

            existing = mappings.get(observed_text)

            if existing is not None and existing != proposed_text:
                raise ValueError(
                    "Conflicting deterministic remediation mappings "
                    "were found for the same observed value."
                )

            mappings[observed_text] = proposed_text

        if not mappings:
            raise ValueError(
                "No deterministic remediation mappings were available."
            )

        case_lines = []

        for observed, proposed in mappings.items():
            case_lines.append(
                "        WHEN "
                f"{cls._azure_sql_literal(observed)} "
                "THEN "
                f"{cls._azure_sql_literal(proposed)}"
            )

        where_values = ",\n        ".join(
            cls._azure_sql_literal(observed)
            for observed in mappings
        )

        case_sql = "\n".join(case_lines)

        return (
            f"UPDATE {normalized_table}\n"
            f"SET {normalized_column} = CASE {normalized_column}\n"
            f"{case_sql}\n"
            f"        ELSE {normalized_column}\n"
            f"    END\n"
            f"WHERE {normalized_column} IN (\n"
            f"        {where_values}\n"
            f");"
        )

    @staticmethod
    def _validate_generated_remediation_regex(
            pattern: str,
        ) -> str:
            normalized = str(
                pattern or ""
            ).strip()

            if not normalized:
                raise ValueError(
                    "Generated remediation regex is empty."
                )

            if len(normalized) > 500:
                raise ValueError(
                    "Generated remediation regex is too long."
                )

            try:
                re.compile(normalized)
            except re.error as exc:
                raise ValueError(
                    "Generated remediation contains "
                    "an invalid regex."
                ) from exc

            return normalized

    @staticmethod
    def _validate_generated_remediation_sql(
            *,
            sql: str,
            source_table: str,
        ) -> str:
            """
            Allow one controlled UPDATE statement only.

            This validator intentionally rejects all other DML/DDL,
            comments, multi-statement SQL, and UPDATE statements that
            do not contain a restrictive WHERE clause.
            """
            normalized = str(
                sql or ""
            ).strip()

            if not normalized:
                raise ValueError(
                    "Generated remediation SQL is empty."
                )

            if len(normalized) > 5000:
                raise ValueError(
                    "Generated remediation SQL is too long."
                )

            if "--" in normalized or "/*" in normalized:
                raise ValueError(
                    "Generated remediation SQL must not "
                    "contain comments."
                )

            # Allow one optional trailing semicolon, but no
            # additional statements.
            without_trailing_semicolon = (
                normalized[:-1].rstrip()
                if normalized.endswith(";")
                else normalized
            )

            if ";" in without_trailing_semicolon:
                raise ValueError(
                    "Generated remediation SQL must contain "
                    "exactly one statement."
                )

            upper_sql = without_trailing_semicolon.upper()

            if not upper_sql.startswith("UPDATE "):
                raise ValueError(
                    "Generated remediation SQL must be "
                    "a controlled UPDATE statement."
                )

            forbidden = re.compile(
                r"\b("
                r"DELETE|DROP|TRUNCATE|MERGE|ALTER|"
                r"CREATE|INSERT|GRANT|REVOKE|CALL|"
                r"EXECUTE"
                r")\b",
                flags=re.IGNORECASE,
            )

            if forbidden.search(
                without_trailing_semicolon
            ):
                raise ValueError(
                    "Generated remediation SQL contains "
                    "a forbidden operation."
                )

            where_match = re.search(
                r"\bWHERE\b",
                without_trailing_semicolon,
                flags=re.IGNORECASE,
            )

            if not where_match:
                raise ValueError(
                    "Generated remediation UPDATE must "
                    "contain a WHERE clause."
                )

            where_clause = (
                without_trailing_semicolon[
                    where_match.end():
                ]
                .strip()
            )

            if not where_clause:
                raise ValueError(
                    "Generated remediation UPDATE has "
                    "an empty WHERE clause."
                )

            normalized_where = re.sub(
                r"\s+",
                "",
                where_clause,
            ).upper()

            if normalized_where in {
                "TRUE",
                "1=1",
                "(1=1)",
            }:
                raise ValueError(
                    "Generated remediation UPDATE WHERE "
                    "clause is not restrictive."
                )

            # Basic table-target guard. It deliberately avoids
            # attempting to be a full SQL parser.
            expected_table = str(
                source_table or ""
            ).strip()

            if (
                expected_table
                and expected_table != "source_table"
            ):
                update_target_match = re.match(
                    r"^\s*UPDATE\s+(`[^`]+`|\"[^\"]+\"|[^\s]+)",
                    without_trailing_semicolon,
                    flags=re.IGNORECASE,
                )

                if update_target_match:
                    actual_target = (
                        update_target_match
                        .group(1)
                        .strip("`\"")
                    )

                    if actual_target != expected_table.strip("`\""):
                        raise ValueError(
                            "Generated remediation SQL targets "
                            "an unexpected source table."
                        )

            return (
                without_trailing_semicolon
                + ";"
            )

    @staticmethod
    def _phone_junk_sql_block_reason(
            *,
            sql: str,
            recommendation: Dict[str, Any],
        ) -> str | None:
            """Fail closed unless phone-junk SQL uses complete exact evidence."""
            source_column = str(
                recommendation.get("source_column") or ""
            ).strip()
    
            if not source_column:
                return "the persisted physical source_column is missing."
    
            evidence_samples = recommendation.get("evidence_samples") or []
            if not isinstance(evidence_samples, list):
                return "persisted evidence_samples are unavailable."
    
            observed_values: list[str] = []
            for sample in evidence_samples:
                if not isinstance(sample, dict):
                    continue
                value = sample.get("observed_value")
                if value is None:
                    continue
                normalized_value = str(value)
                if normalized_value and normalized_value not in observed_values:
                    observed_values.append(normalized_value)
    
            affected_count_raw = (
                recommendation.get("affected_record_count")
                or recommendation.get("finding_count")
                or 0
            )
            try:
                affected_count = int(affected_count_raw)
            except (TypeError, ValueError):
                affected_count = 0
    
            if affected_count <= 0:
                return "the persisted affected-record count is missing or invalid."
    
            if len(observed_values) < affected_count:
                return (
                    "the persisted evidence does not enumerate every affected "
                    "junk value."
                )
    
            normalized_sql = str(sql or "").strip()
            upper_sql = normalized_sql.upper()
    
            if re.search(
                r"\b(?:NOT\s+REGEXP_LIKE|NOT\s+REGEXP_CONTAINS|"
                r"NOT\s+RLIKE|REGEXP_LIKE|REGEXP_CONTAINS|RLIKE)\b",
                upper_sql,
            ):
                return "broad regex predicates are not allowed for corrective nullification."
    
            identifier = re.escape(source_column)
            set_match = re.search(
                rf"\bSET\s+(?:[`\"]?{identifier}[`\"]?)\s*=\s*NULL\b",
                normalized_sql,
                flags=re.IGNORECASE,
            )
            if not set_match:
                return (
                    "the UPDATE does not set the authoritative physical "
                    f"source column {source_column!r} to NULL."
                )
    
            where_match = re.search(
                r"\bWHERE\b(?P<where>.+)$",
                normalized_sql.rstrip(";"),
                flags=re.IGNORECASE | re.DOTALL,
            )
            where_clause = where_match.group("where") if where_match else ""
    
            if not re.search(
                rf"(?:[`\"]?{identifier}[`\"]?)\s*(?:=|\bIN\s*\()",
                where_clause,
                flags=re.IGNORECASE,
            ):
                return (
                    "the WHERE clause is not an equality or IN allowlist on "
                    f"the physical source column {source_column!r}."
                )
    
            for observed_value in observed_values:
                escaped_literal = observed_value.replace("'", "''")
                if f"'{escaped_literal}'" not in where_clause:
                    return (
                        "the WHERE clause does not contain every exact persisted "
                        "junk value."
                    )
    
            return None
    
        # ---------------------------------------------------------
        # Deterministic candidate fixes
        # ---------------------------------------------------------
    
    def _build_deterministic_recommendations(
            self,
            *,
            organization_id: str,
            profile_run_id: str,
            domain: str,
            total_records: int,
            finding_rows: List[Dict[str, Any]],
            column_mappings: List[Dict[str, Any]],
        ) -> List[Dict[str, Any]]:
            recommendations: List[Dict[str, Any]] = []
    
            for row in finding_rows:
                rule_id = str(row.get("rule_id") or "").strip()
    
                field_name = (
                    str(row.get("field_name") or "").strip()
                    or None
                )
    
                source_column = (
                    self._source_column_for_target(
                        column_mappings,
                        field_name,
                    )
                    or (
                        str(row.get("source_column") or "").strip()
                        or None
                    )
                )
    
                dimension = str(
                    row.get("dimension") or ""
                ).strip().upper()
    
                severity = str(
                    row.get("severity") or "MEDIUM"
                ).strip().upper()
    
                finding_count = int(
                    row.get("finding_count") or 0
                )
    
                affected_record_count = int(
                    row.get("affected_record_count") or 0
                )
    
                affected_percent = (
                    round(
                        (affected_record_count / total_records) * 100.0,
                        2,
                    )
                    if total_records > 0
                    else 0.0
                )
    
                candidate = self._build_candidate_fix(
                    domain=domain,
                    rule_id=rule_id,
                    field_name=field_name,
                    dimension=dimension,
                    severity=severity,
                    affected_percent=affected_percent,
                )
    
                # Bounded samples are for AI/UI explanation only.
                # Governed remediation must fetch complete evidence separately
                # whenever an executable artifact requires it.
                evidence_samples: List[Dict[str, Any]] = []
    
                if field_name:
                    evidence_samples = (
                        self.repository.get_finding_evidence(
                            organization_id=organization_id,
                            profile_run_id=profile_run_id,
                            rule_id=rule_id,
                            field_name=field_name,
                            limit=5,
                        )
                    )
    
                # Append once per authoritative finding aggregate.
                recommendations.append(
                    {
                        "organization_id": organization_id,
                        "profile_run_id": profile_run_id,
                        "recommendation_id": (
                            f"dqr_{uuid.uuid4().hex[:20]}"
                        ),
                        "rule_id": rule_id,
                        "field_name": field_name,
                        "source_column": source_column,
                        "dimension": dimension,
                        "severity": severity,
                        "finding_count": finding_count,
                        "affected_record_count": affected_record_count,
                        "affected_percent": affected_percent,
                        "evidence_samples": evidence_samples,
                        **candidate,
                    }
                )
    
            return recommendations
    
    def _build_candidate_fix(
            self,
            *,
            domain: str,
            rule_id: str,
            field_name: str | None,
            dimension: str,
            severity: str,
            affected_percent: float,
        ) -> Dict[str, Any]:
            field = (
                field_name
                or "record"
            )
    
            if dimension == "UNIQUENESS":
                return {
                    "recommendation_title": (
                        f"Resolve duplicate "
                        f"{field} values"
                    ),
                    "suggested_action":
                        "DEDUPLICATE_RECORDS",
                    "suggested_rule_type":
                        "UNIQUENESS",
                    "suggested_sql": (
                        "SELECT\n"
                        f"  {field},\n"
                        "  COUNT(*) AS "
                        "duplicate_count\n"
                        "FROM source_table\n"
                        f"WHERE {field} IS NOT NULL\n"
                        f"GROUP BY {field}\n"
                        "HAVING COUNT(*) > 1\n"
                        "ORDER BY "
                        "duplicate_count DESC;"
                    ),
                    "suggested_regex": None,
                    "suggested_threshold": (
                        self._default_threshold(
                            severity=severity,
                            affected_percent=(
                                affected_percent
                            ),
                        )
                    ),
                    "automation_recommendation":
                        "STEWARD_REVIEW_REQUIRED",
                    "automation_confidence": 0.60,
                }
    
            if dimension == "COMPLETENESS":
                return {
                    "recommendation_title": (
                        f"Improve {field} "
                        "completeness"
                    ),
                    "suggested_action":
                        "POPULATE_MISSING_VALUES",
                    "suggested_rule_type":
                        "COMPLETENESS",
                    "suggested_sql": (
                        "SELECT *\n"
                        "FROM source_table\n"
                        f"WHERE {field} IS NULL\n"
                        f"   OR TRIM("
                        f"CAST({field} AS STRING)"
                        f") = '';"
                    ),
                    "suggested_regex": None,
                    "suggested_threshold": (
                        self._default_threshold(
                            severity=severity,
                            affected_percent=(
                                affected_percent
                            ),
                        )
                    ),
                    "automation_recommendation": (
                        "ENRICH_IF_AUTHORITATIVE_"
                        "SOURCE_AVAILABLE"
                    ),
                    "automation_confidence": 0.75,
                }
    
            if dimension == "VALIDITY":
                regex_pattern = (
                    self._suggest_regex(
                        field
                    )
                )
    
                return {
                    "recommendation_title": (
                        f"Correct invalid "
                        f"{field} values"
                    ),
                    "suggested_action":
                        "CORRECT_INVALID_VALUES",
                    "suggested_rule_type":
                        "VALIDITY",
                    "suggested_sql": (
                        self._build_validity_sql(
                            field=field,
                            regex_pattern=(
                                regex_pattern
                            ),
                        )
                    ),
                    "suggested_regex":
                        regex_pattern,
                    "suggested_threshold": (
                        self._default_threshold(
                            severity=severity,
                            affected_percent=(
                                affected_percent
                            ),
                        )
                    ),
                    "automation_recommendation":
                        "AUTO_FIX_CANDIDATE",
                    "automation_confidence": 0.90,
                }
    
            if dimension == "STANDARDIZATION":
                return {
                    "recommendation_title": (
                        f"Standardize {field}"
                    ),
                    "suggested_action":
                        "STANDARDIZE_VALUES",
                    "suggested_rule_type":
                        "STANDARDIZATION",
                    "suggested_sql": (
                        "SELECT\n"
                        "  *,\n"
                        f"  UPPER(TRIM("
                        f"CAST({field} AS STRING)"
                        f")) AS {field}_standardized\n"
                        "FROM source_table;"
                    ),
                    "suggested_regex": None,
                    "suggested_threshold": (
                        self._default_threshold(
                            severity=severity,
                            affected_percent=(
                                affected_percent
                            ),
                        )
                    ),
                    "automation_recommendation":
                        "AUTO_FIX_CANDIDATE",
                    "automation_confidence": 0.95,
                }
    
            return {
                "recommendation_title": (
                    f"Review {rule_id}"
                ),
                "suggested_action":
                    "REVIEW_RECORDS",
                "suggested_rule_type":
                    dimension or "CUSTOM",
                "suggested_sql": None,
                "suggested_regex": None,
                "suggested_threshold": (
                    self._default_threshold(
                        severity=severity,
                        affected_percent=(
                            affected_percent
                        ),
                    )
                ),
                "automation_recommendation":
                    "REVIEW_REQUIRED",
                "automation_confidence": 0.50,
            }
    
        # ---------------------------------------------------------
        # Claude JSON parsing
        # ---------------------------------------------------------
    
    @staticmethod
    def _extract_json_object(
            raw: Any,
        ) -> Dict[str, Any]:
            """
            Parse structured DQ AI recommendation JSON
            from an LLM response.
    
            Tolerates:
            - already-parsed dicts
            - markdown ```json fences
            - explanatory text before/after JSON
            - trailing commas before } or ]
            - multiple candidate JSON objects
    
            Does not invent genuinely truncated JSON.
            """
    
            if isinstance(raw, dict):
                return raw
    
            if raw is None:
                raise ValueError(
                    "Claude returned no DQ recommendation content."
                )
    
            text = str(raw).strip()
    
            if not text:
                raise ValueError(
                    "Claude returned an empty "
                    "DQ recommendation response."
                )
    
            # -----------------------------------------------------
            # 1. Strip markdown fences
            # -----------------------------------------------------
    
            text = re.sub(
                r"^\s*```(?:json)?\s*",
                "",
                text,
                flags=re.IGNORECASE,
            )
    
            text = re.sub(
                r"\s*```\s*$",
                "",
                text,
            )
    
            text = text.strip()
    
            # -----------------------------------------------------
            # 2. Try exact JSON first
            # -----------------------------------------------------
    
            try:
                value = json.loads(text)
    
                if isinstance(value, dict):
                    return value
    
            except json.JSONDecodeError:
                pass
    
            # -----------------------------------------------------
            # 3. Remove trailing commas and retry
            # -----------------------------------------------------
    
            cleaned = re.sub(
                r",\s*([}\]])",
                r"\1",
                text,
            )
    
            try:
                value = json.loads(cleaned)
    
                if isinstance(value, dict):
                    return value
    
            except json.JSONDecodeError:
                pass
    
            # -----------------------------------------------------
            # 4. Repair invalid JSON escapes and retry
            # -----------------------------------------------------
            #
            # LLM responses occasionally contain regex notation such
            # as \s, \S, \d, \(, or \) inside a JSON string without
            # escaping the backslash for JSON.
            #
            # Only repair invalid escapes while inside quoted JSON
            # strings. Preserve valid JSON escapes, including:
            # \", \\, \/, \b, \f, \n, \r, \t, and \uXXXX.
            #
            # This retry occurs only after normal JSON parsing failed,
            # so already-valid responses are never modified.
    
            def repair_invalid_json_escapes(
                json_text: str,
            ) -> str:
                repaired: List[str] = []
                in_string = False
                index = 0
    
                valid_json_escapes = {
                    '"',
                    "\\",
                    "/",
                    "b",
                    "f",
                    "n",
                    "r",
                    "t",
                    "u",
                }
    
                while index < len(json_text):
                    char = json_text[index]
    
                    if char == '"':
                        # A quote is escaped only when preceded by an
                        # odd number of consecutive backslashes.
                        backslash_count = 0
                        previous_index = index - 1
    
                        while (
                            previous_index >= 0
                            and json_text[previous_index] == "\\"
                        ):
                            backslash_count += 1
                            previous_index -= 1
    
                        if backslash_count % 2 == 0:
                            in_string = not in_string
    
                        repaired.append(char)
                        index += 1
                        continue
    
                    if (
                        in_string
                        and char == "\\"
                        and index + 1 < len(json_text)
                    ):
                        next_char = json_text[index + 1]
    
                        if next_char not in valid_json_escapes:
                            # Convert an invalid JSON escape such as
                            # \s into the valid JSON representation \\s.
                            repaired.append("\\")
                            repaired.append("\\")
                            repaired.append(next_char)
                            index += 2
                            continue
    
                    repaired.append(char)
                    index += 1
    
                return "".join(repaired)
    
            repaired_cleaned = repair_invalid_json_escapes(
                cleaned
            )
    
            if repaired_cleaned != cleaned:
                try:
                    value = json.loads(
                        repaired_cleaned
                    )
    
                    if isinstance(value, dict):
                        return value
    
                except json.JSONDecodeError:
                    # Continue through balanced-object extraction so
                    # explanatory text around JSON remains supported.
                    pass
    
            cleaned = repaired_cleaned
    
            # -----------------------------------------------------
            # 4. Extract balanced JSON object candidates
            # -----------------------------------------------------
    
            candidates = []
    
            depth = 0
            start_index = None
            in_string = False
            escape_next = False
    
            for index, char in enumerate(cleaned):
                if escape_next:
                    escape_next = False
                    continue
    
                if char == "\\":
                    if in_string:
                        escape_next = True
                    continue
    
                if char == '"':
                    in_string = not in_string
                    continue
    
                if in_string:
                    continue
    
                if char == "{":
                    if depth == 0:
                        start_index = index
    
                    depth += 1
    
                elif char == "}":
                    if depth > 0:
                        depth -= 1
    
                        if (
                            depth == 0
                            and start_index is not None
                        ):
                            candidates.append(
                                cleaned[
                                    start_index:index + 1
                                ]
                            )
    
                            start_index = None
    
            # -----------------------------------------------------
            # 5. Parse each candidate independently
            # -----------------------------------------------------
    
            for candidate in candidates:
                candidate = re.sub(
                    r",\s*([}\]])",
                    r"\1",
                    candidate,
                )
    
                try:
                    value = json.loads(
                        candidate
                    )
    
                    if isinstance(
                        value,
                        dict,
                    ):
                        return value
    
                except json.JSONDecodeError:
                    continue
    
            # -----------------------------------------------------
            # 6. Final failure with useful context
            # -----------------------------------------------------
    
            try:
                json.loads(cleaned)
    
            except json.JSONDecodeError as exc:
                start = max(
                    0,
                    exc.pos - 300,
                )
                end = min(
                    len(cleaned),
                    exc.pos + 300,
                )
    
                error_context = cleaned[
                    start:end
                ]
    
                raise ValueError(
                    "Unable to parse Claude DQ recommendation JSON. "
                    f"JSON error: {exc.msg}. "
                    f"Line: {exc.lineno}. "
                    f"Column: {exc.colno}. "
                    f"Character: {exc.pos}. "
                    f"Context around failure: {error_context!r}"
                ) from exc
    
            raise ValueError(
                "Unable to parse Claude DQ recommendation JSON."
            )
        # ---------------------------------------------------------
        # Strict AI response validation
        # ---------------------------------------------------------
    
    def _validate_ai_response(
            self,
            *,
            payload: Dict[str, Any],
            organization_id: str,
            profile_run_id: str,
            domain: str,
            deterministic_recommendations: (
                List[Dict[str, Any]]
            ),
        ) -> Dict[str, Any]:
            response_profile_run_id = str(
                payload.get("profile_run_id")
                or ""
            ).strip()
    
            if (
                response_profile_run_id
                != profile_run_id
            ):
                raise ValueError(
                    "Claude returned the wrong "
                    "profile_run_id."
                )
    
            response_domain = str(
                payload.get("domain")
                or ""
            ).strip().upper()
    
            if response_domain != domain:
                raise ValueError(
                    "Claude returned the wrong "
                    "DQ domain."
                )
    
            deterministic_by_id = {
                str(
                    item[
                        "recommendation_id"
                    ]
                ): item
                for item
                in deterministic_recommendations
            }
    
            raw_recommendations = (
                payload.get(
                    "recommendations"
                )
            )
    
            if not isinstance(
                raw_recommendations,
                list,
            ):
                raise ValueError(
                    "Claude recommendations "
                    "must be a list."
                )
    
            validated: List[
                Dict[str, Any]
            ] = []
    
            for raw in raw_recommendations:
                if not isinstance(
                    raw,
                    dict,
                ):
                    continue
    
                recommendation_id = str(
                    raw.get(
                        "recommendation_id"
                    )
                    or ""
                ).strip()
    
                deterministic = (
                    deterministic_by_id.get(
                        recommendation_id
                    )
                )
    
                if not deterministic:
                    raise ValueError(
                        "Claude created an unknown "
                        "recommendation_id."
                    )
    
                rule_id = str(
                    raw.get("rule_id")
                    or ""
                ).strip()
    
                if (
                    rule_id
                    != deterministic[
                        "rule_id"
                    ]
                ):
                    raise ValueError(
                        "Claude changed rule_id."
                    )
    
                field_name = raw.get(
                    "field_name"
                )
    
                if (
                    field_name
                    != deterministic.get(
                        "field_name"
                    )
                ):
                    raise ValueError(
                        "Claude changed field_name."
                    )
    
                dimension = str(
                    raw.get("dimension")
                    or ""
                ).strip().upper()
    
                if (
                    dimension
                    != deterministic[
                        "dimension"
                    ]
                    or dimension
                    not in VALID_DIMENSIONS
                ):
                    raise ValueError(
                        "Claude returned an invalid "
                        "DQ dimension."
                    )
    
                severity = str(
                    raw.get("severity")
                    or ""
                ).strip().upper()
    
                if (
                    severity
                    != deterministic[
                        "severity"
                    ]
                    or severity
                    not in VALID_SEVERITIES
                ):
                    raise ValueError(
                        "Claude returned an invalid "
                        "severity."
                    )
    
                implementation_type = str(
                    raw.get(
                        "implementation_type"
                    )
                    or "MANUAL_REVIEW"
                ).strip().upper()
    
                if (
                    implementation_type
                    not in
                    VALID_IMPLEMENTATION_TYPES
                ):
                    implementation_type = (
                        "MANUAL_REVIEW"
                    )
    
                automation = str(
                    raw.get(
                        "automation_recommendation"
                    )
                    or "REVIEW_REQUIRED"
                ).strip().upper()
    
                if (
                    automation
                    not in
                    VALID_AUTOMATION_RECOMMENDATIONS
                ):
                    automation = (
                        "REVIEW_REQUIRED"
                    )
    
                suggested_sql = (
                    raw.get("suggested_sql")
                )
    
                if suggested_sql:
                    suggested_sql = str(
                        suggested_sql
                    ).strip()
    
                    if (
                        FORBIDDEN_SQL_PATTERN.search(
                            suggested_sql
                        )
                    ):
                        raise ValueError(
                            "Claude returned "
                            "destructive SQL."
                        )
    
                suggested_regex = (
                    raw.get(
                        "suggested_regex"
                    )
                )
    
                if suggested_regex:
                    suggested_regex = str(
                        suggested_regex
                    ).strip()
    
                    try:
                        re.compile(
                            suggested_regex
                        )
                    except re.error as exc:
                        raise ValueError(
                            "Claude returned an "
                            "invalid regex."
                        ) from exc
    
                threshold = (
                    self._bounded_float(
                        raw.get(
                            "suggested_threshold"
                        ),
                        minimum=0.0,
                        maximum=100.0,
                        default=float(
                            deterministic.get(
                                "suggested_threshold"
                            )
                            or 95.0
                        ),
                    )
                )
    
                automation_confidence = (
                    self._bounded_float(
                        raw.get(
                            "automation_confidence"
                        ),
                        minimum=0.0,
                        maximum=1.0,
                        default=float(
                            deterministic.get(
                                "automation_confidence"
                            )
                            or 0.5
                        ),
                    )
                )
    
                validated.append(
                    {
                        "organization_id":
                            organization_id,
                        "profile_run_id":
                            profile_run_id,
                        "recommendation_id":
                            recommendation_id,
                        "domain":
                            domain,
                        "rule_id":
                            rule_id,
                        "field_name":
                            field_name,
                        "source_column":
                            deterministic.get(
                                "source_column"
                            ),
                        "dimension":
                            dimension,
                        "severity":
                            severity,
                        "finding_count":
                            deterministic.get(
                                "finding_count",
                                0,
                            ),
                        "affected_record_count":
                            deterministic.get(
                                "affected_record_count",
                                0,
                            ),
                        "affected_percent":
                            deterministic.get(
                                "affected_percent",
                                0.0,
                            ),
                        "evidence_samples":
                            deterministic.get(
                                "evidence_samples",
                                [],
                            ),
                        "recommendation_title":
                            self._safe_text(
                                raw.get(
                                    "recommendation_title"
                                )
                            ),
                        "business_impact":
                            self._safe_text(
                                raw.get(
                                    "business_impact"
                                )
                            ),
                        "recommended_remediation":
                            self._safe_text(
                                raw.get(
                                    "recommended_remediation"
                                )
                            ),
                        "implementation_type":
                            implementation_type,
                        "suggested_sql":
                            suggested_sql,
                        "suggested_regex":
                            suggested_regex,
                        "suggested_threshold":
                            threshold,
                        "automation_recommendation":
                            automation,
                        "automation_confidence":
                            automation_confidence,
                        "steward_approval_required":
                            bool(
                                raw.get(
                                    "steward_approval_required",
                                    True,
                                )
                            ),
                        "reasoning_summary":
                            self._safe_text(
                                raw.get(
                                    "reasoning_summary"
                                )
                            ),
                        "status": "OPEN",
                    }
                )
                        # -----------------------------------------------------
            # Validate complete recommendation coverage.
            # -----------------------------------------------------
    
            returned_id_list = [
                str(
                    item.get(
                        "recommendation_id"
                    )
                    or ""
                ).strip()
                for item in raw_recommendations
                if isinstance(
                    item,
                    dict,
                )
            ]
    
            if (
                len(returned_id_list)
                != len(set(returned_id_list))
            ):
                raise ValueError(
                    "Claude returned duplicate "
                    "recommendation_id values."
                )
    
            expected_ids = set(
                deterministic_by_id.keys()
            )
    
            returned_ids = set(
                returned_id_list
            )
    
            validated_ids = {
                str(
                    item.get(
                        "recommendation_id"
                    )
                    or ""
                ).strip()
                for item in validated
            }
    
            missing_ids = (
                expected_ids
                - validated_ids
            )
    
            unexpected_ids = (
                returned_ids
                - expected_ids
            )
    
            if (
                validated_ids
                != expected_ids
            ):
                raise ValueError(
                    "Claude recommendation coverage "
                    "does not match deterministic "
                    "finding groups. "
                    f"Expected={len(expected_ids)}, "
                    f"returned={len(returned_ids)}, "
                    f"validated={len(validated_ids)}, "
                    f"missing_ids={sorted(missing_ids)}, "
                    f"unexpected_ids={sorted(unexpected_ids)}"
                )
    
            overall = payload.get(
                "overall_analysis"
            )
    
            if not isinstance(
                overall,
                dict,
            ):
                overall = {}
    
            return {
                "overall_analysis": {
                    "dq_health_summary":
                        self._safe_text(
                            overall.get(
                                "dq_health_summary"
                            )
                        ),
                    "highest_priority_issue":
                        self._safe_text(
                            overall.get(
                                "highest_priority_issue"
                            )
                        ),
                    "recommended_next_action":
                        self._safe_text(
                            overall.get(
                                "recommended_next_action"
                            )
                        ),
                    "confidence":
                        self._bounded_float(
                            overall.get(
                                "confidence"
                            ),
                            minimum=0.0,
                            maximum=1.0,
                            default=0.80,
                        ),
                },
                "recommendations":
                    validated,
            }
    
        # ---------------------------------------------------------
        # Helpers
        # ---------------------------------------------------------
    
    @staticmethod
    def _normalize_column_mappings(
            value: Any,
        ) -> List[Dict[str, Any]]:
            """
            Normalize persisted column mappings into a predictable list.
    
            BigQuery JSON values may arrive as a native list or as a JSON
            string depending on the retrieval/serialization path.
            """
            if isinstance(value, list):
                return [
                    item
                    for item in value
                    if isinstance(item, dict)
                ]
    
            if isinstance(value, str):
                raw = value.strip()
    
                if not raw:
                    return []
    
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    return []
    
                if isinstance(parsed, list):
                    return [
                        item
                        for item in parsed
                        if isinstance(item, dict)
                    ]
    
            return []
    
    @staticmethod
    def _source_column_for_target(
            column_mappings: Any,
            target_field: str | None,
        ) -> str | None:
            """
            Resolve the physical source column for a canonical target field.
    
            The persisted mapping is treated as authoritative metadata.
            Claude is never allowed to invent or override this value.
            """
            normalized_target = str(
                target_field or ""
            ).strip().lower()
    
            if not normalized_target:
                return None
    
            for mapping in column_mappings or []:
                if not isinstance(mapping, dict):
                    continue
    
                mapped_target = str(
                    mapping.get("target_field")
                    or ""
                ).strip().lower()
    
                if mapped_target != normalized_target:
                    continue
    
                source_column = str(
                    mapping.get("source_column")
                    or ""
                ).strip()
    
                return source_column or None
    
            return None
    
    @staticmethod
    def _require_organization_id(
            organization_id: str,
        ) -> str:
            normalized = str(
                organization_id or ""
            ).strip()
    
            if not normalized:
                raise ValueError(
                    "organization_id is required."
                )
    
            if not normalized.startswith(
                "org_"
            ):
                raise ValueError(
                    "organization_id must use "
                    "the org_ identifier standard."
                )
    
            return normalized
    
    @staticmethod
    def _safe_text(
            value: Any,
        ) -> str:
            return str(
                value or ""
            ).strip()
    
    @staticmethod
    def _bounded_float(
            value: Any,
            *,
            minimum: float,
            maximum: float,
            default: float,
        ) -> float:
            try:
                number = float(value)
            except (
                TypeError,
                ValueError,
            ):
                number = default
    
            return round(
                max(
                    minimum,
                    min(
                        maximum,
                        number,
                    ),
                ),
                4,
            )
    
    @staticmethod
    def _default_threshold(
            *,
            severity: str,
            affected_percent: float,
        ) -> float:
            current_pass_rate = (
                100.0
                - max(
                    0.0,
                    min(
                        100.0,
                        affected_percent,
                    ),
                )
            )
    
            if severity in {
                "CRITICAL",
                "HIGH",
            }:
                return round(
                    min(
                        99.0,
                        max(
                            95.0,
                            current_pass_rate
                            + 2.0,
                        ),
                    ),
                    2,
                )
    
            return round(
                min(
                    98.0,
                    max(
                        90.0,
                        current_pass_rate
                        + 1.0,
                    ),
                ),
                2,
            )
    
    @staticmethod
    def _suggest_regex(
            field_name: str,
        ) -> str | None:
            normalized = (
                field_name
                .strip()
                .lower()
            )
    
            if normalized in {
                "email",
                "provider_email",
                "contact_email",
            }:
                return (
                    r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
                )
    
            if normalized == "npi":
                return r"^\d{10}$"
    
            if normalized in {
                "gtin",
                "upc",
            }:
                return r"^\d{8,14}$"
    
            return None
    
    @staticmethod
    def _build_validity_sql(
            *,
            field: str,
            regex_pattern: str | None,
        ) -> str:
            if regex_pattern:
                return (
                    "SELECT *\n"
                    "FROM source_table\n"
                    f"WHERE {field} IS NOT NULL\n"
                    "  AND NOT REGEXP_CONTAINS("
                    f"CAST({field} AS STRING), "
                    f"r'{regex_pattern}');"
                )
    
            return (
                "SELECT *\n"
                "FROM source_table\n"
                f"WHERE {field} IS NOT NULL;"
            )
