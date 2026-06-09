CREATE OR REPLACE VIEW `api-project-503305938314.ai_data_steward_mvp.<VIEW_NAME>` AS
WITH base AS (
  SELECT
    dataset_id,
    dataset_name,
    domain,
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
    last_validated_at,
    certification_effective_at,
    certification_expires_at,
    recertification_due_at,
    created_at,
    created_by,
    updated_at,
    updated_by
  FROM `ai_data_steward_mvp.DG_DATASET_REGISTRY`
),
scored AS (
  SELECT
    *,
    (
      IF(dataset_name IS NOT NULL AND TRIM(dataset_name) != '', 25, 0) +
      IF(business_definition IS NOT NULL AND TRIM(business_definition) != '', 25, 0) +
      IF(glossary_link IS NOT NULL AND TRIM(glossary_link) != '', 25, 0) +
      IF(metadata_complete = TRUE, 25, 0)
    ) AS fair_findable_score,

    (
      IF(certified_for_use = TRUE, 30, 0) +
      IF(data_classification IS NOT NULL AND TRIM(data_classification) != '', 20, 0) +
      IF(data_owner IS NOT NULL AND TRIM(data_owner) != '', 25, 0) +
      IF(steward_name IS NOT NULL AND TRIM(steward_name) != '', 25, 0)
    ) AS fair_accessible_score,

    (
      IF(UPPER(COALESCE(cdl_status, '')) = 'CONFORMANT', 35, IF(UPPER(COALESCE(cdl_status, '')) = 'PARTIAL', 15, 0)) +
      IF(UPPER(COALESCE(cdm_status, '')) = 'CONFORMANT', 35, IF(UPPER(COALESCE(cdm_status, '')) = 'PARTIAL', 15, 0)) +
      IF(source_table IS NOT NULL AND TRIM(source_table) != '', 15, 0) +
      IF(target_table IS NOT NULL AND TRIM(target_table) != '', 15, 0)
    ) AS fair_interoperable_score,

    (
      IF(UPPER(COALESCE(certification_status, '')) = 'CERTIFIED', 25, IF(UPPER(COALESCE(certification_status, '')) = 'IN_REVIEW', 10, 0)) +
      IF(lineage_complete = TRUE, 20, 0) +
      IF(provenance_complete = TRUE, 20, 0) +
      IF(evidence_link IS NOT NULL AND TRIM(evidence_link) != '', 15, 0) +
      IF(use_case_context IS NOT NULL AND TRIM(use_case_context) != '', 10, 0) +
      IF(version_number IS NOT NULL AND TRIM(version_number) != '', 10, 0)
    ) AS fair_reusable_score
  FROM base
)
SELECT
  dataset_id,
  dataset_name,
  domain,
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

  ROUND(
    (fair_findable_score + fair_accessible_score + fair_interoperable_score + fair_reusable_score) / 4,
    1
  ) AS fair_overall_score,

  CASE
    WHEN ROUND(
      (fair_findable_score + fair_accessible_score + fair_interoperable_score + fair_reusable_score) / 4,
      1
    ) >= 90 THEN 'STRONG'
    WHEN ROUND(
      (fair_findable_score + fair_accessible_score + fair_interoperable_score + fair_reusable_score) / 4,
      1
    ) >= 75 THEN 'STABLE'
    WHEN ROUND(
      (fair_findable_score + fair_accessible_score + fair_interoperable_score + fair_reusable_score) / 4,
      1
    ) >= 60 THEN 'MODERATE'
    ELSE 'AT_RISK'
  END AS fair_maturity_band,

  last_validated_at,
  certification_effective_at,
  certification_expires_at,
  recertification_due_at,
  created_at,
  created_by,
  updated_at,
  updated_by
FROM scored
