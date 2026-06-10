
CREATE TABLE IF NOT EXISTS `api-project-503305938314.ai_data_steward_mvp.Policy_Change_Log`
(
  change_id STRING NOT NULL,
  policy_id STRING,
  domain STRING,
  policy_version STRING,

  change_type STRING,
  change_summary STRING,
  change_reason STRING,
  changed_field STRING,
  old_value STRING,
  new_value STRING,

  requested_by STRING,
  approved_by STRING,
  change_status STRING,

  effective_from TIMESTAMP,
  effective_to TIMESTAMP,

  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP(),
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
);
