from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from google.cloud import bigquery

from app.api.schemas import MatchExplainRequest
from app.services.bq_metrics import BigQueryMetrics
from app.services.quality_profile_config_factory import get_governed_dq_rules


class PolicyIntelligenceEngine:
    CACHE_TTL_SECONDS = 300  # 5 minutes

    def __init__(
        self,
        project_id: Optional[str] = None,
        dataset: Optional[str] = None,
        default_domain: Optional[str] = None,
        default_policy_version: Optional[str] = None,
    ):
        self.project_id = (
            project_id
            or os.getenv("BQ_PROJECT_ID")
            or "api-project-503305938314"
        )
        self.dataset = (
            dataset
            or os.getenv("BQ_DATASET")
            or "ai_data_steward_mvp"
        )

        self.policy_config_table = (
            f"{self.project_id}.{self.dataset}.Policy_Config"
        )
        self.policy_thresholds_table = (
            f"{self.project_id}.{self.dataset}.Policy_Thresholds"
        )
        self.policy_risk_rules_table = (
            f"{self.project_id}.{self.dataset}.Policy_Risk_Rules"
        )

        self.default_domain = (
            default_domain
            or os.getenv("DEFAULT_DOMAIN")
            or "CUSTOMER"
        ).strip().upper()

        self.default_policy_version = (
            default_policy_version
            or os.getenv("DEFAULT_POLICY_VERSION")
            or "v1"
        ).strip()

        self.metrics = BigQueryMetrics()
        self.client = bigquery.Client(project=self.project_id)
        self._cache: dict[str, dict[str, Any]] = {}

        self.policy_dq_rule_map_table = (
            f"{self.project_id}.{self.dataset}."
            "POLICY_DQ_RULE_MAP"
        )

        self.dq_policy_compliance_evidence_table = (
            f"{self.project_id}.{self.dataset}."
            "DQ_POLICY_COMPLIANCE_EVIDENCE"
        )

        self.dq_rule_execution_table = (
            f"{self.project_id}.{self.dataset}."
            "DQ_RULE_EXECUTION"
        )

        self.dq_ai_recommendations_table = (
            f"{self.project_id}.{self.dataset}."
            "DQ_AI_RECOMMENDATIONS"
        )

        self.dq_ai_remediation_versions_table = (
            f"{self.project_id}.{self.dataset}."
            "DQ_AI_REMEDIATION_VERSIONS"
        )

    def _utc_now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _ensure_request_id(self, request_id: Optional[str]) -> str:
        return request_id or f"req_{uuid.uuid4().hex[:16]}"

    def _build_audit_packet_id(self) -> str:
        return f"aud_{uuid.uuid4().hex[:12]}"

    def clear_cache(self) -> None:
        self._cache = {}

    def _cache_get(self, key: str):
        entry = self._cache.get(key)
        if not entry:
            return None

        expires_at = entry["expires_at"]
        if time.time() > expires_at:
            del self._cache[key]
            return None

        return entry["value"]

    def _cache_set(self, key: str, value) -> None:
        self._cache[key] = {
            "value": value,
            "expires_at": time.time() + self.CACHE_TTL_SECONDS,
        }

    def _normalize_value(self, value: Any) -> Any:
        if hasattr(value, "isoformat"):
            return value.isoformat()
        if hasattr(value, "item"):
            return value.item()
        return value

    def _row_to_plain_dict(self, row: bigquery.table.Row) -> dict[str, Any]:
        item = dict(row.items())
        for key, value in list(item.items()):
            item[key] = self._normalize_value(value)
        return item

    def _rows_to_plain_dicts(self, rows) -> list[dict[str, Any]]:
        return [self._row_to_plain_dict(r) for r in rows]

    def _normalize_domain(self, domain: Optional[str]) -> str:
        return (domain or self.default_domain).strip().upper()

    def _normalize_policy_version(self, policy_version: Optional[str]) -> str:
        return (policy_version or self.default_policy_version).strip()

    @staticmethod
    def _require_organization_id(organization_id: str) -> str:
        normalized = str(organization_id or "").strip()
        if not normalized:
            raise ValueError("organization_id is required for tenant-scoped policy operations")
        return normalized

    def _safe_float(
        self,
        value: Any,
        default: float,
        min_value: float = 0.0,
        max_value: float = 1.0,
    ) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError):
            result = default
        return max(min_value, min(result, max_value))

    def _safe_int(
        self,
        value: Any,
        default: int,
        min_value: int = 0,
        max_value: int = 100,
    ) -> int:
        try:
            result = int(value)
        except (TypeError, ValueError):
            result = default
        return max(min_value, min(result, max_value))

    def _default_policy_config(self, domain: str) -> dict[str, Any]:
        return {
            "policy_id": None,
            "domain": domain,
            "policy_version": self.default_policy_version,
            "policy_name": None,
            "policy_description": None,
            "active_flag": 'Y',
            "effective_from": None,
            "effective_to": None,
            "default_decision_mode": "REVIEW",
            "review_required_flag": "Y",
            "allow_auto_merge_flag": "N",
            "organization_id": None,
        }

    def _default_policy_thresholds(
            self,
            organization_id: str,
            domain: str,
            policy_version: str,
        ) -> dict[str, Any]:
        print(f"[WARNING] USING DEFAULT POLICY THRESHOLDS FOR DOMAIN={domain}")
        print( f"[WARNING] USING DEFAULT POLICY THRESHOLDS "f"FOR DOMAIN={domain}, POLICY_VERSION={policy_version}"
)
        return {
            "threshold_id": None,
            "policy_id": None,
            "domain": domain,
            "organization_id": organization_id,
            "policy_version": policy_version,
            "min_review_score": 0.90,
            "min_approve_merge_score": 0.95,
            "min_auto_merge_score": 0.98,
            "max_auto_merge_override_rate": 0.02,
            "max_review_override_rate": 0.10,
            "high_risk_score_cutoff": 0.90,
            "medium_risk_score_cutoff": 0.95,
            "require_exact_dob_flag": "N",
            "require_email_or_address_flag": "N",
            "require_manual_review_on_conflict_flag": "Y",
            "active_flag": 'Y',
            "effective_from": None,
            "effective_to": None,
        }

    def get_policy_dq_rules(
        self,
        *,
        organization_id: str,
        policy_id: str,
        domain: str,
        policy_version: str,
    ) -> list[dict[str, Any]]:
        """
        Return the active DQ rules explicitly governed by
        the supplied policy.

        This is the authoritative Policy -> DQ Rule mapping.
        No inference by domain or rule name is allowed.
        """

        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        effective_policy_id = str(
            policy_id or ""
        ).strip()

        if not effective_policy_id:
            raise ValueError(
                "policy_id is required."
            )

        effective_domain = self._normalize_domain(
            domain
        )

        effective_policy_version = (
            self._normalize_policy_version(
                policy_version
            )
        )

        query = f"""
            SELECT
                policy_dq_rule_map_id,
                organization_id,
                policy_id,
                policy_version,
                domain,
                dq_rule_id,
                compliance_operator,
                compliance_threshold,
                severity,
                required_flag,
                active_flag,
                created_at,
                created_by,
                updated_at,
                updated_by
            FROM `{self.policy_dq_rule_map_table}`
            WHERE organization_id = @organization_id
            AND policy_id = @policy_id
            AND domain = @domain
            AND policy_version = @policy_version
            AND active_flag = TRUE
            ORDER BY
                required_flag DESC,
                severity DESC,
                dq_rule_id
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "policy_id",
                    "STRING",
                    effective_policy_id,
                ),
                bigquery.ScalarQueryParameter(
                    "domain",
                    "STRING",
                    effective_domain,
                ),
                bigquery.ScalarQueryParameter(
                    "policy_version",
                    "STRING",
                    effective_policy_version,
                ),
            ]
        )

        rows = self.client.query(
            query,
            job_config=job_config,
        ).result()

        results = self._rows_to_plain_dicts(
            rows
        )

        # Defense in depth against accidental
        # cross-tenant or cross-policy leakage.
        for row in results:
            returned_org = str(
                row.get("organization_id")
                or ""
            ).strip()

            returned_policy_id = str(
                row.get("policy_id")
                or ""
            ).strip()

            returned_domain = str(
                row.get("domain")
                or ""
            ).strip().upper()

            returned_policy_version = str(
                row.get("policy_version")
                or ""
            ).strip()

            if (
                returned_org
                != effective_organization_id
            ):
                raise RuntimeError(
                    "Policy DQ rule mapping query "
                    "returned another organization."
                )

            if (
                returned_policy_id
                != effective_policy_id
            ):
                raise RuntimeError(
                    "Policy DQ rule mapping query "
                    "returned another policy."
                )

            if (
                returned_domain
                != effective_domain
            ):
                raise RuntimeError(
                    "Policy DQ rule mapping query "
                    "returned another domain."
                )

            if (
                returned_policy_version
                != effective_policy_version
            ):
                raise RuntimeError(
                    "Policy DQ rule mapping query "
                    "returned another policy version."
                )

        return results

    def upsert_policy_dq_rule_mapping(
        self,
        *,
        organization_id: str,
        policy_id: str,
        policy_version: str,
        domain: str,
        dq_rule_id: str,
        compliance_threshold: float,
        compliance_operator: str = ">=",
        severity: str = "HIGH",
        required_flag: bool = True,
        active_flag: bool = True,
        updated_by: str = "system",
    ) -> dict[str, Any]:
        """
        Persist one explicit Governance Policy -> DQ Rule relationship.

        The database mapping remains authoritative. No relationship is
        inferred from rule names or domain alone.
        """
        effective_organization_id = self._require_organization_id(
            organization_id
        )
        effective_policy_id = str(policy_id or "").strip()
        effective_policy_version = self._normalize_policy_version(
            policy_version
        )
        effective_domain = self._normalize_domain(domain)
        effective_rule_id = str(dq_rule_id or "").strip().upper()

        if not effective_policy_id:
            raise ValueError("policy_id is required.")

        if not effective_rule_id:
            raise ValueError("dq_rule_id is required.")

        safe_threshold = max(
            0.0,
            min(1.0, float(compliance_threshold)),
        )

        operator = str(compliance_operator or ">=").strip().upper()
        operator_aliases = {
            "GTE": ">=",
            "GE": ">=",
            ">=": ">=",
            "GT": ">",
            ">": ">",
            "LTE": "<=",
            "LE": "<=",
            "<=": "<=",
            "LT": "<",
            "<": "<",
            "EQ": "=",
            "==": "=",
            "=": "=",
        }
        normalized_operator = operator_aliases.get(operator)

        if normalized_operator is None:
            raise ValueError(
                "Unsupported compliance_operator. "
                "Supported values are >=, >, <=, <, =."
            )

        safe_severity = str(severity or "HIGH").strip().upper()
        if safe_severity not in {
            "LOW",
            "MEDIUM",
            "HIGH",
            "CRITICAL",
        }:
            safe_severity = "HIGH"

        actor = str(updated_by or "system").strip() or "system"
        now_ts = datetime.now(timezone.utc)
        map_id = (
            "poldq_"
            + uuid.uuid5(
                uuid.NAMESPACE_URL,
                "|".join(
                    [
                        effective_organization_id,
                        effective_policy_id,
                        effective_policy_version,
                        effective_domain,
                        effective_rule_id,
                    ]
                ),
            ).hex[:20]
        )

        query = f"""
        MERGE `{self.policy_dq_rule_map_table}` T
        USING (
            SELECT
                @policy_dq_rule_map_id AS policy_dq_rule_map_id,
                @organization_id AS organization_id,
                @policy_id AS policy_id,
                @policy_version AS policy_version,
                @domain AS domain,
                @dq_rule_id AS dq_rule_id,
                @compliance_operator AS compliance_operator,
                @compliance_threshold AS compliance_threshold,
                @severity AS severity,
                @required_flag AS required_flag,
                @active_flag AS active_flag,
                @created_at AS created_at,
                @created_by AS created_by,
                @updated_at AS updated_at,
                @updated_by AS updated_by
        ) S
        ON T.organization_id = S.organization_id
        AND T.policy_id = S.policy_id
        AND T.policy_version = S.policy_version
        AND T.domain = S.domain
        AND T.dq_rule_id = S.dq_rule_id

        WHEN MATCHED THEN
          UPDATE SET
            compliance_operator = S.compliance_operator,
            compliance_threshold = S.compliance_threshold,
            severity = S.severity,
            required_flag = S.required_flag,
            active_flag = S.active_flag,
            updated_at = S.updated_at,
            updated_by = S.updated_by

        WHEN NOT MATCHED THEN
          INSERT (
            policy_dq_rule_map_id,
            organization_id,
            policy_id,
            policy_version,
            domain,
            dq_rule_id,
            compliance_operator,
            compliance_threshold,
            severity,
            required_flag,
            active_flag,
            created_at,
            created_by,
            updated_at,
            updated_by
          )
          VALUES (
            S.policy_dq_rule_map_id,
            S.organization_id,
            S.policy_id,
            S.policy_version,
            S.domain,
            S.dq_rule_id,
            S.compliance_operator,
            S.compliance_threshold,
            S.severity,
            S.required_flag,
            S.active_flag,
            S.created_at,
            S.created_by,
            S.updated_at,
            S.updated_by
          )
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "policy_dq_rule_map_id",
                    "STRING",
                    map_id,
                ),
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "policy_id",
                    "STRING",
                    effective_policy_id,
                ),
                bigquery.ScalarQueryParameter(
                    "policy_version",
                    "STRING",
                    effective_policy_version,
                ),
                bigquery.ScalarQueryParameter(
                    "domain",
                    "STRING",
                    effective_domain,
                ),
                bigquery.ScalarQueryParameter(
                    "dq_rule_id",
                    "STRING",
                    effective_rule_id,
                ),
                bigquery.ScalarQueryParameter(
                    "compliance_operator",
                    "STRING",
                    normalized_operator,
                ),
                bigquery.ScalarQueryParameter(
                    "compliance_threshold",
                    "FLOAT64",
                    safe_threshold,
                ),
                bigquery.ScalarQueryParameter(
                    "severity",
                    "STRING",
                    safe_severity,
                ),
                bigquery.ScalarQueryParameter(
                    "required_flag",
                    "BOOL",
                    bool(required_flag),
                ),
                bigquery.ScalarQueryParameter(
                    "active_flag",
                    "BOOL",
                    bool(active_flag),
                ),
                bigquery.ScalarQueryParameter(
                    "created_at",
                    "TIMESTAMP",
                    now_ts,
                ),
                bigquery.ScalarQueryParameter(
                    "created_by",
                    "STRING",
                    actor,
                ),
                bigquery.ScalarQueryParameter(
                    "updated_at",
                    "TIMESTAMP",
                    now_ts,
                ),
                bigquery.ScalarQueryParameter(
                    "updated_by",
                    "STRING",
                    actor,
                ),
            ]
        )

        self.client.query(
            query,
            job_config=job_config,
        ).result()

        return {
            "policy_dq_rule_map_id": map_id,
            "organization_id": effective_organization_id,
            "policy_id": effective_policy_id,
            "policy_version": effective_policy_version,
            "domain": effective_domain,
            "dq_rule_id": effective_rule_id,
            "compliance_operator": normalized_operator,
            "compliance_threshold": safe_threshold,
            "severity": safe_severity,
            "required_flag": bool(required_flag),
            "active_flag": bool(active_flag),
            "updated_by": actor,
            "updated_at": now_ts.isoformat(),
        }

    def get_profile_rule_execution(
        self,
        *,
        organization_id: str,
        profile_run_id: str,
        domain: str,
        dq_rule_id: str,
    ) -> dict[str, Any] | None:
        """
        Load the exact deterministic DQ rule execution evidence produced by
        QualityProfilerService for one profile run.

        This is the Profiling -> Governance evidence boundary.
        """
        effective_organization_id = self._require_organization_id(
            organization_id
        )
        effective_profile_run_id = str(
            profile_run_id or ""
        ).strip()
        effective_domain = self._normalize_domain(domain)
        effective_rule_id = str(
            dq_rule_id or ""
        ).strip().upper()

        if not effective_profile_run_id:
            raise ValueError("profile_run_id is required.")

        if not effective_rule_id:
            raise ValueError("dq_rule_id is required.")

        query = f"""
        SELECT
            created_at,
            organization_id,
            profile_run_id,
            domain,
            source_table,
            rule_id,
            dimension,
            severity,
            evaluated_record_count,
            passed_record_count,
            failed_record_count,
            skipped_record_count,
            execution_status,
            compliance_rate
        FROM `{self.dq_rule_execution_table}`
        WHERE organization_id = @organization_id
          AND profile_run_id = @profile_run_id
          AND domain = @domain
          AND UPPER(rule_id) = @dq_rule_id
        ORDER BY created_at DESC
        LIMIT 1
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "profile_run_id",
                    "STRING",
                    effective_profile_run_id,
                ),
                bigquery.ScalarQueryParameter(
                    "domain",
                    "STRING",
                    effective_domain,
                ),
                bigquery.ScalarQueryParameter(
                    "dq_rule_id",
                    "STRING",
                    effective_rule_id,
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
            return None

        result = self._row_to_plain_dict(rows[0])

        if str(
            result.get("organization_id") or ""
        ).strip() != effective_organization_id:
            raise RuntimeError(
                "DQ rule execution query returned another organization."
            )

        return result

    def get_dq_rule_remediation_state(
        self,
        *,
        organization_id: str,
        profile_run_id: str,
        domain: str,
        authoritative_rule_id: str,
        diagnostic_rule_ids: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """
        Return tenant- and profile-scoped remediation state for one governed
        authoritative DQ control and its supporting diagnostic rules.

        This method does NOT change policy compliance. It only describes
        whether remediation artifacts for the authoritative or diagnostic
        rules have been generated and approved.
        """
        effective_organization_id = self._require_organization_id(
            organization_id
        )
        effective_profile_run_id = str(
            profile_run_id or ""
        ).strip()
        effective_domain = self._normalize_domain(domain)
        effective_authoritative_rule_id = str(
            authoritative_rule_id or ""
        ).strip().upper()

        if not effective_profile_run_id:
            raise ValueError("profile_run_id is required.")

        if not effective_authoritative_rule_id:
            raise ValueError("authoritative_rule_id is required.")

        normalized_diagnostic_rule_ids = sorted({
            str(rule_id or "").strip().upper()
            for rule_id in (diagnostic_rule_ids or [])
            if str(rule_id or "").strip()
        })

        governed_rule_ids = [
            effective_authoritative_rule_id,
            *normalized_diagnostic_rule_ids,
        ]

        query = f"""
        SELECT
            r.organization_id,
            r.profile_run_id,
            UPPER(TRIM(r.domain)) AS domain,
            UPPER(TRIM(r.rule_id)) AS rule_id,
            r.recommendation_id,
            UPPER(TRIM(COALESCE(r.remediation_approval_status, '')))
                AS remediation_approval_status,
            r.remediation_approved_at,
            r.remediation_artifact_type,
            r.remediation_safety_status,
            rv.remediation_version_id,
            rv.version_number
        FROM `{self.dq_ai_recommendations_table}` r
        LEFT JOIN (
            SELECT
                organization_id,
                recommendation_id,
                remediation_version_id,
                version_number
            FROM `{self.dq_ai_remediation_versions_table}`
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY organization_id, recommendation_id
                ORDER BY version_number DESC, created_at DESC
            ) = 1
        ) rv
          ON rv.organization_id = r.organization_id
         AND rv.recommendation_id = r.recommendation_id
        WHERE r.organization_id = @organization_id
          AND r.profile_run_id = @profile_run_id
          AND UPPER(TRIM(r.domain)) = @domain
          AND UPPER(TRIM(r.rule_id)) IN UNNEST(@rule_ids)
        ORDER BY
            CASE
                WHEN UPPER(TRIM(r.rule_id)) = @authoritative_rule_id THEN 0
                ELSE 1
            END,
            r.remediation_approved_at DESC,
            r.created_at DESC
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "profile_run_id",
                    "STRING",
                    effective_profile_run_id,
                ),
                bigquery.ScalarQueryParameter(
                    "domain",
                    "STRING",
                    effective_domain,
                ),
                bigquery.ArrayQueryParameter(
                    "rule_ids",
                    "STRING",
                    governed_rule_ids,
                ),
                bigquery.ScalarQueryParameter(
                    "authoritative_rule_id",
                    "STRING",
                    effective_authoritative_rule_id,
                ),
            ]
        )

        rows = self._rows_to_plain_dicts(
            self.client.query(
                query,
                job_config=job_config,
            ).result()
        )

        for row in rows:
            if str(row.get("organization_id") or "").strip() != (
                effective_organization_id
            ):
                raise RuntimeError(
                    "DQ remediation state query returned another organization."
                )

            if str(row.get("profile_run_id") or "").strip() != (
                effective_profile_run_id
            ):
                raise RuntimeError(
                    "DQ remediation state query returned another profile run."
                )

            if str(row.get("domain") or "").strip().upper() != (
                effective_domain
            ):
                raise RuntimeError(
                    "DQ remediation state query returned another domain."
                )

        by_rule_id: dict[str, dict[str, Any]] = {}
        for row in rows:
            row_rule_id = str(
                row.get("rule_id") or ""
            ).strip().upper()
            if row_rule_id and row_rule_id not in by_rule_id:
                by_rule_id[row_rule_id] = row

        authoritative_row = by_rule_id.get(
            effective_authoritative_rule_id
        )

        approved_diagnostic_rule_ids = [
            rule_id
            for rule_id in normalized_diagnostic_rule_ids
            if str(
                (by_rule_id.get(rule_id) or {}).get(
                    "remediation_approval_status"
                )
                or ""
            ).strip().upper() == "APPROVED"
        ]

        diagnostic_recommendation_rule_ids = [
            rule_id
            for rule_id in normalized_diagnostic_rule_ids
            if rule_id in by_rule_id
        ]

        authoritative_status = str(
            (authoritative_row or {}).get(
                "remediation_approval_status"
            )
            or ""
        ).strip().upper() or None

        if authoritative_status == "APPROVED":
            remediation_status = "AUTHORITATIVE_APPROVED"
        elif (
            normalized_diagnostic_rule_ids
            and len(approved_diagnostic_rule_ids)
            == len(normalized_diagnostic_rule_ids)
        ):
            remediation_status = "ALL_DIAGNOSTICS_APPROVED"
        elif approved_diagnostic_rule_ids:
            remediation_status = "PARTIALLY_APPROVED"
        elif authoritative_row or diagnostic_recommendation_rule_ids:
            remediation_status = "PENDING_REVIEW"
        else:
            remediation_status = "NOT_STARTED"

        return {
            "remediation_status": remediation_status,
            "authoritative_remediation_approval_status": (
                authoritative_status
            ),
            "authoritative_remediation_recommendation_id": (
                (authoritative_row or {}).get("recommendation_id")
            ),
            "authoritative_remediation_version_id": (
                (authoritative_row or {}).get("remediation_version_id")
            ),
            "diagnostic_rule_count": len(
                normalized_diagnostic_rule_ids
            ),
            "diagnostic_recommendation_rule_count": len(
                diagnostic_recommendation_rule_ids
            ),
            "approved_diagnostic_rule_count": len(
                approved_diagnostic_rule_ids
            ),
            "approved_diagnostic_rule_ids": (
                approved_diagnostic_rule_ids
            ),
            "diagnostic_remediation_states": [
                {
                    "dq_rule_id": rule_id,
                    "recommendation_id": (
                        by_rule_id.get(rule_id) or {}
                    ).get("recommendation_id"),
                    "remediation_approval_status": (
                        str(
                            (by_rule_id.get(rule_id) or {}).get(
                                "remediation_approval_status"
                            )
                            or ""
                        ).strip().upper()
                        or None
                    ),
                    "remediation_version_id": (
                        by_rule_id.get(rule_id) or {}
                    ).get("remediation_version_id"),
                }
                for rule_id in normalized_diagnostic_rule_ids
            ],
        }

    def evaluate_profile_against_policy(
        self,
        *,
        organization_id: str,
        profile_run_id: str,
        domain: str,
        policy_id: str | None = None,
        policy_version: str | None = None,
    ) -> dict[str, Any]:
        """
        Bridge Governance Policy to deterministic profiling evidence.

        Flow:
            Policy
              -> explicit POLICY_DQ_RULE_MAP relationship
              -> DQ_RULE_EXECUTION for the requested profile run
              -> deterministic threshold evaluation
              -> DQ_POLICY_COMPLIANCE_EVIDENCE

        The policy denominator is evaluated_record_count, NOT every row in
        the profile. skipped_record_count is excluded from compliance math.
        """
        effective_organization_id = self._require_organization_id(
            organization_id
        )
        effective_profile_run_id = str(
            profile_run_id or ""
        ).strip()
        effective_domain = self._normalize_domain(domain)

        if not effective_profile_run_id:
            raise ValueError("profile_run_id is required.")

        if policy_id:
            effective_policy_id = str(policy_id).strip()
            effective_policy_version = self._normalize_policy_version(
                policy_version
            )
        else:
            active_policy, _ = self._get_active_policy_config(
                effective_domain,
                effective_organization_id,
            )
            effective_policy_id = str(
                active_policy.get("policy_id") or ""
            ).strip()
            effective_policy_version = self._normalize_policy_version(
                policy_version
                or active_policy.get("policy_version")
            )

        if not effective_policy_id:
            raise ValueError(
                "No policy_id could be resolved for DQ policy compliance."
            )

        mappings = self.get_policy_dq_rules(
            organization_id=effective_organization_id,
            policy_id=effective_policy_id,
            domain=effective_domain,
            policy_version=effective_policy_version,
        )

        catalog_rules = get_governed_dq_rules(
            domain=effective_domain,
        )
        catalog_by_rule_id = {
            str(item.get("rule_id") or "").strip().upper(): item
            for item in catalog_rules
        }

        results: list[dict[str, Any]] = []

        for mapping in mappings:
            rule_id = str(
                mapping.get("dq_rule_id") or ""
            ).strip().upper()

            execution = self.get_profile_rule_execution(
                organization_id=effective_organization_id,
                profile_run_id=effective_profile_run_id,
                domain=effective_domain,
                dq_rule_id=rule_id,
            )

            if execution is None:
                results.append(
                    {
                        "organization_id": effective_organization_id,
                        "policy_id": effective_policy_id,
                        "policy_version": effective_policy_version,
                        "domain": effective_domain,
                        "dq_rule_id": rule_id,
                        "profile_run_id": effective_profile_run_id,
                        "compliance_status": "NOT_EVALUATED",
                        "reason": (
                            "No DQ_RULE_EXECUTION evidence exists for this "
                            "rule in the requested profile run."
                        ),
                        "required_flag": bool(
                            mapping.get("required_flag")
                        ),
                        "catalog_authoritative": bool(
                            catalog_by_rule_id.get(rule_id, {}).get(
                                "authoritative"
                            )
                        ),
                    }
                )
                continue

            execution_status = str(
                execution.get("execution_status") or ""
            ).strip().upper()

            evaluated = max(
                0,
                int(
                    execution.get(
                        "evaluated_record_count"
                    )
                    or 0
                ),
            )
            passed = max(
                0,
                int(
                    execution.get(
                        "passed_record_count"
                    )
                    or 0
                ),
            )
            failed = max(
                0,
                int(
                    execution.get(
                        "failed_record_count"
                    )
                    or 0
                ),
            )
            skipped = max(
                0,
                int(
                    execution.get(
                        "skipped_record_count"
                    )
                    or 0
                ),
            )

            if execution_status != "EXECUTED" or evaluated <= 0:
                results.append(
                    {
                        "organization_id": effective_organization_id,
                        "policy_id": effective_policy_id,
                        "policy_version": effective_policy_version,
                        "domain": effective_domain,
                        "dq_rule_id": rule_id,
                        "profile_run_id": effective_profile_run_id,
                        "execution_status": execution_status
                        or "NOT_EVALUATED",
                        "evaluated_record_count": evaluated,
                        "passed_record_count": passed,
                        "failed_record_count": failed,
                        "skipped_record_count": skipped,
                        "measured_compliance_rate": None,
                        "required_compliance_rate": float(
                            mapping.get(
                                "compliance_threshold"
                            )
                            or 0
                        ),
                        "compliance_status": "NOT_EVALUATED",
                        "required_flag": bool(
                            mapping.get("required_flag")
                        ),
                        "catalog_authoritative": bool(
                            catalog_by_rule_id.get(rule_id, {}).get(
                                "authoritative"
                            )
                        ),
                    }
                )
                continue

            evidence = self.evaluate_dq_policy_compliance(
                organization_id=effective_organization_id,
                policy_id=effective_policy_id,
                policy_version=effective_policy_version,
                domain=effective_domain,
                dq_rule_id=rule_id,
                profile_run_id=effective_profile_run_id,
                # IMPORTANT: policy denominator is only records on which the
                # rule actually executed. SKIP is not a failure.
                total_records=evaluated,
                affected_records=failed,
                compliance_operator=str(
                    mapping.get("compliance_operator")
                    or ">="
                ),
                compliance_threshold=float(
                    mapping.get("compliance_threshold")
                    or 0
                ),
            )

            diagnostic_rule_ids = list(
                catalog_by_rule_id.get(
                    rule_id,
                    {},
                ).get(
                    "diagnostic_rule_ids",
                    (),
                )
            )

            remediation_state = (
                self.get_dq_rule_remediation_state(
                    organization_id=effective_organization_id,
                    profile_run_id=effective_profile_run_id,
                    domain=effective_domain,
                    authoritative_rule_id=rule_id,
                    diagnostic_rule_ids=diagnostic_rule_ids,
                )
            )

            evidence.update(
                {
                    "execution_status": execution_status,
                    "evaluated_record_count": evaluated,
                    "passed_record_count": passed,
                    "failed_record_count": failed,
                    "skipped_record_count": skipped,
                    "profile_rule_compliance_rate": (
                        execution.get("compliance_rate")
                    ),
                    "required_flag": bool(
                        mapping.get("required_flag")
                    ),
                    "severity": str(
                        mapping.get("severity")
                        or execution.get("severity")
                        or "MEDIUM"
                    ).upper(),
                    "catalog_authoritative": bool(
                        catalog_by_rule_id.get(rule_id, {}).get(
                            "authoritative"
                        )
                    ),
                    "governed_rule_description": (
                        catalog_by_rule_id.get(
                            rule_id,
                            {},
                        ).get("description")
                    ),
                    "diagnostic_rule_ids": diagnostic_rule_ids,
                    **remediation_state,
                }
            )

            results.append(evidence)

        status_rank = {
            "NOT_MET": 1,
            "AT_RISK": 2,
            "NOT_EVALUATED": 3,
            "MET": 4,
        }

        results.sort(
            key=lambda item: (
                status_rank.get(
                    str(
                        item.get("compliance_status")
                        or ""
                    ).upper(),
                    5,
                ),
                str(
                    item.get("dq_rule_id")
                    or ""
                ),
            )
        )

        required_results = [
            item
            for item in results
            if bool(item.get("required_flag"))
        ]

        if not mappings:
            overall_status = "NOT_EVALUATED"
        elif any(
            str(item.get("compliance_status") or "").upper()
            == "NOT_MET"
            for item in required_results
        ):
            overall_status = "NOT_MET"
        elif any(
            str(item.get("compliance_status") or "").upper()
            == "AT_RISK"
            for item in required_results
        ):
            overall_status = "AT_RISK"
        elif any(
            str(item.get("compliance_status") or "").upper()
            == "NOT_EVALUATED"
            for item in required_results
        ):
            overall_status = "NOT_EVALUATED"
        elif required_results and all(
            str(item.get("compliance_status") or "").upper()
            == "MET"
            for item in required_results
        ):
            overall_status = "MET"
        else:
            overall_status = "NOT_EVALUATED"

        return {
            "organization_id": effective_organization_id,
            "policy_id": effective_policy_id,
            "policy_version": effective_policy_version,
            "domain": effective_domain,
            "profile_run_id": effective_profile_run_id,
            "policy_compliance_status": overall_status,
            "mapped_rule_count": len(mappings),
            "required_rule_count": len(required_results),
            "met_rule_count": sum(
                1
                for item in results
                if item.get("compliance_status") == "MET"
            ),
            "at_risk_rule_count": sum(
                1
                for item in results
                if item.get("compliance_status") == "AT_RISK"
            ),
            "not_met_rule_count": sum(
                1
                for item in results
                if item.get("compliance_status") == "NOT_MET"
            ),
            "not_evaluated_rule_count": sum(
                1
                for item in results
                if item.get("compliance_status")
                == "NOT_EVALUATED"
            ),
            "rule_results": results,
            "evaluated_at": self._utc_now_iso(),
        }

    def evaluate_dq_policy_compliance(
        self,
        *,
        organization_id: str,
        policy_id: str,
        policy_version: str,
        domain: str,
        dq_rule_id: str,
        profile_run_id: str,
        total_records: int,
        affected_records: int,
        compliance_operator: str,
        compliance_threshold: float,
        remediation_recommendation_id: str | None = None,
        remediation_approval_status: str | None = None,
        remediation_version_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Deterministically evaluate one DQ rule against
        one mapped governance policy threshold.

        Policy compliance is based on measured profile
        evidence, never merely on remediation approval.

        IMPORTANT:
        When called from evaluate_profile_against_policy(), total_records is
        the rule's evaluated_record_count and affected_records is the rule's
        failed_record_count. skipped records are excluded from the policy
        denominator.
        """

        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        effective_policy_id = str(
            policy_id or ""
        ).strip()

        effective_policy_version = (
            self._normalize_policy_version(
                policy_version
            )
        )

        effective_domain = self._normalize_domain(
            domain
        )

        effective_rule_id = str(
            dq_rule_id or ""
        ).strip().upper()

        effective_profile_run_id = str(
            profile_run_id or ""
        ).strip()

        if not effective_policy_id:
            raise ValueError(
                "policy_id is required."
            )

        if not effective_rule_id:
            raise ValueError(
                "dq_rule_id is required."
            )

        if not effective_profile_run_id:
            raise ValueError(
                "profile_run_id is required."
            )

        safe_total_records = max(
            0,
            int(total_records or 0),
        )

        safe_affected_records = max(
            0,
            int(affected_records or 0),
        )

        # A rule cannot affect more records than
        # exist in the profile population.
        safe_affected_records = min(
            safe_affected_records,
            safe_total_records,
        )

        safe_threshold = max(
            0.0,
            min(
                1.0,
                float(
                    compliance_threshold or 0
                ),
            ),
        )

        operator = str(
            compliance_operator or ">="
        ).strip().upper()

        operator_aliases = {
            "GTE": ">=",
            "GE": ">=",
            ">=": ">=",
            "GT": ">",
            ">": ">",
            "LTE": "<=",
            "LE": "<=",
            "<=": "<=",
            "LT": "<",
            "<": "<",
            "EQ": "=",
            "==": "=",
            "=": "=",
        }

        normalized_operator = (
            operator_aliases.get(
                operator
            )
        )

        if normalized_operator is None:
            raise ValueError(
                "Unsupported compliance_operator. "
                "Supported values are >=, >, <=, <, =."
            )

        evaluated_at = datetime.now(
            timezone.utc
        )

        if safe_total_records <= 0:
            compliant_records = 0
            measured_compliance_rate = None
            compliance_status = (
                "NOT_EVALUATED"
            )

        else:
            compliant_records = max(
                0,
                safe_total_records
                - safe_affected_records,
            )

            measured_compliance_rate = (
                compliant_records
                / safe_total_records
            )

            if normalized_operator == ">=":
                policy_met = (
                    measured_compliance_rate
                    >= safe_threshold
                )

            elif normalized_operator == ">":
                policy_met = (
                    measured_compliance_rate
                    > safe_threshold
                )

            elif normalized_operator == "<=":
                policy_met = (
                    measured_compliance_rate
                    <= safe_threshold
                )

            elif normalized_operator == "<":
                policy_met = (
                    measured_compliance_rate
                    < safe_threshold
                )

            else:
                # Equality comparisons use a very small
                # floating point tolerance.
                policy_met = (
                    abs(
                        measured_compliance_rate
                        - safe_threshold
                    )
                    <= 1e-9
                )

            if policy_met:
                compliance_status = "MET"

            else:
                # Keep AT_RISK deterministic and narrow:
                # for normal minimum-compliance policies,
                # within 2 percentage points of target
                # is considered AT_RISK.
                #
                # Anything farther away is NOT_MET.
                if (
                    normalized_operator
                    in {">=", ">"}
                    and measured_compliance_rate
                    < safe_threshold
                    and measured_compliance_rate
                    >= max(
                        0.0,
                        safe_threshold - 0.02,
                    )
                ):
                    compliance_status = (
                        "AT_RISK"
                    )

                else:
                    compliance_status = (
                        "NOT_MET"
                    )

        evidence_id = (
            f"dqpol_{uuid.uuid4().hex[:20]}"
        )

        row = {
            "compliance_evidence_id":
                evidence_id,
            "organization_id":
                effective_organization_id,
            "policy_id":
                effective_policy_id,
            "policy_version":
                effective_policy_version,
            "domain":
                effective_domain,
            "dq_rule_id":
                effective_rule_id,
            "profile_run_id":
                effective_profile_run_id,
            "total_records":
                safe_total_records,
            "affected_records":
                safe_affected_records,
            "compliant_records":
                compliant_records,
            "measured_compliance_rate":
                measured_compliance_rate,
            "required_compliance_rate":
                safe_threshold,
            "compliance_status":
                compliance_status,
            "evaluated_at":
                evaluated_at.isoformat(),
            "remediation_recommendation_id": (
                str(
                    remediation_recommendation_id
                    or ""
                ).strip()
                or None
            ),
            "remediation_approval_status": (
                str(
                    remediation_approval_status
                    or ""
                ).strip().upper()
                or None
            ),
            "remediation_version_id": (
                str(
                    remediation_version_id
                    or ""
                ).strip()
                or None
            ),
        }

        errors = (
            self.client.insert_rows_json(
                self.dq_policy_compliance_evidence_table,
                [row],
            )
        )

        if errors:
            raise RuntimeError(
                "Failed to persist DQ policy "
                f"compliance evidence: {errors}"
            )

        return row

    def _format_override_learning_context(self, rows: list[dict[str, Any]]) -> str:
        if not rows:
            return (
                "No significant steward override patterns were found for this domain "
                "and rule set."
            )

        lines = []
        for row in rows:
            triggered_rule = row.get("triggered_rule", "UNKNOWN_RULE")
            override_reason_code = row.get("override_reason_code") or "UNKNOWN_REASON"
            override_count = row.get("override_count", 0)

            lines.append(
                f"- Rule {triggered_rule} was previously overridden {override_count} "
                f"times, most often for reason {override_reason_code}."
            )

        return "\n".join(lines)

    def get_dq_policy_compliance(
        self,
        *,
        organization_id: str,
        policy_id: str | None = None,
        domain: str | None = None,
        policy_version: str | None = None,
        profile_run_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Return latest deterministic DQ policy compliance
        evidence for Governance Intelligence.

        Results remain tenant-scoped and can optionally be
        filtered by policy, domain, policy version, or
        profile run.
        """

        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        effective_policy_id = (
            str(
                policy_id or ""
            ).strip()
            or None
        )

        effective_domain = (
            self._normalize_domain(
                domain
            )
            if domain
            else None
        )

        effective_policy_version = (
            self._normalize_policy_version(
                policy_version
            )
            if policy_version
            else None
        )

        effective_profile_run_id = (
            str(
                profile_run_id or ""
            ).strip()
            or None
        )

        safe_limit = max(
            1,
            min(
                int(limit or 100),
                500,
            ),
        )

        predicates = [
            "organization_id = @organization_id"
        ]

        query_parameters: list[
            bigquery.ScalarQueryParameter
        ] = [
            bigquery.ScalarQueryParameter(
                "organization_id",
                "STRING",
                effective_organization_id,
            ),
            bigquery.ScalarQueryParameter(
                "limit",
                "INT64",
                safe_limit,
            ),
        ]

        if effective_policy_id:
            predicates.append(
                "policy_id = @policy_id"
            )

            query_parameters.append(
                bigquery.ScalarQueryParameter(
                    "policy_id",
                    "STRING",
                    effective_policy_id,
                )
            )

        if effective_domain:
            predicates.append(
                "domain = @domain"
            )

            query_parameters.append(
                bigquery.ScalarQueryParameter(
                    "domain",
                    "STRING",
                    effective_domain,
                )
            )

        if effective_policy_version:
            predicates.append(
                "policy_version = @policy_version"
            )

            query_parameters.append(
                bigquery.ScalarQueryParameter(
                    "policy_version",
                    "STRING",
                    effective_policy_version,
                )
            )

        if effective_profile_run_id:
            predicates.append(
                "profile_run_id = @profile_run_id"
            )

            query_parameters.append(
                bigquery.ScalarQueryParameter(
                    "profile_run_id",
                    "STRING",
                    effective_profile_run_id,
                )
            )

        where_clause = " AND ".join(
            predicates
        )

        query = f"""
            SELECT
                compliance_evidence_id,
                organization_id,
                policy_id,
                policy_version,
                domain,
                dq_rule_id,
                profile_run_id,
                total_records,
                affected_records,
                compliant_records,
                measured_compliance_rate,
                required_compliance_rate,
                compliance_status,
                evaluated_at,
                remediation_recommendation_id,
                remediation_approval_status,
                remediation_version_id
            FROM `{self.dq_policy_compliance_evidence_table}`
            WHERE {where_clause}
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY
                    policy_id,
                    policy_version,
                    domain,
                    dq_rule_id
                ORDER BY
                    evaluated_at DESC
            ) = 1
            ORDER BY
                CASE compliance_status
                    WHEN 'NOT_MET' THEN 1
                    WHEN 'AT_RISK' THEN 2
                    WHEN 'NOT_EVALUATED' THEN 3
                    WHEN 'MET' THEN 4
                    ELSE 5
                END,
                evaluated_at DESC
            LIMIT @limit
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=query_parameters
        )

        rows = self.client.query(
            query,
            job_config=job_config,
        ).result()

        results = self._rows_to_plain_dicts(
            rows
        )

        for row in results:
            returned_org = str(
                row.get("organization_id")
                or ""
            ).strip()

            if (
                returned_org
                != effective_organization_id
            ):
                raise RuntimeError(
                    "DQ policy compliance query "
                    "returned another organization."
                )

        return results

    def _get_active_policy_config(
        self,
        domain: str,
        organization_id: str,
    ) -> tuple[dict[str, Any], bool]:
        effective_domain = self._normalize_domain(domain)
        effective_organization_id = self._require_organization_id(organization_id)
        cache_key = f"policy_config:{effective_organization_id}:{effective_domain}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached, True

        query = f"""
        SELECT
            policy_id,
            domain,
            policy_version,
            policy_name,
            policy_description,
            active_flag,
            effective_from,
            effective_to,
            default_decision_mode,
            review_required_flag,
            allow_auto_merge_flag,
            organization_id
        FROM `{self.policy_config_table}`
        WHERE organization_id = @organization_id
          AND domain = @domain
          AND active_flag = 'Y'
          AND (effective_from IS NULL OR effective_from <= CURRENT_TIMESTAMP())
          AND (effective_to IS NULL OR effective_to >= CURRENT_TIMESTAMP())
        ORDER BY effective_from DESC
        LIMIT 1
        """
        
        job = self.client.query(
            query,
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter(
                        "organization_id",
                        "STRING",
                        effective_organization_id,
                    ),
                    bigquery.ScalarQueryParameter(
                        "domain",
                        "STRING",
                        effective_domain,
                        
                    ),
                ]
            ),
        )

        rows = self._rows_to_plain_dicts(job.result())
        if not rows:
            raise ValueError(
                f"No active policy config found for domain={effective_domain}"
            )

        result = rows[0]

        self._cache_set(cache_key, result)
        return result, False

    def _get_active_policy_thresholds(
        self,
        domain: str,
        policy_version: str,
        organization_id: str,
    ) -> tuple[dict[str, Any], bool]:
        effective_domain = self._normalize_domain(domain)
        effective_policy_version = self._normalize_policy_version(policy_version)
        effective_organization_id = self._require_organization_id(organization_id)

        cache_key = (
            f"policy_thresholds:{effective_organization_id}:"
            f"{effective_domain}:{effective_policy_version}"
        )
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached, True
        
        query = f"""
        SELECT
            threshold_id,
            policy_id,
            organization_id,
            domain,
            policy_version,
            min_review_score,
            min_approve_merge_score,
            min_auto_merge_score,
            max_auto_merge_override_rate,
            max_review_override_rate,
            high_risk_score_cutoff,
            medium_risk_score_cutoff,
            require_exact_dob_flag,
            require_email_or_address_flag,
            require_manual_review_on_conflict_flag,
            active_flag,
            effective_from,
            effective_to,
            organization_id
        FROM `{self.policy_thresholds_table}`
        WHERE organization_id = @organization_id
          AND domain = @domain
          AND policy_version = @policy_version
          AND active_flag = 'Y'
          AND (effective_from IS NULL OR effective_from <= CURRENT_TIMESTAMP())
          AND (effective_to IS NULL OR effective_to >= CURRENT_TIMESTAMP())
        ORDER BY effective_from DESC
        LIMIT 1
        """
        
        job = self.client.query(
            query,
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter(
                        "organization_id",
                        "STRING",
                        effective_organization_id,
                    ),
                    bigquery.ScalarQueryParameter(
                        "domain",
                        "STRING",
                        effective_domain,
                    ),
                    bigquery.ScalarQueryParameter(
                        "policy_version",
                        "STRING",
                        effective_policy_version,
                    ),
                ]
            ),
        )

        rows = self._rows_to_plain_dicts(job.result())

        if not rows:
            raise ValueError(
                f"No active policy thresholds found for "
                f"domain={effective_domain}, "
                f"policy_version={effective_policy_version}"
            )

        result = rows[0]
        

        self._cache_set(cache_key, result)

        return result, False

    def _get_active_policy_risk_rules(
        self,
        domain: str,
        policy_version: str,
        triggered_rules: list[str],
        organization_id: str,
    ) -> tuple[list[dict[str, Any]], bool]:
        if not triggered_rules:
            return [], False

        effective_domain = self._normalize_domain(domain)
        effective_policy_version = self._normalize_policy_version(policy_version)
        effective_organization_id = self._require_organization_id(organization_id)
        rules_key = "|".join(sorted(triggered_rules))
        cache_key = (
            f"policy_risk_rules:{effective_organization_id}:"
            f"{effective_domain}:{effective_policy_version}:{rules_key}"
        )
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached, True

        query = f"""
        SELECT
            risk_rule_id,
            policy_id,
            domain,
            policy_version,
            triggered_rule,
            override_reason_code,
            risk_weight,
            risk_level,
            recommended_action,
            steward_learning_enabled_flag,
            active_flag,
            effective_from,
            effective_to,
            notes,
            organization_id
        FROM `{self.policy_risk_rules_table}`
        WHERE organization_id = @organization_id
          AND domain = @domain
          AND policy_version = @policy_version
          AND active_flag = 'Y'
          AND triggered_rule IN UNNEST(@triggered_rules)
          AND (effective_from IS NULL OR effective_from <= CURRENT_TIMESTAMP())
          AND (effective_to IS NULL OR effective_to >= CURRENT_TIMESTAMP())
        ORDER BY risk_weight DESC, triggered_rule
        """

        job = self.client.query(
            query,
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter(
                        "organization_id",
                        "STRING",
                        effective_organization_id,
                    ),
                    bigquery.ScalarQueryParameter(
                        "domain",
                        "STRING",
                        effective_domain,
                    ),
                    bigquery.ScalarQueryParameter(
                        "policy_version",
                        "STRING",
                        effective_policy_version,
                    ),
                    bigquery.ArrayQueryParameter(
                        "triggered_rules",
                        "STRING",
                        triggered_rules,
                    ),
                ]
            ),
        )

        result = self._rows_to_plain_dicts(job.result())
        self._cache_set(cache_key, result)
        return result, False
    
    def _build_threshold_guidance(
        self,
        match_score: float,
        thresholds: dict[str, Any],
    ) -> str:
        
        safe_match_score = float(match_score or 0)
        
        min_review = self._safe_float(
            thresholds.get("min_review_score"),
            0.90,
        )
        min_approve = self._safe_float(
            thresholds.get("min_approve_merge_score"),
            0.95,
        )
        min_auto = self._safe_float(
            thresholds.get("min_auto_merge_score"),
            0.98,
        )

        require_exact_dob = str(
            thresholds.get("require_exact_dob_flag", "N")
        ).upper()
        require_email_or_address = str(
            thresholds.get("require_email_or_address_flag", "N")
        ).upper()
        require_manual_on_conflict = str(
            thresholds.get("require_manual_review_on_conflict_flag", "Y")
        ).upper()

        parts = [
            f"Minimum review score: {min_review:.2f}.",
            f"Minimum approve-merge score: {min_approve:.2f}.",
            f"Minimum auto-merge score: {min_auto:.2f}.",
        ]

        if safe_match_score >= min_auto:        
            parts.append("Current score is in the highest threshold range.")
        elif safe_match_score >= min_approve:
            parts.append("Current score is in the strong approve-merge range.")
        elif safe_match_score >= min_review:
            parts.append("Current score is in the review threshold range.")
        else:
            parts.append("Current score is below preferred review threshold.")

        if require_exact_dob == "Y":
            parts.append("Exact DOB is policy-significant when available.")
        if require_email_or_address == "Y":
            parts.append(
                "Email or address support is preferred for stronger approval decisions."
            )
        if require_manual_on_conflict == "Y":
            parts.append("Conflicting evidence should bias toward manual review.")

        return " ".join(parts)

    def _build_risk_rule_guidance(self, risk_rules: list[dict[str, Any]]) -> str:
        if not risk_rules:
            return (
                "No active policy risk rules matched the triggered rules for this case."
            )

        lines = []
        for rule in risk_rules[:5]:
            triggered_rule = rule.get("triggered_rule", "UNKNOWN_RULE")
            risk_level = rule.get("risk_level", "UNKNOWN")
            recommended_action = rule.get("recommended_action", "REVIEW")
            override_reason_code = rule.get("override_reason_code") or "GENERAL_RISK"
            notes = rule.get("notes") or "No additional notes."

            lines.append(
                f"- Policy risk rule for {triggered_rule}: risk_level={risk_level}, "
                f"recommended_action={recommended_action}, "
                f"override_reason_code={override_reason_code}. Notes: {notes}"
            )

        return "\n".join(lines)

    def _determine_risk_band(
        self,
        match_score: float,
        thresholds: dict[str, Any],
    ) -> str:
        high_cutoff = self._safe_float(
            thresholds.get("high_risk_score_cutoff"),
            0.90,
        )
        medium_cutoff = self._safe_float(
            thresholds.get("medium_risk_score_cutoff"),
            0.95,
        )

        if match_score < high_cutoff:
            return "HIGH"
        if match_score < medium_cutoff:
            return "MEDIUM"
        return "LOW"

    def _get_highest_risk_level(self, risk_rules: list[dict[str, Any]]) -> str:
        risk_rank = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
        highest_risk_level = "LOW"

        for rule in risk_rules:
            level = str(rule.get("risk_level", "LOW")).upper()
            if risk_rank.get(level, 1) > risk_rank.get(highest_risk_level, 1):
                highest_risk_level = level

        return highest_risk_level

    def _get_override_rate_from_learning_rows(
        self,
        learning_rows: list[dict[str, Any]],
    ) -> float | None:
        total_overrides = 0

        for row in learning_rows:
            try:
                total_overrides += int(row.get("override_count", 0) or 0)
            except (TypeError, ValueError):
                continue

        if total_overrides <= 0:
            return 0.0
        if total_overrides >= 10:
            return 0.30
        if total_overrides >= 5:
            return 0.15
        if total_overrides >= 2:
            return 0.08
        return 0.03

    def compute_composite_risk(
        self,
        match_score: float,
        highest_risk_level: str,
        source_a: str | None,
        source_b: str | None,
        override_rate: float | None,
    ) -> int:
        risk_map = {
            "LOW": 10,
            "MEDIUM": 30,
            "HIGH": 50,
            "CRITICAL": 70,
        }

        policy_risk = risk_map.get(str(highest_risk_level).upper(), 30)
        match_risk = (1 - float(match_score)) * 30

        trust_map = {
            "MDM": 0,
            "ERP": 5,
            "CRM": 10,
            "SUPPLIER_PORTAL": 15,
            "PLM": 8,
            "PIM": 8,
            "LEGACY": 20,
        }

        trust_a = trust_map.get((source_a or "").upper(), 20)
        trust_b = trust_map.get((source_b or "").upper(), 20)
        source_risk = abs(trust_a - trust_b)

        override_risk = 0
        if override_rate is not None:
            if override_rate > 0.30:
                override_risk = 30
            elif override_rate > 0.15:
                override_risk = 20
            elif override_rate > 0.05:
                override_risk = 10

        score = policy_risk + match_risk + source_risk + override_risk
        return int(max(0, min(score, 100)))

    def _composite_risk_band(self, score: int) -> str:
        if score >= 76:
            return "SEVERE"
        if score >= 51:
            return "ELEVATED"
        if score >= 26:
            return "MODERATE"
        return "LOW"

    def _evaluate_policy_decision(
        self,
        req: MatchExplainRequest,
        policy_cfg: dict[str, Any],
        thresholds: dict[str, Any],
        risk_rules: list[dict[str, Any]],
        learning_rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        
        match_score = self._safe_float(req.match_score, 0.0)
        safe_match_score = match_score

        min_review = self._safe_float(
            thresholds.get("min_review_score"),
            0.90,
        )
        min_approve = self._safe_float(
            thresholds.get("min_approve_merge_score"),
            0.95,
        )
        min_auto = self._safe_float(
            thresholds.get("min_auto_merge_score"),
            0.98,
        )

        allow_auto_merge = (
            str(policy_cfg.get("allow_auto_merge_flag", "N")).upper() == "Y"
        )
        review_required_flag = (
            str(policy_cfg.get("review_required_flag", "Y")).upper() == "Y"
        )
        require_manual_on_conflict = (
            str(
                thresholds.get(
                    "require_manual_review_on_conflict_flag",
                    "Y",
                )
            ).upper()
            == "Y"
        )

        risk_band = self._determine_risk_band(match_score, thresholds)
        highest_risk_level = self._get_highest_risk_level(risk_rules)

        recommended_actions = set()
        steward_learning_override_count = 0

        for rule in risk_rules:
            action = str(rule.get("recommended_action", "REVIEW")).upper()
            recommended_actions.add(action)

        for row in learning_rows:
            try:
                steward_learning_override_count += int(
                    row.get("override_count", 0) or 0
                )
            except (TypeError, ValueError):
                continue

        override_rate = self._get_override_rate_from_learning_rows(learning_rows)
        composite_risk_score = self.compute_composite_risk(
            match_score=match_score,
            highest_risk_level=highest_risk_level,
            source_a=getattr(req.record_a, "source_system", None),
            source_b=getattr(req.record_b, "source_system", None),
            override_rate=override_rate,
        )
        composite_risk_band = self._composite_risk_band(composite_risk_score)

        blockers: list[str] = []
        rationale: list[str] = []
        primary_risk_driver = None

        if risk_rules:
            primary_risk_driver = risk_rules[0].get("triggered_rule")

        if match_score < min_review:
            blockers.append("MATCH_SCORE_BELOW_REVIEW_THRESHOLD")
            rationale.append(
                f"Match score {match_score:.2f} is below minimum review threshold "
                f"{min_review:.2f}."
            )

        if "BLOCK" in recommended_actions or "BLOCK_MERGE" in recommended_actions:
            blockers.append("POLICY_RISK_RULE_BLOCK")
            rationale.append(
                "At least one active policy risk rule recommends blocking the merge."
            )

        if highest_risk_level in {"HIGH", "CRITICAL"}:
            rationale.append(
                f"Highest matched policy risk level is {highest_risk_level}."
            )

        if primary_risk_driver:
            rationale.append(f"Primary risk driver is {primary_risk_driver}.")

        if steward_learning_override_count > 0:
            rationale.append(
                f"Steward learning context found {steward_learning_override_count} "
                f"prior overrides across similar triggered rules."
            )

        rationale.append(
            f"Composite risk score is {composite_risk_score}/100 "
            f"({composite_risk_band})."
        )

        common_payload = {
            "risk_band": risk_band,
            "highest_risk_level": highest_risk_level,
            "recommended_actions_seen": sorted(recommended_actions),
            "steward_learning_override_count": steward_learning_override_count,
            "override_rate_estimate": override_rate,
            "composite_risk_score": composite_risk_score,
            "composite_risk_band": composite_risk_band,
            "primary_risk_driver": primary_risk_driver,
            "decision_factors": {
                "match_score": match_score,
                "min_review_score": min_review,
                "min_approve_merge_score": min_approve,
                "min_auto_merge_score": min_auto,
                "allow_auto_merge": allow_auto_merge,
                "review_required_flag": review_required_flag,
                "require_manual_review_on_conflict": require_manual_on_conflict,
                "blockers": blockers,
                "source_system_a": getattr(req.record_a, "source_system", None),
                "source_system_b": getattr(req.record_b, "source_system", None),
            },
        }

        if blockers:
            return {
                "recommendation": "BLOCK_MERGE",
                "recommendation_reason": " ; ".join(rationale),
                **common_payload,
            }

        if require_manual_on_conflict and (
            "REVIEW" in recommended_actions
            or "REVIEW_REQUIRED" in recommended_actions
            or highest_risk_level in {"HIGH", "CRITICAL"}
            or composite_risk_score >= 51
        ):
            return {
                "recommendation": "REVIEW_REQUIRED",
                "recommendation_reason": " ; ".join(
                    rationale
                    + [
                        "Policy conflict handling requires manual review for this "
                        "risk posture."
                    ]
                ),
                **common_payload,
            }

        if (
            safe_match_score >= min_auto
            and allow_auto_merge
            and highest_risk_level == "LOW"
            and composite_risk_score <= 25
        ):
            return {
                "recommendation": "AUTO_MERGE",
                "recommendation_reason": (
                    f"Match score {safe_match_score:.2f} meets auto-merge threshold "
                    f"{min_auto:.2f}, auto-merge is allowed by policy, highest "
                    f"matched risk level is {highest_risk_level}, and composite risk "
                    f"score is {composite_risk_score}/100."
                ),
                **common_payload,
            }

        if (
            safe_match_score >= min_approve
            and highest_risk_level in {"LOW", "MEDIUM"}
            and composite_risk_score <= 50
        ):
            if review_required_flag:
                return {
                    "recommendation": "REVIEW_REQUIRED",
                    "recommendation_reason": (
                        f"Match score {safe_match_score:.2f} meets approve-merge threshold "
                        f"{min_approve:.2f}, but active policy requires review before "
                        "merge approval."
                    ),
                    **common_payload,
                }

            return {
                "recommendation": "APPROVE_MERGE",
                "recommendation_reason": (
                    f"Match score {match_score:.2f} meets approve-merge threshold "
                    f"{min_approve:.2f}, highest matched risk level is "
                    f"{highest_risk_level}, and composite risk score is "
                    f"{composite_risk_score}/100."
                ),
                **common_payload,
            }

        return {
            "recommendation": "REVIEW_REQUIRED",
            "recommendation_reason": (
                f"Match score {match_score:.2f} meets review threshold "
                f"{min_review:.2f} but does not qualify for a stronger automated "
                f"decision. Composite risk score is {composite_risk_score}/100."
            ),
            **common_payload,
        }

    def get_policy_config_bundle(
        self,
        organization_id: str,
        domain: Optional[str] = None,
        policy_version: Optional[str] = None,
    ) -> dict[str, Any]:
        effective_domain = self._normalize_domain(domain)
        effective_organization_id = self._require_organization_id(organization_id)

        if policy_version:
            effective_policy_version = (
                self._normalize_policy_version(
                    policy_version
                )
            )
        else:
            active_cfg, _ = (
                self._get_active_policy_config(
                    effective_domain,
                    effective_organization_id,
                )
            )

            effective_policy_version = (
                self._normalize_policy_version(
                    active_cfg.get("policy_version")
        )
    )


        config_query = f"""
        SELECT
          policy_id,
          domain,
          policy_version,
          policy_name,
          policy_description,
          active_flag,
          effective_from,
          effective_to,
          default_decision_mode,
          review_required_flag,
          allow_auto_merge_flag,
          organization_id
        FROM `{self.policy_config_table}`
        WHERE organization_id = @organization_id
          AND domain = @domain
          AND policy_version = @policy_version
        ORDER BY effective_from DESC, policy_id DESC
        LIMIT 1
        """

        thresholds_query = f"""
        SELECT
          threshold_id,
          policy_id,
          domain,
          policy_version,
          min_review_score,
          min_approve_merge_score,
          min_auto_merge_score,
          max_auto_merge_override_rate,
          max_review_override_rate,
          high_risk_score_cutoff,
          medium_risk_score_cutoff,
          require_exact_dob_flag,
          require_email_or_address_flag,
          require_manual_review_on_conflict_flag,
          active_flag,
          effective_from,
          effective_to,
          organization_id
        FROM `{self.policy_thresholds_table}`
        WHERE organization_id = @organization_id
          AND domain = @domain
          AND policy_version = @policy_version
        ORDER BY effective_from DESC, threshold_id DESC
        LIMIT 1
        """

        risk_rules_query = f"""
        SELECT
          risk_rule_id,
          policy_id,
          domain,
          policy_version,
          triggered_rule,
          override_reason_code,
          risk_weight,
          risk_level,
          recommended_action,
          steward_learning_enabled_flag,
          active_flag,
          effective_from,
          effective_to,
          notes,
          organization_id
        FROM `{self.policy_risk_rules_table}`
        WHERE organization_id = @organization_id
          AND domain = @domain
          AND policy_version = @policy_version
        ORDER BY risk_weight DESC, triggered_rule
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "domain",
                    "STRING",
                    effective_domain,
                ),
                bigquery.ScalarQueryParameter(
                    "policy_version",
                    "STRING",
                    effective_policy_version,
                ),
            ]
        )

        cfg_rows = list(self.client.query(config_query, job_config=job_config).result())
        threshold_rows = list(
            self.client.query(thresholds_query, job_config=job_config).result()
        )
        risk_rule_rows = list(
            self.client.query(risk_rules_query, job_config=job_config).result()
        )

        config_row = (
            self._row_to_plain_dict(cfg_rows[0])
            if cfg_rows
            else self._default_policy_config(effective_domain)
        )
        thresholds_row = (
            self._row_to_plain_dict(threshold_rows[0])
            if threshold_rows
            else self._default_policy_thresholds(
                organization_id,
                effective_domain,
                effective_policy_version,
            )
        )
        risk_rules = [self._row_to_plain_dict(r) for r in risk_rule_rows]

        return {
            "organization_id": effective_organization_id,
            "domain": effective_domain,
            "policy_version": effective_policy_version,
            "config": config_row,
            "thresholds": thresholds_row,
            "risk_rules": risk_rules,
            "generated_at": self._utc_now_iso(),
        }

    def save_policy_draft(
        self,
        payload: dict[str, Any],
        organization_id: str,
    ) -> dict[str, Any]:
        effective_organization_id = self._require_organization_id(organization_id)
        domain = self._normalize_domain(payload.get("domain"))
        policy_version = self._normalize_policy_version(payload.get("policy_version"))
        created_by = str(
        payload.get("created_by")
        or payload.get("updated_by")
        or "system"
        ).strip()

        updated_by = str(
        payload.get("updated_by")
        or created_by
        ).strip()
        now_ts = datetime.now(timezone.utc)

        requested_active_flag = str(
            payload.get("active_flag") or "N"
        ).strip().upper()

        active_flag = (
            "Y"
            if requested_active_flag == "Y"
            else "N"
        )
    

        current_bundle = self.get_policy_config_bundle(
            organization_id=effective_organization_id,
            domain=domain,
            policy_version=policy_version,
        )
        current_config = current_bundle.get("config") or self._default_policy_config(domain)
        current_thresholds = (current_bundle.get("thresholds")
            or self._default_policy_thresholds(
                effective_organization_id,
                domain,
                policy_version,
    )
)

        config_row = {
            "policy_id": str(
                payload.get("policy_id")
                or current_config.get("policy_id")
                or f"pol_{uuid.uuid4().hex[:12]}"
            ),
            "domain": domain,
            "organization_id": effective_organization_id,
            "created_by": created_by,
            "created_at": now_ts,
            "updated_by": updated_by,
            "updated_at": now_ts,
            "policy_version": policy_version,
            "policy_name": payload.get("policy_name", current_config.get("policy_name")),
            "policy_description": payload.get(
                "policy_description",
                current_config.get("policy_description"),
            ),
           "active_flag": active_flag,

           "effective_from": payload.get(
            "effective_from",
            current_config.get("effective_from"),
        ),
            "effective_to": payload.get(
                "effective_to",
                current_config.get("effective_to"),
            ),
            "default_decision_mode": str(
                payload.get(
                    "default_decision_mode",
                    current_config.get("default_decision_mode", "REVIEW"),
                )
            ),
            "review_required_flag": str(
                payload.get(
                    "review_required_flag",
                    current_config.get("review_required_flag", "Y"),
                )
            ),
            "allow_auto_merge_flag": str(
                payload.get(
                    "allow_auto_merge_flag",
                    current_config.get("allow_auto_merge_flag", "N"),
                )
            ),
        }

        threshold_row = {
            "threshold_id": str(
                payload.get("threshold_id")
                or current_thresholds.get("threshold_id")
                or f"thr_{uuid.uuid4().hex[:12]}"
            ),
            "policy_id": config_row["policy_id"],
            "organization_id": effective_organization_id,
            "domain": domain,
            "policy_version": policy_version,
            "min_review_score": self._safe_float(
                payload.get(
                    "min_review_score",
                    current_thresholds.get("min_review_score"),
                ),
                0.90,
            ),
            "min_approve_merge_score": self._safe_float(
                payload.get(
                    "min_approve_merge_score",
                    current_thresholds.get("min_approve_merge_score"),
                ),
                0.95,
            ),
            "min_auto_merge_score": self._safe_float(
                payload.get(
                    "min_auto_merge_score",
                    current_thresholds.get("min_auto_merge_score"),
                ),
                0.98,
            ),
            "max_auto_merge_override_rate": self._safe_float(
                payload.get(
                    "max_auto_merge_override_rate",
                    current_thresholds.get("max_auto_merge_override_rate"),
                ),
                0.02,
            ),
            "max_review_override_rate": self._safe_float(
                payload.get(
                    "max_review_override_rate",
                    current_thresholds.get("max_review_override_rate"),
                ),
                0.10,
            ),
            "high_risk_score_cutoff": self._safe_float(
                payload.get(
                    "high_risk_score_cutoff",
                    current_thresholds.get("high_risk_score_cutoff"),
                ),
                0.90,
            ),
            "medium_risk_score_cutoff": self._safe_float(
                payload.get(
                    "medium_risk_score_cutoff",
                    current_thresholds.get("medium_risk_score_cutoff"),
                ),
                0.95,
            ),
            "require_exact_dob_flag": str(
                payload.get(
                    "require_exact_dob_flag",
                    current_thresholds.get("require_exact_dob_flag", "N"),
                )
            ),
            "require_email_or_address_flag": str(
                payload.get(
                    "require_email_or_address_flag",
                    current_thresholds.get(
                        "require_email_or_address_flag",
                        "N",
                    ),
                )
            ),
            "require_manual_review_on_conflict_flag": str(
                payload.get(
                    "require_manual_review_on_conflict_flag",
                    current_thresholds.get(
                        "require_manual_review_on_conflict_flag",
                        "Y",
                    ),
                )
            ),
          "active_flag": active_flag,

          "effective_from": payload.get(
            "effective_from",
            current_thresholds.get("effective_from"),
        ),
            "effective_to": payload.get(
                "effective_to",
                current_thresholds.get("effective_to"),
            ),
        }

        merge_config_sql = f"""
    MERGE `{self.policy_config_table}` T
    USING (
        SELECT
            @policy_id AS policy_id,
            @organization_id AS organization_id,
            @domain AS domain,
            @policy_version AS policy_version,
            @policy_name AS policy_name,
            @policy_description AS policy_description,
            @active_flag AS active_flag,
            @effective_from AS effective_from,
            @effective_to AS effective_to,
            @default_decision_mode AS default_decision_mode,
            @review_required_flag AS review_required_flag,
            @allow_auto_merge_flag AS allow_auto_merge_flag,
            @created_by AS created_by,
            @created_at AS created_at,
            @updated_by AS updated_by,
            @updated_at AS updated_at
    ) S

    ON T.organization_id = S.organization_id
    AND T.domain = S.domain
    AND T.policy_version = S.policy_version

    WHEN MATCHED THEN
        UPDATE SET
            policy_name = S.policy_name,
            policy_description = S.policy_description,
            active_flag = S.active_flag,
            effective_from = S.effective_from,
            effective_to = S.effective_to,
            default_decision_mode = S.default_decision_mode,
            review_required_flag = S.review_required_flag,
            allow_auto_merge_flag = S.allow_auto_merge_flag,
            updated_by = S.updated_by,
            updated_at = S.updated_at

    WHEN NOT MATCHED THEN
        INSERT (
            policy_id,
            organization_id,
            domain,
            policy_version,
            policy_name,
            policy_description,
            active_flag,
            effective_from,
            effective_to,
            default_decision_mode,
            review_required_flag,
            allow_auto_merge_flag,
            created_by,
            created_at,
            updated_by,
            updated_at
        )
        VALUES (
            S.policy_id,
            S.organization_id,
            S.domain,
            S.policy_version,
            S.policy_name,
            S.policy_description,
            S.active_flag,
            S.effective_from,
            S.effective_to,
            S.default_decision_mode,
            S.review_required_flag,
            S.allow_auto_merge_flag,
            S.created_by,
            S.created_at,
            S.updated_by,
            S.updated_at
        )
    """

        merge_thresholds_sql = f"""
        MERGE `{self.policy_thresholds_table}` T
        USING (
          SELECT
            @threshold_id AS threshold_id,
            @policy_id AS policy_id,
            @organization_id AS organization_id,
            @domain AS domain,
            @policy_version AS policy_version,
            @min_review_score AS min_review_score,
            @min_approve_merge_score AS min_approve_merge_score,
            @min_auto_merge_score AS min_auto_merge_score,
            @max_auto_merge_override_rate AS max_auto_merge_override_rate,
            @max_review_override_rate AS max_review_override_rate,
            @high_risk_score_cutoff AS high_risk_score_cutoff,
            @medium_risk_score_cutoff AS medium_risk_score_cutoff,
            @require_exact_dob_flag AS require_exact_dob_flag,
            @require_email_or_address_flag AS require_email_or_address_flag,
            @require_manual_review_on_conflict_flag AS require_manual_review_on_conflict_flag,
            @active_flag AS active_flag,
            @effective_from AS effective_from,
            @effective_to AS effective_to
        ) S
        ON T.organization_id = S.organization_id
           AND T.domain = S.domain
           AND T.policy_version = S.policy_version
        WHEN MATCHED THEN
          UPDATE SET
            min_review_score = S.min_review_score,
            min_approve_merge_score = S.min_approve_merge_score,
            min_auto_merge_score = S.min_auto_merge_score,
            max_auto_merge_override_rate = S.max_auto_merge_override_rate,
            max_review_override_rate = S.max_review_override_rate,
            high_risk_score_cutoff = S.high_risk_score_cutoff,
            medium_risk_score_cutoff = S.medium_risk_score_cutoff,
            require_exact_dob_flag = S.require_exact_dob_flag,
            require_email_or_address_flag = S.require_email_or_address_flag,
            require_manual_review_on_conflict_flag = S.require_manual_review_on_conflict_flag,
            active_flag = S.active_flag,
            effective_from = S.effective_from,
            effective_to = S.effective_to
        WHEN NOT MATCHED THEN
          INSERT (
            threshold_id,
            policy_id,
            organization_id,
            domain,
            policy_version,
            min_review_score,
            min_approve_merge_score,
            min_auto_merge_score,
            max_auto_merge_override_rate,
            max_review_override_rate,
            high_risk_score_cutoff,
            medium_risk_score_cutoff,
            require_exact_dob_flag,
            require_email_or_address_flag,
            require_manual_review_on_conflict_flag,
            active_flag,
            effective_from,
            effective_to
          )
          VALUES (
            S.threshold_id,
            S.policy_id,
            S.organization_id,
            S.domain,
            S.policy_version,
            S.min_review_score,
            S.min_approve_merge_score,
            S.min_auto_merge_score,
            S.max_auto_merge_override_rate,
            S.max_review_override_rate,
            S.high_risk_score_cutoff,
            S.medium_risk_score_cutoff,
            S.require_exact_dob_flag,
            S.require_email_or_address_flag,
            S.require_manual_review_on_conflict_flag,
            S.active_flag,
            S.effective_from,
            S.effective_to
          )
        """
        print(
            "POLICY THRESHOLDS MERGE TARGET:",
            self.policy_thresholds_table,
            flush=True,
        )
        self.client.query(
            merge_config_sql,
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter(
                        "organization_id",
                        "STRING",
                        effective_organization_id,
                    ),
                    bigquery.ScalarQueryParameter(
                        "policy_id",
                        "STRING",
                        config_row["policy_id"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "domain",
                        "STRING",
                        config_row["domain"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "policy_version",
                        "STRING",
                        config_row["policy_version"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "policy_name",
                        "STRING",
                        config_row["policy_name"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "policy_description",
                        "STRING",
                        config_row["policy_description"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "active_flag",
                        "STRING",
                        config_row["active_flag"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "effective_from",
                        "TIMESTAMP",
                        config_row["effective_from"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "effective_to",
                        "TIMESTAMP",
                        config_row["effective_to"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "default_decision_mode",
                        "STRING",
                        config_row["default_decision_mode"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "review_required_flag",
                        "STRING",
                        config_row["review_required_flag"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "allow_auto_merge_flag",
                        "STRING",
                        config_row["allow_auto_merge_flag"],
                    ),

                    bigquery.ScalarQueryParameter(
                        "created_by",
                        "STRING",
                        config_row["created_by"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "created_at",
                        "TIMESTAMP",
                        config_row["created_at"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "updated_by",
                        "STRING",
                        config_row["updated_by"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "updated_at",
                        "TIMESTAMP",
                        config_row["updated_at"],
                    ),
                ]
            ),
        ).result()

        runtime_table = self.client.get_table(
            self.policy_thresholds_table
        )

        print(
            "POLICY THRESHOLDS RUNTIME SCHEMA:",
            [field.name for field in runtime_table.schema],
            flush=True,
        )

        print(
            "POLICY THRESHOLDS TABLE TYPE:",
            runtime_table.table_type,
            flush=True,
        )

        self.client.query(
            merge_thresholds_sql,
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter(
                        "organization_id",
                        "STRING",
                        effective_organization_id,
                    ),
                    bigquery.ScalarQueryParameter(
                        "threshold_id",
                        "STRING",
                        threshold_row["threshold_id"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "policy_id",
                        "STRING",
                        threshold_row["policy_id"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "domain",
                        "STRING",
                        threshold_row["domain"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "policy_version",
                        "STRING",
                        threshold_row["policy_version"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "min_review_score",
                        "FLOAT64",
                        threshold_row["min_review_score"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "min_approve_merge_score",
                        "FLOAT64",
                        threshold_row["min_approve_merge_score"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "min_auto_merge_score",
                        "FLOAT64",
                        threshold_row["min_auto_merge_score"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "max_auto_merge_override_rate",
                        "FLOAT64",
                        threshold_row["max_auto_merge_override_rate"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "max_review_override_rate",
                        "FLOAT64",
                        threshold_row["max_review_override_rate"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "high_risk_score_cutoff",
                        "FLOAT64",
                        threshold_row["high_risk_score_cutoff"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "medium_risk_score_cutoff",
                        "FLOAT64",
                        threshold_row["medium_risk_score_cutoff"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "require_exact_dob_flag",
                        "STRING",
                        threshold_row["require_exact_dob_flag"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "require_email_or_address_flag",
                        "STRING",
                        threshold_row["require_email_or_address_flag"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "require_manual_review_on_conflict_flag",
                        "STRING",
                        threshold_row["require_manual_review_on_conflict_flag"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "active_flag",
                        "STRING",
                        threshold_row["active_flag"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "effective_from",
                        "TIMESTAMP",
                        threshold_row["effective_from"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "effective_to",
                        "TIMESTAMP",
                        threshold_row["effective_to"],
                    ),
                ]
            ),
        ).result()

        self.clear_cache()

        return {
            "status": "DRAFT_SAVED",
            "organization_id": effective_organization_id,
            "domain": domain,
            "policy_version": policy_version,
            "updated_by": updated_by,
            "saved_at": now_ts.isoformat(),
            "config": config_row,
            "thresholds": threshold_row,
        }

    def publish_policy_version(
        self,
        domain: Optional[str],
        policy_version: str,
        organization_id: str,
        published_by: str = "system",
    ) -> dict[str, Any]:
        effective_organization_id = self._require_organization_id(organization_id)
        effective_domain = self._normalize_domain(domain)
        effective_policy_version = self._normalize_policy_version(policy_version)
        publisher = str(published_by or "system").strip()
        now_ts = datetime.now(timezone.utc)

        deactivate_config_sql = f"""
        UPDATE `{self.policy_config_table}`
        SET
          active_flag = 'N',
          effective_to = CURRENT_TIMESTAMP()
        WHERE organization_id = @organization_id
          AND domain = @domain
          AND active_flag = 'Y'
        """

        deactivate_thresholds_sql = f"""
        UPDATE `{self.policy_thresholds_table}`
        SET
          active_flag = 'N',
          effective_to = CURRENT_TIMESTAMP()
        WHERE organization_id = @organization_id
          AND domain = @domain
          AND active_flag = 'Y'
        """

        deactivate_risk_rules_sql = f"""
        UPDATE `{self.policy_risk_rules_table}`
        SET
          active_flag = 'N',
          effective_to = CURRENT_TIMESTAMP()
        WHERE organization_id = @organization_id
          AND domain = @domain
          AND active_flag = 'Y'
          AND policy_version != @policy_version
        """

        activate_config_sql = f"""
        UPDATE `{self.policy_config_table}`
        SET
          active_flag = 'Y',
          effective_from = COALESCE(effective_from, CURRENT_TIMESTAMP()),
          effective_to = NULL
        WHERE organization_id = @organization_id
          AND domain = @domain
          AND policy_version = @policy_version
        """

        activate_thresholds_sql = f"""
        UPDATE `{self.policy_thresholds_table}`
        SET
          active_flag = 'Y',
          effective_from = COALESCE(effective_from, CURRENT_TIMESTAMP()),
          effective_to = NULL
        WHERE organization_id = @organization_id
          AND domain = @domain
          AND policy_version = @policy_version
        """

        activate_risk_rules_sql = f"""
        UPDATE `{self.policy_risk_rules_table}`
        SET
          active_flag = 'Y',
          effective_from = COALESCE(effective_from, CURRENT_TIMESTAMP()),
          effective_to = NULL
        WHERE organization_id = @organization_id
          AND domain = @domain
          AND policy_version = @policy_version
        """

        base_job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "organization_id",
                    "STRING",
                    effective_organization_id,
                ),
                bigquery.ScalarQueryParameter(
                    "domain",
                    "STRING",
                    effective_domain,
                ),
                bigquery.ScalarQueryParameter(
                    "policy_version",
                    "STRING",
                    effective_policy_version,
                ),
            ]
        )

        deactivate_cfg_job = self.client.query(
            deactivate_config_sql,
            job_config=base_job_config,
        )
        deactivate_cfg_job.result()

        deactivate_thr_job = self.client.query(
            deactivate_thresholds_sql,
            job_config=base_job_config,
        )
        deactivate_thr_job.result()

        deactivate_risk_job = self.client.query(
            deactivate_risk_rules_sql,
            job_config=base_job_config,
        )
        deactivate_risk_job.result()

        activate_cfg_job = self.client.query(
            activate_config_sql,
            job_config=base_job_config,
        )
        activate_cfg_job.result()

        activate_thr_job = self.client.query(
            activate_thresholds_sql,
            job_config=base_job_config,
        )
        activate_thr_job.result()

        activate_risk_job = self.client.query(
            activate_risk_rules_sql,
            job_config=base_job_config,
        )
        activate_risk_job.result()

        self.clear_cache()

        cfg_affected = activate_cfg_job.num_dml_affected_rows or 0
        thr_affected = activate_thr_job.num_dml_affected_rows or 0
        risk_affected = activate_risk_job.num_dml_affected_rows or 0

        if cfg_affected == 0 and thr_affected == 0 and risk_affected == 0:
            raise ValueError(
                f"No draft policy found for domain={effective_domain}, "
                f"policy_version={effective_policy_version}"
            )

        return {
            "status": "PUBLISHED",
            "organization_id": effective_organization_id,
            "domain": effective_domain,
            "policy_version": effective_policy_version,
            "published_by": publisher,
            "published_at": now_ts.isoformat(),
            "config_rows_activated": cfg_affected,
            "threshold_rows_activated": thr_affected,
            "risk_rule_rows_activated": risk_affected,
              }

    def build_decision_context(
        self,
        req: MatchExplainRequest,
        organization_id: str,
    ) -> dict[str, Any]:
        start_time = time.perf_counter()
        effective_organization_id = self._require_organization_id(organization_id)

        domain = self._normalize_domain(req.domain)
        request_id = self._ensure_request_id(req.request_id)
        audit_packet_id = self._build_audit_packet_id()

        policy_cfg, policy_cfg_cache_hit = self._get_active_policy_config(
            domain,
            effective_organization_id,
        )

        policy_version = (
            self._normalize_policy_version(req.policy_version)
            if req.policy_version
            else self._normalize_policy_version(policy_cfg.get("policy_version"))
        )

        thresholds, thresholds_cache_hit = self._get_active_policy_thresholds(
            domain,
            policy_version,
            effective_organization_id,
        )

        risk_rules, risk_rules_cache_hit = self._get_active_policy_risk_rules(
            domain,
            policy_version,
            req.triggered_rules,
            effective_organization_id,
        )

        learning_data = self.metrics.get_override_learning_context(
            organization_id=effective_organization_id,
            domain=domain,
            triggered_rules=req.triggered_rules,
            match_score=req.match_score,
            record_a_source_system=getattr(req.record_a, "source_system", None),
            record_b_source_system=getattr(req.record_b, "source_system", None),
            limit=5,
        )

        if not isinstance(learning_data, dict):
            learning_data = {}

        learning_rows = learning_data.get("summary_patterns", [])
        if not isinstance(learning_rows, list):
            learning_rows = []

        learning_context = self._format_override_learning_context(learning_rows)
        threshold_guidance = self._build_threshold_guidance(req.match_score, thresholds)
        risk_rule_guidance = self._build_risk_rule_guidance(risk_rules)

        recommendation_packet = self._evaluate_policy_decision(
            req=req,
            policy_cfg=policy_cfg,
            thresholds=thresholds,
            risk_rules=risk_rules,
            learning_rows=learning_rows,
        )

        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

        policy_context = (
            f"Effective domain: {domain}\n"
            f"Effective policy version: {policy_version}\n"
            f"Default decision mode: {policy_cfg.get('default_decision_mode', 'REVIEW')}\n"
            f"Review required flag: {policy_cfg.get('review_required_flag', 'Y')}\n"
            f"Allow auto merge flag: {policy_cfg.get('allow_auto_merge_flag', 'N')}\n"
            f"Threshold guidance: {threshold_guidance}\n"
            f"Policy risk rule guidance:\n{risk_rule_guidance}\n"
            f"Recommended policy decision: {recommendation_packet['recommendation']}\n"
            f"Recommendation reason: {recommendation_packet['recommendation_reason']}\n"
            f"Composite risk score: {recommendation_packet['composite_risk_score']}/100\n"
            f"Composite risk band: {recommendation_packet['composite_risk_band']}\n"
            f"Highest risk level: {recommendation_packet['highest_risk_level']}\n"
            f"Primary risk driver: {recommendation_packet.get('primary_risk_driver') or 'N/A'}"
        )

        return {
            "organization_id": effective_organization_id,
            "domain": domain,
            "policy_version": policy_version,
            "request_id": request_id,
            "audit_packet_id": audit_packet_id,
            "learning_context": learning_context,
            "threshold_guidance": threshold_guidance,
            "policy_context": policy_context,
            "policy_config": policy_cfg,
            "policy_thresholds": thresholds,
            "policy_risk_rules": risk_rules,
            "policy_recommendation": recommendation_packet,
            "generated_at": self._utc_now_iso(),
            "trace": {
                "engine": "PolicyIntelligenceEngine",
                "domain": domain,
                "policy_version": policy_version,
                "triggered_rule_count": len(req.triggered_rules or []),
                "matched_risk_rule_count": len(risk_rules),
                "policy_config_cache_hit": policy_cfg_cache_hit,
                "policy_thresholds_cache_hit": thresholds_cache_hit,
                "policy_risk_rules_cache_hit": risk_rules_cache_hit,
                "processing_time_ms": elapsed_ms,
                "used_learning_context": bool(learning_rows),
                "recommended_decision": recommendation_packet["recommendation"],
                "highest_risk_level": recommendation_packet["highest_risk_level"],
                "risk_band": recommendation_packet["risk_band"],
                "composite_risk_score": recommendation_packet["composite_risk_score"],
                "composite_risk_band": recommendation_packet["composite_risk_band"],
                "primary_risk_driver": recommendation_packet.get("primary_risk_driver"),
            },
        }