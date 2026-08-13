from __future__ import annotations

import os
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv


load_dotenv()


class JiraConnector:
    TENANT_LABEL_PREFIX = "org_"

    def __init__(self) -> None:
        self.base_url = (os.getenv("JIRA_BASE_URL") or "").rstrip("/")
        self.email = os.getenv("JIRA_EMAIL")
        self.api_token = os.getenv("JIRA_API_TOKEN")
        self.project_key = os.getenv("JIRA_PROJECT_KEY")

        if not all(
            [
                self.base_url,
                self.email,
                self.api_token,
                self.project_key,
            ]
        ):
            raise ValueError(
                "Missing Jira environment variables."
            )

        self.auth = (self.email, self.api_token)

        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _require_organization_id(
        organization_id: str,
    ) -> str:
        normalized = str(organization_id or "").strip()

        if not normalized:
            raise ValueError(
                "organization_id is required for tenant-isolated Jira operations."
            )

        return normalized

    @classmethod
    def _organization_label(
        cls,
        organization_id: str,
    ) -> str:
        normalized = cls._require_organization_id(
            organization_id
        )

        # Jira labels should remain URL/API friendly.
        safe = "".join(
            ch if ch.isalnum() or ch in {"_", "-"} else "-"
            for ch in normalized
        )

        return f"tenant-{safe}"

    def _raise_for_jira_error(
        self,
        response: requests.Response,
        *,
        action: str,
    ) -> None:
        if response.status_code in {200, 201, 204}:
            return

        raise RuntimeError(
            f"Jira {action} failed: "
            f"{response.status_code} - {response.text}"
        )

    def _get_issue_labels(
        self,
        *,
        issue_key: str,
    ) -> list[str]:
        response = requests.get(
            f"{self.base_url}/rest/api/3/issue/{issue_key}",
            params={"fields": "labels"},
            auth=self.auth,
            headers=self.headers,
            timeout=30,
        )

        self._raise_for_jira_error(
            response,
            action="issue lookup",
        )

        payload = response.json()
        fields = payload.get("fields") or {}
        labels = fields.get("labels") or []

        return [
            str(label)
            for label in labels
            if label is not None
        ]

    def _assert_issue_belongs_to_organization(
        self,
        *,
        issue_key: str,
        organization_id: str,
    ) -> None:
        expected_label = self._organization_label(
            organization_id
        )

        labels = self._get_issue_labels(
            issue_key=issue_key
        )

        if expected_label not in labels:
            raise PermissionError(
                "Jira issue does not belong to the authenticated organization."
            )

    def create_issue(
        self,
        *,
        organization_id: str,
        summary: str,
        description: str,
        issue_type: str = "Idea",
        priority: Optional[str] = None,
        labels: Optional[list[str]] = None,
    ) -> Dict[str, Any]:
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        tenant_label = self._organization_label(
            effective_organization_id
        )

        effective_labels = list(labels or [])

        if tenant_label not in effective_labels:
            effective_labels.append(tenant_label)

        payload: dict[str, Any] = {
            "fields": {
                "project": {
                    "key": self.project_key,
                },
                "summary": summary,
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {
                                    "type": "text",
                                    "text": description,
                                }
                            ],
                        }
                    ],
                },
                "issuetype": {
                    "name": issue_type,
                },
                "labels": effective_labels,
            }
        }

        if priority:
            payload["fields"]["priority"] = {
                "name": priority,
            }

        response = requests.post(
            f"{self.base_url}/rest/api/3/issue",
            json=payload,
            auth=self.auth,
            headers=self.headers,
            timeout=30,
        )

        self._raise_for_jira_error(
            response,
            action="issue creation",
        )

        result = response.json()

        # Add tenant context to the returned application payload.
        result["organization_id"] = (
            effective_organization_id
        )

        return result

    def add_comment(
        self,
        *,
        organization_id: str,
        issue_key: str,
        comment: str,
    ) -> Dict[str, Any]:
        effective_organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        # Fail closed: verify tenant ownership before mutating an issue.
        self._assert_issue_belongs_to_organization(
            issue_key=issue_key,
            organization_id=effective_organization_id,
        )

        payload = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": comment,
                            }
                        ],
                    }
                ],
            }
        }

        response = requests.post(
            (
                f"{self.base_url}/rest/api/3/issue/"
                f"{issue_key}/comment"
            ),
            json=payload,
            auth=self.auth,
            headers=self.headers,
            timeout=30,
        )

        self._raise_for_jira_error(
            response,
            action="comment creation",
        )

        result = response.json()
        result["organization_id"] = (
            effective_organization_id
        )

        return result
