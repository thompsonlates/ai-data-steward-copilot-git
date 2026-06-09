from app.workflow.connectors.jira_connector import JiraConnector

jira = JiraConnector()

response = jira.create_issue(
    summary="AI Copilot Governance Workflow Test",
    description="Testing AI Data Steward Copilot Jira integration.",
    issue_type="Idea",
)

print(response)