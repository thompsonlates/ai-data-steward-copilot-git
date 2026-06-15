CREATE OR REPLACE VIEW `api-project-503305938314.ai_data_steward_mvp.V_GOVERNANCE_TOP_BLOCKERS` AS
WITH blockers AS (
  SELECT
    CASE
      WHEN failed_checks > 0 THEN 'FAILED_CERTIFICATION_CHECKS'
      WHEN critical_findings > 0 THEN 'CRITICAL_FINDINGS'
      WHEN high_findings > 0 THEN 'HIGH_FINDINGS'
      WHEN certification_readiness_score < 90 THEN 'READINESS_BELOW_THRESHOLD'
      WHEN fair_overall_score < 80 THEN 'FAIR_SCORE_BELOW_THRESHOLD'
      ELSE 'NO_BLOCKER'
    END AS blocker_reason,
    CASE
      WHEN critical_findings > 0 THEN 'CRITICAL'
      WHEN failed_checks > 0 OR high_findings > 0 THEN 'HIGH'
      WHEN certification_readiness_score < 90 THEN 'MEDIUM'
      WHEN fair_overall_score < 80 THEN 'LOW'
      ELSE 'NONE'
    END AS blocker_severity
  FROM `api-project-503305938314.ai_data_steward_mvp.V_GOVERNANCE_DATASET_DETAIL`
  WHERE
    failed_checks > 0
    OR critical_findings > 0
    OR high_findings > 0
    OR certification_readiness_score < 90
    OR fair_overall_score < 80
)
SELECT
  blocker_reason,
  blocker_severity,
  COUNT(*) AS blocker_count
FROM blockers
GROUP BY blocker_reason, blocker_severity;
