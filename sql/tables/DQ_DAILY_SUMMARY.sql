CREATE OR REPLACE TABLE `api-project-503305938314.ai_data_steward_mvp.DQ_DAILY_SUMMARY` AS
SELECT
  metric_date,
  domain,
  total_records,
  avg_record_score,
  records_below_threshold,
  total_findings,
  critical_findings,
  high_findings,
  medium_findings,
  low_findings,
  total_rules_executed,
  failed_rule_count,
  duplicate_record_count,
  records_flagged_by_ai,
  ai_recommendations_generated,
  steward_actions_taken,
  automated_fixes_applied,
  dq_health_score,
  dq_risk_score,
  automation_readiness_score,
  summary_created_at AS created_at
FROM `api-project-503305938314.ai_data_steward_mvp.DQ_INTELLIGENCE_DASHBOARD_VW`;
