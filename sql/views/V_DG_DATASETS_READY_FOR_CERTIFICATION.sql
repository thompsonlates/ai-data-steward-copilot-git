CREATE OR REPLACE VIEW `api-project-503305938314.ai_data_steward_mvp.<VIEW_NAME>` AS
SELECT
  dataset_id,
  dataset_name,
  domain,
  lifecycle_stage,
  dq_score,
  dq_threshold,
  lineage_complete,
  provenance_complete,
  metadata_complete,
  fair_overall_score,
  pass_fail_status,
  certification_status,
  recertification_due_at
FROM `ai_data_steward_mvp.DG_DATASET_REGISTRY`
WHERE
  lifecycle_stage IN ('CURATION', 'CERTIFICATION')
  AND dq_score >= dq_threshold
  AND lineage_complete = TRUE
  AND provenance_complete = TRUE
  AND metadata_complete = TRUE
  AND certification_status IN ('NOT_STARTED', 'IN_REVIEW', 'RECERT_REQUIRED')
