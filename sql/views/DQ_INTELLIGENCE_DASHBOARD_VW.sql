CREATE OR REPLACE VIEW `api-project-503305938314.ai_data_steward_mvp.DQ_INTELLIGENCE_DASHBOARD_VW` AS

WITH domains AS (
  SELECT 'CUSTOMER' AS domain UNION ALL
  SELECT 'PROVIDER' UNION ALL
  SELECT 'SUPPLIER' UNION ALL
  SELECT 'PRODUCT' UNION ALL
  SELECT 'PATIENT' UNION ALL
  SELECT 'LOCATION'
),

record_scores AS (
  SELECT
    DATE(created_at) AS metric_date,
    UPPER(domain) AS domain,
    COUNT(*) AS total_records,
    AVG(record_score) AS avg_record_score,
    COUNTIF(record_score < 80) AS records_below_threshold,
    SAFE_DIVIDE(COUNTIF(record_score < 80), COUNT(*)) AS records_below_threshold_rate,

    AVG(completeness_score) AS avg_completeness_score,
    AVG(validity_score) AS avg_validity_score,
    AVG(standardization_score) AS avg_standardization_score,
    AVG(consistency_score) AS avg_consistency_score,
    AVG(uniqueness_score) AS avg_uniqueness_score,

    COUNT(*) AS scored_record_count,
    MIN(record_score) AS min_record_score,
    MAX(record_score) AS max_record_score,
    COUNTIF(record_score < 50) AS very_low_score_count,
    COUNTIF(record_score >= 50 AND record_score < 70) AS low_score_count,
    COUNTIF(record_score >= 70 AND record_score < 90) AS medium_score_count,
    COUNTIF(record_score >= 90) AS high_score_count
  FROM `api-project-503305938314.ai_data_steward_mvp.DQ_RECORD_SCORE`
  GROUP BY metric_date, domain
),

findings AS (
  SELECT
    DATE(created_at) AS metric_date,
    UPPER(domain) AS domain,

    COUNT(*) AS total_findings,
    COUNTIF(UPPER(severity) = 'CRITICAL') AS critical_findings,
    COUNTIF(UPPER(severity) = 'HIGH') AS high_findings,
    COUNTIF(UPPER(severity) = 'MEDIUM') AS medium_findings,
    COUNTIF(UPPER(severity) = 'LOW') AS low_findings,

    COUNTIF(UPPER(status) = 'OPEN') AS open_findings_count,
    COUNTIF(UPPER(status) = 'ACCEPTED') AS accepted_findings_count,
    COUNTIF(UPPER(status) = 'RESOLVED') AS resolved_findings_count,
    COUNTIF(UPPER(status) = 'WAIVED') AS waived_findings_count,

    COUNT(DISTINCT record_id) AS records_with_findings,
    COUNT(DISTINCT rule_id) AS failed_rule_count,

    COUNTIF(UPPER(rule_type) = 'DUPLICATE') AS duplicate_record_count,

    COUNTIF(UPPER(severity) = 'CRITICAL') AS scored_critical_issue_count,
    COUNTIF(UPPER(severity) = 'HIGH') AS scored_high_issue_count,
    COUNTIF(UPPER(severity) = 'MEDIUM') AS scored_medium_issue_count,
    COUNTIF(UPPER(severity) = 'LOW') AS scored_low_issue_count,
    COUNT(*) AS scored_total_issue_count,

    COUNT(DISTINCT rule_id) AS total_rules_executed,
    COUNT(DISTINCT rule_id) AS rules_triggered,
    COUNT(DISTINCT rule_id) AS configured_rule_count,
    COUNT(DISTINCT rule_id) AS active_rule_count,
    0 AS inactive_rule_count,

    COUNT(DISTINCT IF(UPPER(severity) = 'CRITICAL', rule_id, NULL)) AS configured_critical_rules,
    COUNT(DISTINCT IF(UPPER(severity) = 'HIGH', rule_id, NULL)) AS configured_high_rules,
    COUNT(DISTINCT IF(UPPER(severity) = 'MEDIUM', rule_id, NULL)) AS configured_medium_rules,
    COUNT(DISTINCT IF(UPPER(severity) = 'LOW', rule_id, NULL)) AS configured_low_rules,

    COUNT(DISTINCT IF(UPPER(rule_category) = 'VALIDITY', rule_id, NULL)) AS validity_rule_count,
    COUNT(DISTINCT IF(UPPER(rule_category) = 'COMPLETENESS', rule_id, NULL)) AS completeness_rule_count,
    COUNT(DISTINCT IF(UPPER(rule_category) = 'STANDARDIZATION', rule_id, NULL)) AS standardization_rule_count,
    COUNT(DISTINCT IF(UPPER(rule_category) = 'UNIQUENESS', rule_id, NULL)) AS uniqueness_rule_count,
    COUNT(DISTINCT IF(UPPER(rule_category) = 'CONSISTENCY', rule_id, NULL)) AS consistency_rule_count,

    AVG(rule_weight) AS avg_rule_weight
  FROM `api-project-503305938314.ai_data_steward_mvp.DQ_FINDINGS`
  GROUP BY metric_date, domain
),

