from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.workflow.connectors.jira_connector import JiraConnector


@dataclass
class GovernanceWorkflowResult:
    created: bool
    reason: str
    jira_key: Optional[str] = None
    jira_url: Optional[str] = None


class WorkflowOrchestrator:
    """
    Decides when AI Data Steward Copilot should create governance workflow tickets.

    This file should contain governance-routing logic only.
    It should not know Jira API details.
    """

    def __init__(self, jira_connector: Optional[JiraConnector] = None):
        self.jira = jira_connector or JiraConnector()

    def evaluate_match_explanation(
        self,
        explanation: Dict[str, Any],
    ) -> GovernanceWorkflowResult:
        risk_flag = str(explanation.get("risk_flag") or "").upper()
        ai_decision = str(explanation.get("ai_decision") or "").upper()
        recommended_action = str(
            explanation.get("final_recommended_action")
            or explanation.get("recommended_action")
            or ""
        ).upper()
        primary_risk_driver = str(
            explanation.get("primary_risk_driver")
            or explanation.get("primary_signal")
            or "UNKNOWN"
        ).upper()

        automation_readiness = explanation.get("automation_readiness_score")
        try:
            automation_readiness_score = int(automation_readiness)
        except (TypeError, ValueError):
            automation_readiness_score = None

        should_create = self._should_create_governance_ticket(
            risk_flag=risk_flag,
            ai_decision=ai_decision,
            recommended_action=recommended_action,
            primary_risk_driver=primary_risk_driver,
            automation_readiness_score=automation_readiness_score,
        )

        if not should_create:
            return GovernanceWorkflowResult(
                created=False,
                reason="No governance workflow ticket required.",
            )

        summary = self._build_summary(
            risk_flag=risk_flag,
            primary_risk_driver=primary_risk_driver,
            recommended_action=recommended_action,
        )

        description = self._build_description(explanation)

        issue = self.jira.create_issue(
            summary=summary,
            description=description,
            issue_type="Idea",
            priority=None,
            labels=[
                "ai-data-steward-copilot",
                "governance-review",
                primary_risk_driver.lower().replace("_", "-"),
            ],
        )

        jira_key = issue.get("key")
        jira_url = f"{self.jira.base_url}/browse/{jira_key}" if jira_key else None

        return GovernanceWorkflowResult(
            created=True,
            reason="Governance workflow ticket created.",
            jira_key=jira_key,
            jira_url=jira_url,
        )

    def _should_create_governance_ticket(
        self,
        risk_flag: str,
        ai_decision: str,
        recommended_action: str,
        primary_risk_driver: str,
        automation_readiness_score: Optional[int],
    ) -> bool:
        high_risk_drivers = {
            "NPI_REUSE_DETECTED",
            "IDENTIFIER_COLLISION",
            "ADDRESS_MATCH",
            "CROSS_SYSTEM_IDENTITY_COLLISION",
            "REGISTRY_TRUST_DEGRADED",
            "DEA_CONFLICT",
        }

        if risk_flag in {"HIGH", "SEVERE", "CRITICAL"}:
            return True

        if recommended_action in {"REVIEW_REQUIRED", "BLOCK_MERGE", "REJECT_MERGE"}:
            return True

        if ai_decision in {"REVIEW_REQUIRED", "BLOCK_MERGE", "REJECT_MERGE"}:
            return True

        if primary_risk_driver in high_risk_drivers:
            return True

        if automation_readiness_score is not None and automation_readiness_score < 50:
            return True

        return False

    def _build_summary(
        self,
        risk_flag: str,
        primary_risk_driver: str,
        recommended_action: str,
    ) -> str:
        readable_driver = primary_risk_driver.replace("_", " ").title()
        readable_action = recommended_action.replace("_", " ").title()

        return (
            f"Governance Review Required: {readable_driver} "
            f"({risk_flag or 'UNKNOWN'} Risk / {readable_action or 'Review'})"
        )

    def _build_description(self, explanation: Dict[str, Any]) -> str:
        explanation_id = explanation.get("explanation_id", "N/A")
        request_id = explanation.get("request_id", "N/A")
        domain = explanation.get("domain", "N/A")
        policy_version = explanation.get("policy_version", "N/A")

        ai_decision = explanation.get("ai_decision", "N/A")
        recommended_action = (
            explanation.get("final_recommended_action")
            or explanation.get("recommended_action")
            or "N/A"
        )
        confidence = explanation.get("confidence", "N/A")
        risk_flag = explanation.get("risk_flag", "N/A")
        automation_readiness = explanation.get("automation_readiness_score", "N/A")
        primary_risk_driver = (
            explanation.get("primary_risk_driver")
            or explanation.get("primary_signal")
            or "N/A"
        )
        summary = explanation.get("explanation_summary", "No summary provided.")

        record_a = explanation.get("record_a", {}) or {}
        record_b = explanation.get("record_b", {}) or {}

        return f"""
AI Data Steward Copilot Governance Workflow

Explanation ID: {explanation_id}
Request ID: {request_id}
Domain: {domain}
Policy Version: {policy_version}

AI Decision: {ai_decision}
Recommended Action: {recommended_action}
Confidence: {confidence}
Risk Flag: {risk_flag}
Automation Readiness Score: {automation_readiness}
Primary Risk Driver: {primary_risk_driver}

Explanation Summary:
{summary}

Record A:
{record_a}

Record B:
{record_b}

Governance Action Required:
Please review the AI decision, validate the identity evidence, and confirm whether this case should be approved, rejected, merged, unmerged, or escalated for policy review.
""".strip()