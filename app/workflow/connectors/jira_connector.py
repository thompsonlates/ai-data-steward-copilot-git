import os
import requests
from typing import Optional, Dict, Any

from dotenv import load_dotenv

load_dotenv()


class JiraConnector:
    def __init__(self):
        self.base_url = os.getenv("JIRA_BASE_URL")
        self.email = os.getenv("JIRA_EMAIL")
        self.api_token = os.getenv("JIRA_API_TOKEN")
        self.project_key = os.getenv("JIRA_PROJECT_KEY")

        if not all([
            self.base_url,
            self.email,
            self.api_token,
            self.project_key,
        ]):
            raise ValueError(
                "Missing Jira environment variables."
            )

        self.auth = (self.email, self.api_token)

        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def create_issue(
        self,
        summary: str,
        description: str,
        issue_type: str = "Idea",
        priority: Optional[str] = None,
        labels: Optional[list[str]] = None,
    ) -> Dict[str, Any]:

        payload = {
    "fields": {
        "project": {
            "key": self.project_key
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
            "name": issue_type
        },
    }
}

        if labels:
            payload["fields"]["labels"] = labels

        if priority:
            payload["fields"]["priority"] = {
                "name": priority
            }

        response = requests.post(
            f"{self.base_url}/rest/api/3/issue",
            json=payload,
            auth=self.auth,
            headers=self.headers,
            timeout=30,
        )

        if response.status_code not in [200, 201]:
            raise Exception(
                f"Jira issue creation failed: "
                f"{response.status_code} - {response.text}"
            )

        return response.json()

    def add_comment(
        self,
        issue_key: str,
        comment: str,
    ) -> Dict[str, Any]:

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
                                "text": comment
                            }
                        ]
                    }
                ]
            }
        }

        response = requests.post(
            f"{self.base_url}/rest/api/3/issue/{issue_key}/comment",
            json=payload,
            auth=self.auth,
            headers=self.headers,
            timeout=30,
        )

        if response.status_code not in [200, 201]:
            raise Exception(
                f"Jira comment failed: "
                f"{response.status_code} - {response.text}"
            )

        return response.json()