ai_recs AS (
  SELECT
    DATE(created_at) AS metric_date,
    UPPER(domain) AS domain,

    COUNT(DISTINCT record_id) AS records_flagged_by_ai,
    COUNT(*) AS ai_recommendations_generated,

    COUNTIF(UPPER(status) = 'OPEN') AS open_ai_recommendations,
    COUNTIF(UPPER(status) = 'ACCEPTED') AS accepted_ai_recommendations,
    COUNTIF(UPPER(status) = 'REJECTED') AS rejected_ai_recommendations,
    COUNTIF(UPPER(status) = 'IMPLEMENTED') AS implemented_ai_recommendations,

    COUNTIF(UPPER(priority) = 'HIGH') AS high_priority_recommendations,
    COUNTIF(UPPER(priority) = 'MEDIUM') AS medium_priority_recommendations,
    COUNTIF(UPPER(priority) = 'LOW') AS low_priority_recommendations,

    AVG(confidence_score) AS avg_ai_recommendation_confidence,
    COUNT(DISTINCT rule_id) AS ai_rules_flagged_count,

    COUNTIF(UPPER(status) IN ('ACCEPTED', 'IMPLEMENTED', 'REJECTED')) AS steward_actions_taken,
    COUNTIF(UPPER(status) = 'IMPLEMENTED') AS automated_fixes_applied
  FROM `api-project-503305938314.ai_data_steward_mvp.DQ_AI_RECOMMENDATIONS`
  GROUP BY metric_date, domain
),

calendar_domains AS (
  SELECT
    CURRENT_DATE() AS metric_date,
    domain
  FROM domains
)

