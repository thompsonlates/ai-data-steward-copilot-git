from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.cloud import bigquery


class GovernanceAiRecommendationRepository:
    def __init__(
        self,
        client: Optional[bigquery.Client] = None,
    ) -> None:
        self.project_id = (
            os.getenv("GOOGLE_CLOUD_PROJECT")
            or os.getenv("PROJECT_ID")
            or "api-project-503305938314"
        )

        self.dataset_id = os.getenv(
            "BQ_DATASET",
            "ai_data_steward_mvp",
        )

        self.table_name = os.getenv(
            "BQ_GOVERNANCE_AI_RECOMMENDATIONS_TABLE",
            "GOVERNANCE_AI_RECOMMENDATIONS",
        )

        self.table_id = (
            f"{self.project_id}."
            f"{self.dataset_id}."
            f"{self.table_name}"
        )

        self.client = client or bigquery.Client(
            project=self.project_id
        )

    @staticmethod
    def _require_organization_id(
        organization_id: str,
    ) -> str:
        value = str(
            organization_id or ""
        ).strip()

        if not value:
            raise ValueError(
                "organization_id is required"
            )

        return value

    @staticmethod
    def _normalize_string(
        value: Any,
    ) -> str:
        return str(
            value or ""
        ).strip()

    @staticmethod
    def _normalize_list(
        values: Optional[List[Any]],
    ) -> List[str]:
        return [
            str(value).strip()
            for value in (values or [])
            if str(value).strip()
        ]

    def _build_fingerprint(
        self,
        *,
        organization_id: str,
        dataset_id: str,
        domain: Optional[str],
        headline: str,
        priority: str,
        recommended_actions: List[str],
    ) -> str:
        """
        Stable fingerprint for the same logical
        Governance AI recommendation.

        organization_id is intentionally included
        so recommendations can never collide
        across tenants.
        """

        payload = {
            "organization_id": organization_id,
            "dataset_id": dataset_id,
            "domain": (
                domain.upper()
                if domain
                else None
            ),
            "headline": headline,
            "priority": priority.upper(),
            "recommended_actions": (
                recommended_actions
            ),
        }

        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )

        return hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()

    def _find_existing(
        self,
        *,
        organization_id: str,
        recommendation_fingerprint: str,
    ) -> Optional[Dict[str, Any]]:
        organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        query = f"""
        SELECT
          recommendation_id,
          organization_id,
          recommendation_fingerprint,
          dataset_id,
          dataset_name,
          domain,
          headline,
          summary,
          priority,
          recommended_actions,
          supporting_evidence,
          confidence,
          status,
          created_at,
          updated_at
        FROM `{self.table_id}`
        WHERE organization_id = @organization_id
          AND recommendation_fingerprint =
              @recommendation_fingerprint
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
                    "recommendation_fingerprint",
                    "STRING",
                    recommendation_fingerprint,
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

        return dict(rows[0].items())

    def get_or_create_recommendation(
        self,
        *,
        organization_id: str,
        dataset_id: str,
        dataset_name: str,
        domain: Optional[str],
        headline: str,
        summary: str,
        priority: str,
        recommended_actions: List[str],
        supporting_evidence: List[str],
        confidence: Optional[float],
    ) -> Dict[str, Any]:
        organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        dataset_id = self._normalize_string(
            dataset_id
        )
        dataset_name = self._normalize_string(
            dataset_name
        )
        headline = self._normalize_string(
            headline
        )
        summary = self._normalize_string(
            summary
        )
        priority = (
            self._normalize_string(
                priority
            ).upper()
            or "MEDIUM"
        )

        normalized_domain = (
            self._normalize_string(
                domain
            ).upper()
            if domain
            else None
        )

        normalized_actions = (
            self._normalize_list(
                recommended_actions
            )
        )

        normalized_evidence = (
            self._normalize_list(
                supporting_evidence
            )
        )

        fingerprint = self._build_fingerprint(
            organization_id=organization_id,
            dataset_id=dataset_id,
            domain=normalized_domain,
            headline=headline,
            priority=priority,
            recommended_actions=(
                normalized_actions
            ),
        )

        existing = self._find_existing(
            organization_id=organization_id,
            recommendation_fingerprint=(
                fingerprint
            ),
        )

        if existing:
            return existing

        recommendation_id = (
            f"gov_{uuid.uuid4().hex[:20]}"
        )

        now = datetime.now(
            timezone.utc
        )

        row = {
            "recommendation_id":
                recommendation_id,
            "organization_id":
                organization_id,
            "recommendation_fingerprint":
                fingerprint,
            "dataset_id":
                dataset_id,
            "dataset_name":
                dataset_name,
            "domain":
                normalized_domain,
            "headline":
                headline,
            "summary":
                summary,
            "priority":
                priority,
            "recommended_actions":
                normalized_actions,
            "supporting_evidence":
                normalized_evidence,
            "confidence":
                confidence,
            "status":
                "ACTIVE",
            "created_at":
                now.isoformat(),
            "updated_at":
                now.isoformat(),
        }

        errors = (
            self.client.insert_rows_json(
                self.table_id,
                [row],
            )
        )

        if errors:
            raise RuntimeError(
                "Failed to persist Governance AI "
                f"recommendation: {errors}"
            )

        return row