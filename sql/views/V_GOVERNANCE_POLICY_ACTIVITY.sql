CREATE OR REPLACE VIEW `api-project-503305938314.ai_data_steward_mvp.<VIEW_NAME>` AS
SELECT
  pcl.change_id,
  pcl.domain,
  pcl.policy_version,
  pc.policy_name,
  pcl.change_type,
  pcl.changed_object,
  pcl.changed_by,
  pcl.changed_at,
  pcl.publish_status
FROM `api-project-503305938314.ai_data_steward_mvp.Policy_Change_Log` pcl
LEFT JOIN `api-project-503305938314.ai_data_steward_mvp.Policy_Config` pc
  ON pcl.domain = pc.domain
 AND pcl.policy_version = pc.policy_version