SELECT
  cd.metric_date,
  cd.domain,

  COALESCE(rs.total_records, 0) AS total_records,
  COALESCE(rs.avg_record_score, 100) AS avg_record_score,
  COALESCE(rs.records_below_threshold, 0) AS records_below_threshold,
  COALESCE(rs.records_below_threshold_rate, 0) AS records_below_threshold_rate,

  ROUND(COALESCE(rs.avg_record_score, 100), 2) AS dq_health_score,
  ROUND(100 - COALESCE(rs.avg_record_score, 100), 2) AS dq_risk_score,
  ROUND(COALESCE(rs.avg_record_score, 100) * 0.85, 2) AS automation_readiness_score,

  COALESCE(rs.avg_completeness_score, 1) AS avg_completeness_score,
  COALESCE(rs.avg_validity_score, 1) AS avg_validity_score,
  COALESCE(rs.avg_standardization_score, 1) AS avg_standardization_score,
  COALESCE(rs.avg_consistency_score, 1) AS avg_consistency_score,
  COALESCE(rs.avg_uniqueness_score, 1) AS avg_uniqueness_score,

  COALESCE(f.total_findings, 0) AS total_findings,
  COALESCE(f.critical_findings, 0) AS critical_findings,
  COALESCE(f.high_findings, 0) AS high_findings,
  COALESCE(f.medium_findings, 0) AS medium_findings,
  COALESCE(f.low_findings, 0) AS low_findings,

  COALESCE(f.open_findings_count, 0) AS open_findings_count,
  COALESCE(f.accepted_findings_count, 0) AS accepted_findings_count,
  COALESCE(f.resolved_findings_count, 0) AS resolved_findings_count,
  COALESCE(f.waived_findings_count, 0) AS waived_findings_count,

  COALESCE(f.records_with_findings, 0) AS records_with_findings,
  COALESCE(f.failed_rule_count, 0) AS failed_rule_count,
  COALESCE(f.duplicate_record_count, 0) AS duplicate_record_count,

  COALESCE(f.scored_critical_issue_count, 0) AS scored_critical_issue_count,
  COALESCE(f.scored_high_issue_count, 0) AS scored_high_issue_count,
  COALESCE(f.scored_medium_issue_count, 0) AS scored_medium_issue_count,
  COALESCE(f.scored_low_issue_count, 0) AS scored_low_issue_count,
  COALESCE(f.scored_total_issue_count, 0) AS scored_total_issue_count,

  COALESCE(ar.records_flagged_by_ai, 0) AS records_flagged_by_ai,
  COALESCE(ar.ai_recommendations_generated, 0) AS ai_recommendations_generated,
  COALESCE(ar.open_ai_recommendations, 0) AS open_ai_recommendations,
  COALESCE(ar.accepted_ai_recommendations, 0) AS accepted_ai_recommendations,
  COALESCE(ar.rejected_ai_recommendations, 0) AS rejected_ai_recommendations,
  COALESCE(ar.implemented_ai_recommendations, 0) AS implemented_ai_recommendations,

  COALESCE(ar.high_priority_recommendations, 0) AS high_priority_recommendations,
  COALESCE(ar.medium_priority_recommendations, 0) AS medium_priority_recommendations,
  COALESCE(ar.low_priority_recommendations, 0) AS low_priority_recommendations,
  COALESCE(ar.avg_ai_recommendation_confidence, 0) AS avg_ai_recommendation_confidence,

  COALESCE(ar.ai_rules_flagged_count, 0) AS ai_rules_flagged_count,
  COALESCE(ar.steward_actions_taken, 0) AS steward_actions_taken,
  COALESCE(ar.automated_fixes_applied, 0) AS automated_fixes_applied,

  COALESCE(rs.scored_record_count, 0) AS scored_record_count,
  COALESCE(rs.min_record_score, 0) AS min_record_score,
  COALESCE(rs.max_record_score, 0) AS max_record_score,
  COALESCE(rs.very_low_score_count, 0) AS very_low_score_count,
  COALESCE(rs.low_score_count, 0) AS low_score_count,
  COALESCE(rs.medium_score_count, 0) AS medium_score_count,
  COALESCE(rs.high_score_count, 0) AS high_score_count,

  COALESCE(f.total_rules_executed, 0) AS total_rules_executed,
  COALESCE(f.rules_triggered, 0) AS rules_triggered,
  COALESCE(f.configured_rule_count, 0) AS configured_rule_count,
  COALESCE(f.active_rule_count, 0) AS active_rule_count,
  COALESCE(f.inactive_rule_count, 0) AS inactive_rule_count,

  COALESCE(f.configured_critical_rules, 0) AS configured_critical_rules,
  COALESCE(f.configured_high_rules, 0) AS configured_high_rules,
  COALESCE(f.configured_medium_rules, 0) AS configured_medium_rules,
  COALESCE(f.configured_low_rules, 0) AS configured_low_rules,

  COALESCE(f.validity_rule_count, 0) AS validity_rule_count,
  COALESCE(f.completeness_rule_count, 0) AS completeness_rule_count,
  COALESCE(f.standardization_rule_count, 0) AS standardization_rule_count,
  COALESCE(f.uniqueness_rule_count, 0) AS uniqueness_rule_count,
  COALESCE(f.consistency_rule_count, 0) AS consistency_rule_count,

  COALESCE(f.avg_rule_weight, 0) AS avg_rule_weight,

  SAFE_DIVIDE(COALESCE(f.total_findings, 0), NULLIF(COALESCE(rs.total_records, 0), 0)) AS findings_per_record_rate,
  SAFE_DIVIDE(COALESCE(f.duplicate_record_count, 0), NULLIF(COALESCE(rs.total_records, 0), 0)) AS duplicate_rate,
  SAFE_DIVIDE(COALESCE(ar.records_flagged_by_ai, 0), NULLIF(COALESCE(rs.total_records, 0), 0)) AS ai_flagged_record_rate,
  SAFE_DIVIDE(COALESCE(ar.automated_fixes_applied, 0), NULLIF(COALESCE(ar.ai_recommendations_generated, 0), 0)) AS automated_fix_rate,
  SAFE_DIVIDE(COALESCE(ar.steward_actions_taken, 0), NULLIF(COALESCE(ar.ai_recommendations_generated, 0), 0)) AS steward_action_rate,
  SAFE_DIVIDE(COALESCE(f.resolved_findings_count, 0), NULLIF(COALESCE(f.total_findings, 0), 0)) AS findings_resolution_rate,
  SAFE_DIVIDE(COALESCE(ar.accepted_ai_recommendations, 0), NULLIF(COALESCE(ar.ai_recommendations_generated, 0), 0)) AS ai_recommendation_acceptance_rate,
  SAFE_DIVIDE(COALESCE(ar.implemented_ai_recommendations, 0), NULLIF(COALESCE(ar.ai_recommendations_generated, 0), 0)) AS ai_recommendation_implementation_rate,
  SAFE_DIVIDE(COALESCE(f.records_with_findings, 0), NULLIF(COALESCE(rs.total_records, 0), 0)) AS impacted_record_rate,

  CURRENT_TIMESTAMP() AS summary_created_at

FROM calendar_domains cd
LEFT JOIN record_scores rs
  ON rs.metric_date = cd.metric_date
 AND rs.domain = cd.domain
LEFT JOIN findings f
  ON f.metric_date = cd.metric_date
 AND f.domain = cd.domain
LEFT JOIN ai_recs ar
  ON ar.metric_date = cd.metric_date
 AND ar.domain = cd.domain;
