-- AI Data Steward Copilot
-- BigQuery DDL: Policy_Config

CREATE TABLE IF NOT EXISTS
  `api-project-503305938314.ai_data_steward_mvp.Policy_Config`
(
  policy_id STRING NOT NULL,
  domain STRING NOT NULL,
  policy_version STRING NOT NULL,
  policy_name STRING,
  policy_description STRING,
  active_flag STRING NOT NULL,
  effective_from TIMESTAMP,
  effective_to TIMESTAMP,
  default_decision_mode STRING,
  review_required_flag STRING,
  allow_auto_merge_flag STRING
)
CLUSTER BY domain, policy_version
OPTIONS (
  description = 'Master policy configuration by domain and policy version.'
);
