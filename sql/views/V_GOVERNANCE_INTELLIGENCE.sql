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

dataset_base AS (
  SELECT
    r.dataset_id,
    r.dataset_name,
    r.domain,
    r.subdomain,
    r.lifecycle_stage AS registry_lifecycle_stage,
    r.data_owner,
    r.steward_name AS registry_steward_name,
    r.pod_owner AS registry_pod_owner,
    r.certification_status AS registry_certification_status,
    r.certification_tier AS registry_certification_tier,
    r.pass_fail_status AS registry_pass_fail_status,
    r.certified_for_use AS registry_certified_for_use,
    r.lineage_complete AS registry_lineage_complete,
    r.provenance_complete AS registry_provenance_complete,
    r.fair_overall_score AS registry_fair_overall_score,
    vr.ready_for_certification,
    vr.certification_blocker_reason,
    vr.certification_readiness_band,
    vr.certification_readiness_score,
    vr.failed_rule_count,
    vr.critical_findings,
    vr.high_findings,
    vr.medium_findings,
    vr.low_findings,
    vr.dq_health_score,
    vr.dq_risk_score,
    vr.automation_readiness_score,
    fs.fair_overall_score AS fair_overall_score,
    fs.fair_maturity_band,
    lc.lifecycle_stage,
    lc.certification_status,
    lc.certification_tier,
    lc.dq_score,
    lc.pass_fail_status,
    lc.lineage_complete,
    lc.provenance_complete,
    lc.certified_for_use,
    lc.approved_by,
    lc.steward_name,
    lc.pod_owner,
    lc.event_timestamp
  FROM `api-project-503305938314.ai_data_steward_mvp.DG_DATASET_REGISTRY` r
  LEFT JOIN `api-project-503305938314.ai_data_steward_mvp.V_CERTIFICATION_READINESS` vr
    ON r.dataset_id = vr.dataset_id
  LEFT JOIN `api-project-503305938314.ai_data_steward_mvp.DG_FAIR_SCORE` fs
    ON r.dataset_id = fs.dataset_id
  LEFT JOIN latest_certification lc
    ON r.dataset_id = lc.dataset_id
   AND lc.rn = 1
),

check_summary AS (
  SELECT
    COUNT(*) AS total_checks,
    COUNTIF(UPPER(result_status) = 'PASS') AS passed_checks,
    COUNTIF(UPPER(result_status) = 'FAIL') AS failed_checks
  FROM `api-project-503305938314.ai_data_steward_mvp.DG_CERTIFICATION_CHECK_RESULTS`
),

policy_summary AS (
  SELECT
    COUNT(*) AS total_policies,
    COUNTIF(UPPER(active_flag) = 'TRUE') AS active_policies
  FROM `api-project-503305938314.ai_data_steward_mvp.Policy_Config`
),

policy_changes_30d AS (
  SELECT
    COUNT(*) AS recent_policy_changes_30d
  FROM `api-project-503305938314.ai_data_steward_mvp.Policy_Change_Log`
  WHERE DATE(changed_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
)

SELECT
  COUNT(*) AS total_datasets,
  COUNTIF(UPPER(COALESCE(certification_status, registry_certification_status, '')) = 'CERTIFIED') AS certified_datasets,
  COUNTIF(COALESCE(ready_for_certification, FALSE) = TRUE) AS ready_for_certification,
  COUNTIF(
    UPPER(COALESCE(certification_status, registry_certification_status, '')) IN ('IN_PROGRESS', 'PENDING')
  ) AS in_progress_certifications,
  ROUND(AVG(COALESCE(fair_overall_score, registry_fair_overall_score, 0)), 2) AS avg_fair_score,
  COUNTIF(
    COALESCE(ready_for_certification, FALSE) = FALSE
    AND certification_blocker_reason IS NOT NULL
    AND TRIM(certification_blocker_reason) != ''
  ) AS open_governance_issues,
  MAX(cs.total_checks) AS total_checks,
  MAX(cs.passed_checks) AS passed_checks,
  MAX(cs.failed_checks) AS failed_checks,
  ROUND(
    SAFE_DIVIDE(MAX(cs.passed_checks), NULLIF(MAX(cs.total_checks), 0)) * 100,
    2
  ) AS check_pass_rate,
  MAX(ps.active_policies) AS active_policies,
  MAX(pc.recent_policy_changes_30d) AS recent_policy_changes_30d
FROM dataset_base
CROSS JOIN check_summary cs
CROSS JOIN policy_summary ps
CROSS JOIN policy_changes_30d pc
