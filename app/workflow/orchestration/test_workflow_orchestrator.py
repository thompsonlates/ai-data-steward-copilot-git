from app.workflow.orchestration.workflow_orchestrator import WorkflowOrchestrator

orchestrator = WorkflowOrchestrator()

result = orchestrator.evaluate_match_explanation(
    {
        "explanation_id": "exp_test_001",
        "request_id": "req_test_001",
        "domain": "PROVIDER",
        "policy_version": "v1",
        "ai_decision": "REVIEW_REQUIRED",
        "recommended_action": "REVIEW_REQUIRED",
        "confidence": 0.72,
        "risk_flag": "HIGH",
        "automation_readiness_score": 44,
        "primary_risk_driver": "NPI_REUSE_DETECTED",
        "explanation_summary": "Shared NPI detected across conflicting provider identities.",
        "record_a": {
            "provider_id": "MDM0001",
            "npi": "1887004555",
        },
        "record_b": {
            "provider_id": "MDM0002",
            "npi": "1887004555",
        },
    }
)

print(result)