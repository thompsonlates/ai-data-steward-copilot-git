-- AI Data Steward Copilot
-- Seed active v1 policies and thresholds

INSERT INTO
  `api-project-503305938314.ai_data_steward_mvp.Policy_Config`
(
  policy_id,
  domain,
  policy_version,
  policy_name,
  policy_description,
  active_flag,
  effective_from,
  effective_to,
  default_decision_mode,
  review_required_flag,
  allow_auto_merge_flag
)
SELECT *
FROM UNNEST([
  STRUCT(
    'POL-CUSTOMER-V1',
    'CUSTOMER',
    'v1',
    'Customer Identity Policy',
    'Identity resolution governance policy for customer records.',
    'Y',
    CURRENT_TIMESTAMP(),
    CAST(NULL AS TIMESTAMP),
    'REVIEW_REQUIRED',
    'Y',
    'Y'
  ),
  STRUCT(
    'POL-PATIENT-V1',
    'PATIENT',
    'v1',
    'Patient Identity Policy',
    'Patient identity resolution and patient safety governance policy.',
    'Y',
    CURRENT_TIMESTAMP(),
    CAST(NULL AS TIMESTAMP),
    'REVIEW_REQUIRED',
    'Y',
    'Y'
  ),
  STRUCT(
    'POL-PROVIDER-V1',
    'PROVIDER',
    'v1',
    'Provider Identity Policy',
    'Provider identity resolution governance policy.',
    'Y',
    CURRENT_TIMESTAMP(),
    CAST(NULL AS TIMESTAMP),
    'REVIEW_REQUIRED',
    'Y',
    'Y'
  ),
  STRUCT(
    'POL-SUPPLIER-V1',
    'SUPPLIER',
    'v1',
    'Supplier Identity Policy',
    'Supplier identity resolution governance policy.',
    'Y',
    CURRENT_TIMESTAMP(),
    CAST(NULL AS TIMESTAMP),
    'REVIEW_REQUIRED',
    'Y',
    'Y'
  ),
  STRUCT(
    'POL-PRODUCT-V1',
    'PRODUCT',
    'v1',
    'Product Identity Policy',
    'Product identity resolution governance policy.',
    'Y',
    CURRENT_TIMESTAMP(),
    CAST(NULL AS TIMESTAMP),
    'REVIEW_REQUIRED',
    'Y',
    'Y'
  )
]);

INSERT INTO
  `api-project-503305938314.ai_data_steward_mvp.Policy_Thresholds`
(
  threshold_id,
  policy_id,
  domain,
  policy_version,
  min_review_score,
  min_approve_merge_score,
  min_auto_merge_score,
  max_auto_merge_override_rate,
  max_review_override_rate,
  high_risk_score_cutoff,
  medium_risk_score_cutoff,
  require_exact_dob_flag,
  require_email_or_address_flag,
  require_manual_review_on_conflict_flag,
  active_flag,
  effective_from,
  effective_to
)
SELECT *
FROM UNNEST([
  STRUCT(
    'THR-CUSTOMER-V1',
    'POL-CUSTOMER-V1',
    'CUSTOMER',
    'v1',
    0.70,
    0.90,
    0.97,
    0.10,
    0.30,
    70.0,
    35.0,
    'N',
    'Y',
    'Y',
    'Y',
    CURRENT_TIMESTAMP(),
    CAST(NULL AS TIMESTAMP)
  ),
  STRUCT(
    'THR-PATIENT-V1',
    'POL-PATIENT-V1',
    'PATIENT',
    'v1',
    0.70,
    0.90,
    0.97,
    0.10,
    0.30,
    70.0,
    35.0,
    'Y',
    'Y',
    'Y',
    'Y',
    CURRENT_TIMESTAMP(),
    CAST(NULL AS TIMESTAMP)
  ),
  STRUCT(
    'THR-PROVIDER-V1',
    'POL-PROVIDER-V1',
    'PROVIDER',
    'v1',
    0.70,
    0.90,
    0.97,
    0.10,
    0.30,
    70.0,
    35.0,
    'N',
    'Y',
    'Y',
    'Y',
    CURRENT_TIMESTAMP(),
    CAST(NULL AS TIMESTAMP)
  ),
  STRUCT(
    'THR-SUPPLIER-V1',
    'POL-SUPPLIER-V1',
    'SUPPLIER',
    'v1',
    0.70,
    0.90,
    0.97,
    0.10,
    0.30,
    70.0,
    35.0,
    'N',
    'Y',
    'Y',
    'Y',
    CURRENT_TIMESTAMP(),
    CAST(NULL AS TIMESTAMP)
  ),
  STRUCT(
    'THR-PRODUCT-V1',
    'POL-PRODUCT-V1',
    'PRODUCT',
    'v1',
    0.70,
    0.90,
    0.97,
    0.10,
    0.30,
    70.0,
    35.0,
    'N',
    'Y',
    'Y',
    'Y',
    CURRENT_TIMESTAMP(),
    CAST(NULL AS TIMESTAMP)
  )
]);
