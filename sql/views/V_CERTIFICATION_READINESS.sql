CREATE OR REPLACE VIEW `api-project-503305938314.ai_data_steward_mvp.V_CERTIFICATION_READINESS` AS
WITH latest_dq AS (
  SELECT
    domain,
    metric_date,
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
    created_at,
    ROW_NUMBER() OVER (
      PARTITION BY UPPER(domain)
      ORDER BY metric_date DESC, created_at DESC
    ) AS rn
  FROM `ai_data_steward_mvp.DQ_DAILY_SUMMARY`
),
dq AS (
  SELECT
    UPPER(domain) AS domain,
    metric_date AS dq_metric_date,
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
    automation_readiness_score
  FROM latest_dq
  WHERE rn = 1
),
fair AS (
  SELECT
    dataset_id,
    dataset_name,
    UPPER(domain) AS domain,
    subdomain,
    lifecycle_stage,
    data_owner,
    steward_name,
    pod_owner,
    engineering_owner,
    analytics_owner,
    source_system,
    source_table,
    target_dataset,
    target_table,
    business_definition,
    use_case_context,
    data_classification,
    cdl_status,
    cdm_status,
    metadata_complete,
    dq_score,
    dq_metric_name,
    dq_threshold,
    integrity_check,
    pass_fail_status,
    certification_status,
    certification_tier,
    certification_gate,
    certification_reason,
    certified_for_use,
    lineage_complete,
    provenance_complete,
    evidence_link,
    glossary_link,
    lineage_link,
    approval_artifact_link,
    version_number,
    policy_version,
    change_ticket_id,
    fair_findable_score,
    fair_accessible_score,
    fair_interoperable_score,
    fair_reusable_score,
    fair_overall_score,
    fair_maturity_band,
    last_validated_at,
    certification_effective_at,
    certification_expires_at,
    recertification_due_at,
    created_at,
    created_by,
    updated_at,
    updated_by
  FROM `ai_data_steward_mvp.DG_FAIR_SCORE`
)
SELECT
  fair.dataset_id,
  fair.dataset_name,
  fair.domain,
  fair.subdomain,
  fair.lifecycle_stage,

  fair.data_owner,
  fair.steward_name,
  fair.pod_owner,
  fair.engineering_owner,
  fair.analytics_owner,

  fair.source_system,
  fair.source_table,
  fair.target_dataset,
  fair.target_table,

  fair.business_definition,
  fair.use_case_context,
  fair.data_classification,
  fair.cdl_status,
  fair.cdm_status,
  fair.metadata_complete,

  fair.dq_score AS registry_dq_score,
  fair.dq_metric_name,
  fair.dq_threshold,
  fair.integrity_check,
  fair.pass_fail_status,

  fair.certification_status,
  fair.certification_tier,
  fair.certification_gate,
  fair.certification_reason,
  fair.certified_for_use,

  fair.lineage_complete,
  fair.provenance_complete,

  fair.evidence_link,
  fair.glossary_link,
  fair.lineage_link,
  fair.approval_artifact_link,

  fair.version_number,
  fair.policy_version,
  fair.change_ticket_id,

  fair.fair_findable_score,
  fair.fair_accessible_score,
  fair.fair_interoperable_score,
  fair.fair_reusable_score,
  fair.fair_overall_score,
  fair.fair_maturity_band,

  dq.dq_metric_date,
  dq.total_records,
  dq.avg_record_score,
  dq.records_below_threshold,
  dq.total_findings,
  dq.critical_findings,
  dq.high_findings,
  dq.medium_findings,
  dq.low_findings,
  dq.total_rules_executed,
  dq.failed_rule_count,
  dq.duplicate_record_count,
  dq.records_flagged_by_ai,
  dq.ai_recommendations_generated,
  dq.steward_actions_taken,
  dq.automated_fixes_applied,
  dq.dq_health_score,
  dq.dq_risk_score,
  dq.automation_readiness_score,

  CASE
    WHEN fair.metadata_complete = TRUE
      AND fair.lineage_complete = TRUE
      AND fair.provenance_complete = TRUE
      AND COALESCE(fair.dq_score, 0) >= COALESCE(fair.dq_threshold, 95)
      AND COALESCE(fair.fair_overall_score, 0) >= 75
      AND UPPER(COALESCE(fair.certification_status, '')) IN ('NOT_STARTED', 'IN_REVIEW', 'RECERT_REQUIRED')
    THEN TRUE
    ELSE FALSE
  END AS ready_for_certification,

  CASE
    WHEN fair.metadata_complete IS NOT TRUE THEN 'METADATA_INCOMPLETE'
    WHEN fair.lineage_complete IS NOT TRUE THEN 'LINEAGE_INCOMPLETE'
    WHEN fair.provenance_complete IS NOT TRUE THEN 'PROVENANCE_INCOMPLETE'
    WHEN COALESCE(fair.dq_score, 0) < COALESCE(fair.dq_threshold, 95) THEN 'DQ_BELOW_THRESHOLD'
    WHEN COALESCE(fair.fair_overall_score, 0) < 75 THEN 'FAIR_SCORE_TOO_LOW'
    WHEN UPPER(COALESCE(fair.certification_status, '')) = 'CERTIFIED' THEN 'ALREADY_CERTIFIED'
    WHEN UPPER(COALESCE(fair.certification_status, '')) = 'REJECTED' THEN 'CERTIFICATION_REJECTED'
    WHEN UPPER(COALESCE(fair.certification_status, '')) = 'EXPIRED' THEN 'CERTIFICATION_EXPIRED'
    ELSE 'READY'
  END AS certification_blocker_reason,

  CASE
    WHEN fair.metadata_complete = TRUE
      AND fair.lineage_complete = TRUE
      AND fair.provenance_complete = TRUE
      AND COALESCE(fair.dq_score, 0) >= COALESCE(fair.dq_threshold, 95)
      AND COALESCE(fair.fair_overall_score, 0) >= 90
    THEN 'HIGH'
    WHEN fair.metadata_complete = TRUE
      AND fair.lineage_complete = TRUE
      AND fair.provenance_complete = TRUE
      AND COALESCE(fair.dq_score, 0) >= COALESCE(fair.dq_threshold, 95)
      AND COALESCE(fair.fair_overall_score, 0) >= 75
    THEN 'MEDIUM'
    ELSE 'LOW'
  END AS certification_readiness_band,

  ROUND(
    (
      IF(fair.metadata_complete = TRUE, 20, 0) +
      IF(fair.lineage_complete = TRUE, 20, 0) +
      IF(fair.provenance_complete = TRUE, 20, 0) +
      IF(COALESCE(fair.dq_score, 0) >= COALESCE(fair.dq_threshold, 95), 20, 0) +
      IF(COALESCE(fair.fair_overall_score, 0) >= 75, 20, 0)
    ),
    1
  ) AS certification_readiness_score,

  fair.last_validated_at,
  fair.certification_effective_at,
  fair.certification_expires_at,
  fair.recertification_due_at,
  fair.created_at,
  fair.created_by,
  fair.updated_at,
  fair.updated_by
FROM fair
LEFT JOIN dq
  ON fair.domain = dq.domain
