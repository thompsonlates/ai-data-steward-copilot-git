-- AI Data Steward Copilot
-- BigQuery DDL: Policy_Thresholds

CREATE TABLE IF NOT EXISTS
  `api-project-503305938314.ai_data_steward_mvp.Policy_Thresholds`
(
  threshold_id STRING NOT NULL,
  policy_id STRING NOT NULL,
  domain STRING NOT NULL,
  policy_version STRING NOT NULL,

  min_review_score FLOAT64,
  min_approve_merge_score FLOAT64,
  min_auto_merge_score FLOAT64,

  max_auto_merge_override_rate FLOAT64,
  max_review_override_rate FLOAT64,

  high_risk_score_cutoff FLOAT64,
  medium_risk_score_cutoff FLOAT64,

  require_exact_dob_flag STRING,
  require_email_or_address_flag STRING,
  require_manual_review_on_conflict_flag STRING,

  active_flag STRING NOT NULL,
  effective_from TIMESTAMP,
  effective_to TIMESTAMP
)
CLUSTER BY domain, policy_version
OPTIONS (
  description = 'Policy decision thresholds and governance constraints by domain and policy version.'
);
