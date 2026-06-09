CREATE TABLE IF NOT EXISTS `api-project-503305938314.ai_data_steward_mvp.DQ_RECORD_SCORE` (
  created_at TIMESTAMP,
  domain STRING,
  record_id STRING,
  record_score FLOAT64,
  completeness_score FLOAT64,
  validity_score FLOAT64,
  standardization_score FLOAT64,
  consistency_score FLOAT64,
  uniqueness_score FLOAT64
);
