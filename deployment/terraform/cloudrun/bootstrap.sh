#!/usr/bin/env bash
set -euo pipefail

readonly SQL_GATE_PROJECT_ID="${1:-gcplab20250706}"
readonly SQL_GATE_REGION="${2:-us-central1}"
readonly SQL_GATE_STATE_BUCKET="${3:-${SQL_GATE_PROJECT_ID}-sql-execution-gate-tf-state}"
readonly SQL_GATE_CLOUD_BUILD_BUCKET="${SQL_GATE_PROJECT_ID}_cloudbuild"
readonly SQL_GATE_TERRAFORM_ACCOUNT_ID="sql-execution-gate-terraform"
readonly SQL_GATE_TERRAFORM_EMAIL="${SQL_GATE_TERRAFORM_ACCOUNT_ID}@${SQL_GATE_PROJECT_ID}.iam.gserviceaccount.com"
readonly SQL_GATE_TERRAFORM_MEMBER="serviceAccount:${SQL_GATE_TERRAFORM_EMAIL}"

gcloud config set project "${SQL_GATE_PROJECT_ID}"
gcloud services enable \
  cloudbuild.googleapis.com \
  iam.googleapis.com \
  serviceusage.googleapis.com \
  storage.googleapis.com \
  --project="${SQL_GATE_PROJECT_ID}"

if ! gcloud storage buckets describe "gs://${SQL_GATE_STATE_BUCKET}" \
  --project="${SQL_GATE_PROJECT_ID}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${SQL_GATE_STATE_BUCKET}" \
    --project="${SQL_GATE_PROJECT_ID}" \
    --location="${SQL_GATE_REGION}" \
    --uniform-bucket-level-access
fi

gcloud storage buckets update "gs://${SQL_GATE_STATE_BUCKET}" \
  --project="${SQL_GATE_PROJECT_ID}" \
  --versioning \
  --uniform-bucket-level-access

if ! gcloud iam service-accounts describe "${SQL_GATE_TERRAFORM_EMAIL}" \
  --project="${SQL_GATE_PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${SQL_GATE_TERRAFORM_ACCOUNT_ID}" \
    --project="${SQL_GATE_PROJECT_ID}" \
    --display-name="SQL Execution Gate Terraform deployer"
fi

readonly SQL_GATE_TERRAFORM_ROLES=(
  roles/artifactregistry.admin
  roles/cloudbuild.builds.editor
  roles/iam.serviceAccountAdmin
  roles/iam.serviceAccountUser
  roles/logging.configWriter
  roles/logging.logWriter
  roles/monitoring.alertPolicyEditor
  roles/monitoring.notificationChannelEditor
  roles/resourcemanager.projectIamAdmin
  roles/run.admin
  roles/serviceusage.serviceUsageAdmin
)

for sql_gate_role in "${SQL_GATE_TERRAFORM_ROLES[@]}"; do
  gcloud projects add-iam-policy-binding "${SQL_GATE_PROJECT_ID}" \
    --member="${SQL_GATE_TERRAFORM_MEMBER}" \
    --role="${sql_gate_role}" \
    --condition=None \
    --quiet >/dev/null
done

gcloud storage buckets add-iam-policy-binding "gs://${SQL_GATE_STATE_BUCKET}" \
  --member="${SQL_GATE_TERRAFORM_MEMBER}" \
  --role="roles/storage.objectAdmin" \
  --quiet >/dev/null

gcloud storage buckets add-iam-policy-binding "gs://${SQL_GATE_CLOUD_BUILD_BUCKET}" \
  --member="${SQL_GATE_TERRAFORM_MEMBER}" \
  --role="roles/storage.objectViewer" \
  --quiet >/dev/null

echo "Terraform bootstrap complete."
echo "State bucket: gs://${SQL_GATE_STATE_BUCKET}"
echo "Build identity: ${SQL_GATE_TERRAFORM_EMAIL}"
