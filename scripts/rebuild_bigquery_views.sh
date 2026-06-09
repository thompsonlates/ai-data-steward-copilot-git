#!/bin/bash
set -e

bq query --use_legacy_sql=false < sql/views/DQ_INTELLIGENCE_DASHBOARD_VW.sql
bq query --use_legacy_sql=false < sql/views/DG_FAIR_SCORE.sql
bq query --use_legacy_sql=false < sql/views/V_CERTIFICATION_READINESS.sql
bq query --use_legacy_sql=false < sql/views/V_DG_DATASETS_READY_FOR_CERTIFICATION.sql
bq query --use_legacy_sql=false < sql/views/V_GOVERNANCE_DATASET_DETAIL.sql
bq query --use_legacy_sql=false < sql/views/V_GOVERNANCE_INTELLIGENCE.sql
bq query --use_legacy_sql=false < sql/views/V_GOVERNANCE_POLICY_ACTIVITY.sql
bq query --use_legacy_sql=false < sql/views/V_GOVERNANCE_TOP_BLOCKERS.sql

echo "BigQuery views rebuilt successfully."
