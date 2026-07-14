-- AI Data Steward Copilot
-- BigQuery DDL: Policy_Risk_Rules

CREATE TABLE IF NOT EXISTS
  `api-project-503305938314.ai_data_steward_mvp.Policy_Risk_Rules`
(
  risk_rule_id STRING NOT NULL,
  policy_id STRING NOT NULL,
  domain STRING NOT NULL,
  policy_version STRING NOT NULL,

  triggered_rule STRING NOT NULL,
  override_reason_code STRING,

  risk_weight FLOAT64,
  risk_level STRING,
  recommended_action STRING,

  steward_learning_enabled_flag STRING,
  active_flag STRING NOT NULL,

  effective_from TIMESTAMP,
  effective_to TIMESTAMP,
  notes STRING
)
CLUSTER BY domain, policy_version, triggered_rule
OPTIONS (
  description = 'Policy-driven risk rules mapped to entity-resolution signals and governance actions.'
);
