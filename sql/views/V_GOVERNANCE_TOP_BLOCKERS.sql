CREATE OR REPLACE VIEW `api-project-503305938314.ai_data_steward_mvp.<VIEW_NAME>` AS
WITH readiness_blockers AS (
  SELECT
    certification_blocker_reason AS blocker_reason,
    COUNT(*) AS blocker_count
  FROM `api-project-503305938314.ai_data_steward_mvp.V_CERTIFICATION_READINESS`
  WHERE COALESCE(ready_for_certification, FALSE) = FALSE
    AND certification_blocker_reason IS NOT NULL
    AND TRIM(certification_blocker_reason) != ''
  GROUP BY certification_blocker_reason
),

failed_check_categories AS (
  SELECT
    CONCAT('FAILED CHECK: ', check_category) AS blocker_reason,
    COUNT(*) AS blocker_count
  FROM `api-project-503305938314.ai_data_steward_mvp.DG_CERTIFICATION_CHECK_RESULTS`
  WHERE UPPER(result_status) = 'FAIL'
  GROUP BY check_category
)

SELECT
  blocker_reason,
  SUM(blocker_count) AS blocker_count
FROM (
  SELECT * FROM readiness_blockers
  UNION ALL
  SELECT * FROM failed_check_categories
)
GROUP BY blocker_reason
ORDER BY blocker_count DESC
