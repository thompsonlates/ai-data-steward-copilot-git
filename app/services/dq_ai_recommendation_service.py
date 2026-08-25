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
    ) -> List[Dict[str, Any]]:
        recommendations: List[
            Dict[str, Any]
        ] = []

        for row in finding_rows:
            rule_id = str(
                row.get("rule_id")
                or ""
            ).strip()

            field_name = (
                str(
                    row.get("field_name")
                    or ""
                ).strip()
                or None
            )

            dimension = str(
                row.get("dimension")
                or ""
            ).strip().upper()

            severity = str(
                row.get("severity")
                or "MEDIUM"
            ).strip().upper()

            finding_count = int(
                row.get("finding_count")
                or 0
            )

            affected_record_count = int(
                row.get(
                    "affected_record_count"
                )
                or 0
            )

            affected_percent = (
                round(
                    (
                        affected_record_count
                        / total_records
                    )
                    * 100.0,
                    2,
                )
                if total_records > 0
                else 0.0
            )

            candidate = (
                self._build_candidate_fix(
                    domain=domain,
                    rule_id=rule_id,
                    field_name=field_name,
                    dimension=dimension,
                    severity=severity,
                    affected_percent=(
                        affected_percent
                    ),
                )
            )

            recommendations.append(
                {
                    "organization_id":
                        organization_id,
                    "profile_run_id":
                        profile_run_id,
                    "recommendation_id": (
                        f"dqr_"
                        f"{uuid.uuid4().hex[:20]}"
                    ),
                    "rule_id":
                        rule_id,
                    "field_name":
                        field_name,
                    "dimension":
                        dimension,
                    "severity":
                        severity,
                    "finding_count":
                        finding_count,
                    "affected_record_count":
                        affected_record_count,
                    "affected_percent":
                        affected_percent,
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