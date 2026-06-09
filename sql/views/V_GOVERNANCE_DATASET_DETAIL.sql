CREATE OR REPLACE VIEW `api-project-503305938314.ai_data_steward_mvp.<VIEW_NAME>` AS
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

failed_checks AS (
  SELECT
    dataset_id,
    dataset_name,
    COUNTIF(UPPER(result_status) = 'FAIL') AS failed_checks
  FROM `api-project-503305938314.ai_data_steward_mvp.DG_CERTIFICATION_CHECK_RESULTS`
  GROUP BY dataset_id, dataset_name
)

SELECT
  r.dataset_id,
  r.dataset_name,
  r.domain,
  r.subdomain,
  r.data_owner,
  r.steward_name AS registry_steward_name,
  r.pod_owner AS registry_pod_owner,
  COALESCE(lc.certification_status, r.certification_status) AS certification_status,
  COALESCE(lc.certification_tier, r.certification_tier) AS certification_tier,
  COALESCE(lc.lifecycle_stage, r.lifecycle_stage) AS lifecycle_stage,
  COALESCE(lc.pass_fail_status, r.pass_fail_status) AS pass_fail_status,
  COALESCE(lc.certified_for_use, r.certified_for_use) AS certified_for_use,
  COALESCE(lc.lineage_complete, r.lineage_complete) AS lineage_complete,
  COALESCE(lc.provenance_complete, r.provenance_complete) AS provenance_complete,
  vr.ready_for_certification,
  vr.certification_blocker_reason,
  vr.certification_readiness_band,
  vr.certification_readiness_score,
  vr.dq_health_score,
  vr.dq_risk_score,
  vr.automation_readiness_score,
  vr.total_findings,
  vr.critical_findings,
  vr.high_findings,
  vr.medium_findings,
  vr.low_findings,
  vr.failed_rule_count,
  COALESCE(fc.failed_checks, 0) AS failed_checks,
  fs.fair_overall_score,
  fs.fair_maturity_band,
  lc.approved_by,
  lc.steward_name AS certification_steward_name,
  lc.pod_owner AS certification_pod_owner,
  lc.event_timestamp,
  r.last_validated_at,
  r.certification_effective_at,
  r.certification_expires_at,
  r.recertification_due_at
FROM `api-project-503305938314.ai_data_steward_mvp.DG_DATASET_REGISTRY` r
LEFT JOIN `api-project-503305938314.ai_data_steward_mvp.V_CERTIFICATION_READINESS` vr
  ON r.dataset_id = vr.dataset_id
LEFT JOIN `api-project-503305938314.ai_data_steward_mvp.DG_FAIR_SCORE` fs
  ON r.dataset_id = fs.dataset_id
LEFT JOIN latest_certification lc
  ON r.dataset_id = lc.dataset_id
 AND lc.rn = 1
LEFT JOIN failed_checks fc
  ON r.dataset_id = fc.dataset_id
