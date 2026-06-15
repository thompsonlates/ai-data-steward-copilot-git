CREATE OR REPLACE VIEW `api-project-503305938314.ai_data_steward_mvp.V_GOVERNANCE_DATASET_DETAIL` AS
WITH latest_certification AS (
  SELECT
    dataset_id,
    dataset_name,
    new_lifecycle_stage AS lifecycle_stage,
    new_certification_status AS certification_status,
    new_certification_tier AS certification_tier,
    new_dq_score AS dq_score,
    new_pass_fail_status AS pass_fail_status,
    lineage_complete,
    provenance_complete,
    certified_for_use,
    approved_by,
    steward_name,
    pod_owner,
    event_timestamp,
    ROW_NUMBER() OVER (
      PARTITION BY dataset_id
      ORDER BY event_timestamp DESC
    ) AS rn
  FROM `api-project-503305938314.ai_data_steward_mvp.DG_CERTIFICATION_HISTORY`
),
check_summary AS (
  SELECT
    dataset_id,
    COUNT(*) AS total_checks,
    COUNTIF(UPPER(status) = 'PASS') AS passed_checks,
    COUNTIF(UPPER(status) = 'FAIL') AS failed_checks,
    COUNTIF(UPPER(severity) = 'CRITICAL') AS critical_findings,
    COUNTIF(UPPER(severity) = 'HIGH') AS high_findings,
    MAX(checked_at) AS last_checked_at
  FROM `api-project-503305938314.ai_data_steward_mvp.DG_CERTIFICATION_CHECK_RESULTS`
  GROUP BY dataset_id
)
SELECT
  r.dataset_id,
  r.dataset_name,
  r.domain,
  r.owner AS data_owner,
  r.steward AS steward_name,
  r.source_system,
  COALESCE(lc.lifecycle_stage, 'PRODUCTION') AS lifecycle_stage,
  COALESCE(lc.certification_status, r.certification_status) AS certification_status,
  COALESCE(lc.certification_tier, 'ENTERPRISE_READY') AS certification_tier,
  COALESCE(lc.dq_score, r.certification_score) AS dq_score,
  COALESCE(lc.pass_fail_status, 'PASS') AS pass_fail_status,
  COALESCE(lc.lineage_complete, TRUE) AS lineage_complete,
  COALESCE(lc.provenance_complete, TRUE) AS provenance_complete,
  COALESCE(lc.certified_for_use, TRUE) AS certified_for_use,
  COALESCE(lc.approved_by, 'governance_admin') AS approved_by,
  COALESCE(lc.pod_owner, 'Governance') AS pod_owner,
  lc.event_timestamp AS last_certification_event_at,
  COALESCE(cs.total_checks, 0) AS total_checks,
  COALESCE(cs.passed_checks, 0) AS passed_checks,
  COALESCE(cs.failed_checks, 0) AS failed_checks,
  COALESCE(fs.overall_fair_score, 0) AS fair_overall_score,
  ROUND(
    SAFE_DIVIDE(
      COALESCE(cs.passed_checks, 0),
      NULLIF(COALESCE(cs.total_checks, 0), 0)
    ) * 100,
    2
  ) AS certification_readiness_score,
  COALESCE(cs.critical_findings, 0) AS critical_findings,
  COALESCE(cs.high_findings, 0) AS high_findings,
  cs.last_checked_at
FROM `api-project-503305938314.ai_data_steward_mvp.DG_DATASET_REGISTRY` r
LEFT JOIN latest_certification lc
  ON r.dataset_id = lc.dataset_id
 AND lc.rn = 1
LEFT JOIN check_summary cs
  ON r.dataset_id = cs.dataset_id
LEFT JOIN `api-project-503305938314.ai_data_steward_mvp.DG_FAIR_SCORE` fs
  ON r.dataset_id = fs.dataset_id;

