CREATE OR REPLACE VIEW `api-project-503305938314.ai_data_steward_mvp.V_GOVERNANCE_POLICY_ACTIVITY`
AS
SELECT
  pcl.change_id,
  pcl.domain,
  pcl.policy_version,
  pc.policy_name,
  pcl.change_type,
  pcl.changed_object,
  pcl.requested_by AS changed_by,
  pcl.changed_at,
  pcl.change_status AS publish_status
FROM `api-project-503305938314.ai_data_steward_mvp.Policy_Change_Log` AS pcl
LEFT JOIN `api-project-503305938314.ai_data_steward_mvp.Policy_Config` AS pc
  ON pcl.domain = pc.domain AND pcl.policy_version = pc.policy_version;
