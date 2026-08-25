from __future__ import annotations

import uuid
from datetime import datetime, timezone

from google.cloud import bigquery


class AiRecommendationFeedbackRepository:
    def __init__(self) -> None:
        self.client = bigquery.Client()

        self.table_id = (
            "api-project-503305938314."
            "ai_data_steward_mvp."
            "AI_RECOMMENDATION_FEEDBACK"
        )

    def save_feedback(
        self,
        *,
        organization_id: str,
        recommendation_id: str,
        recommendation_type: str,
        profile_run_id: str | None,
        domain: str | None,
        rule_id: str | None,
        ai_recommendation: str | None,
        ai_confidence: float | None,
        steward_decision: str,
        steward_comment: str | None,
        override_reason: str | None,
        submitted_by: str | None,
        source_component: str | None,
    ) -> dict:
        organization_id = str(
            organization_id or ""
        ).strip()

        if not organization_id:
            raise ValueError(
                "organization_id is required."
            )

        feedback_id = (
            f"aif_{uuid.uuid4().hex[:20]}"
        )

        submitted_at = datetime.now(
            timezone.utc
        )

        recommendation_status = {
            "APPROVE": "APPROVED",
            "REJECT": "REJECTED",
            "MODIFY": "MODIFICATION_REQUESTED",
            "DEFER": "DEFERRED",
        }.get(
            steward_decision,
            "PENDING",
        )

        row = {
            "feedback_id": feedback_id,
            "organization_id": organization_id,
            "recommendation_id": recommendation_id,
            "recommendation_type": (
                recommendation_type
            ),
            "profile_run_id": profile_run_id,
            "domain": domain,
            "rule_id": rule_id,
            "ai_recommendation": (
                ai_recommendation
            ),
            "ai_confidence": ai_confidence,
            "steward_decision": (
                steward_decision
            ),
            "steward_comment": (
                steward_comment
            ),
            "override_reason": override_reason,
            "submitted_by": submitted_by,
            "submitted_at": (
                submitted_at.isoformat()
            ),
            "source_component": (
                source_component
            ),
            "recommendation_status": (
                recommendation_status
            ),
        }

        errors = self.client.insert_rows_json(
            self.table_id,
            [row],
        )

        if errors:
            raise RuntimeError(
                "Failed to persist AI recommendation "
                f"feedback: {errors}"
            )

        return {
            "success": True,
            "feedback_id": feedback_id,
            "recommendation_status": (
                recommendation_status
            ),
            "submitted_at": (
                submitted_at.isoformat()
            ),
        }