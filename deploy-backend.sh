#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "$0")"

PROJECT_ID="api-project-503305938314"
REGION="us-east1"
SERVICE_NAME="ai-data-steward-api"
ENV_FILE="env.yaml"

echo "AI Data Steward API deployment"
echo "Project: ${PROJECT_ID}"
echo "Region:  ${REGION}"
echo "Service: ${SERVICE_NAME}"

for command_name in /usr/bin/gcloud curl git; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "ERROR: Missing command: ${command_name}"
    exit 1
  fi
done

for required_file in Dockerfile requirements.txt "${ENV_FILE}"; do
  if [[ ! -f "${required_file}" ]]; then
    echo "ERROR: Missing required file: ${required_file}"
    exit 1
  fi
done

if git ls-files --error-unmatch "${ENV_FILE}" >/dev/null 2>&1; then
  echo "ERROR: ${ENV_FILE} is tracked by Git."
  echo "Remove it from Git before deploying."
  exit 1
fi

if [[ -x "./venv/Scripts/python.exe" ]]; then
  PYTHON_BIN="./venv/Scripts/python.exe"
elif [[ -x "./.venv/Scripts/python.exe" ]]; then
  PYTHON_BIN="./.venv/Scripts/python.exe"
elif [[ -x "./venv/bin/python" ]]; then
  PYTHON_BIN="./venv/bin/python"
elif [[ -x "./.venv/bin/python" ]]; then
  PYTHON_BIN="./.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
else
  echo "ERROR: No usable Python environment found."
  exit 1
fi

echo
echo "STEP 1/4: Compiling backend Python"

/usr/bin/env "${PYTHON_BIN}" -m compileall -q app

echo "Python compilation passed."

echo
echo "STEP 2/4: Running full test suite"

/usr/bin/env "${PYTHON_BIN}" -m pytest \
  test \
  ./test_*.py \
  --ignore=y \
  --ignore=venv


echo "Full test suite passed."

echo
echo "STEP 3/4: Deploying backend"

/usr/bin/gcloud run deploy "${SERVICE_NAME}" \
  --source . \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --allow-unauthenticated \
  --env-vars-file="${ENV_FILE}" \
  --quiet

echo
echo "STEP 4/4: Verifying deployed revision"

SERVICE_URL=$(
  /usr/bin/gcloud run services describe "${SERVICE_NAME}" \
    --project="${PROJECT_ID}" \
    --region="${REGION}" \
    --format='value(status.url)'
)

LATEST_CREATED=$(
  /usr/bin/gcloud run services describe "${SERVICE_NAME}" \
    --project="${PROJECT_ID}" \
    --region="${REGION}" \
    --format='value(status.latestCreatedRevisionName)'
)

LATEST_READY=$(
  /usr/bin/gcloud run services describe "${SERVICE_NAME}" \
    --project="${PROJECT_ID}" \
    --region="${REGION}" \
    --format='value(status.latestReadyRevisionName)'
)

if [[ -z "${SERVICE_URL}" ]]; then
  echo "ERROR: Cloud Run did not return a service URL."
  exit 1
fi

if [[ "${LATEST_CREATED}" != "${LATEST_READY}" ]]; then
  echo "ERROR: Latest revision is not ready."
  echo "Created: ${LATEST_CREATED}"
  echo "Ready:   ${LATEST_READY}"
  exit 1
fi

/usr/bin/curl -fsS \
  "${SERVICE_URL}/openapi.json" \
  >/dev/null

echo
echo "Deployment succeeded."
echo "Revision: ${LATEST_READY}"
echo "URL:      ${SERVICE_URL}"
echo "OpenAPI:  ${SERVICE_URL}/openapi.json"
