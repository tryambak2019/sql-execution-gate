#!/usr/bin/env bash
set -euo pipefail

readonly FSBQ_PROJECT_ID="${1:-gcplab20250706}"
readonly FSBQ_REGION="${2:-us-central1}"
readonly FSBQ_STATE_BUCKET="${3:-${FSBQ_PROJECT_ID}-fsbq-tf-state}"
readonly FSBQ_TERRAFORM_ACCOUNT_ID="fsbq-terraform"
readonly FSBQ_TERRAFORM_EMAIL="${FSBQ_TERRAFORM_ACCOUNT_ID}@${FSBQ_PROJECT_ID}.iam.gserviceaccount.com"
readonly FSBQ_TERRAFORM_MEMBER="serviceAccount:${FSBQ_TERRAFORM_EMAIL}"

gcloud config set project "${FSBQ_PROJECT_ID}"
gcloud services enable \
  cloudbuild.googleapis.com \
  iam.googleapis.com \
  serviceusage.googleapis.com \
  storage.googleapis.com \
  --project="${FSBQ_PROJECT_ID}"

if ! gcloud storage buckets describe "gs://${FSBQ_STATE_BUCKET}" \
  --project="${FSBQ_PROJECT_ID}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${FSBQ_STATE_BUCKET}" \
    --project="${FSBQ_PROJECT_ID}" \
    --location="${FSBQ_REGION}" \
    --uniform-bucket-level-access
fi

gcloud storage buckets update "gs://${FSBQ_STATE_BUCKET}" \
  --project="${FSBQ_PROJECT_ID}" \
  --versioning \
  --uniform-bucket-level-access

if ! gcloud iam service-accounts describe "${FSBQ_TERRAFORM_EMAIL}" \
  --project="${FSBQ_PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${FSBQ_TERRAFORM_ACCOUNT_ID}" \
    --project="${FSBQ_PROJECT_ID}" \
    --display-name="FSBQ Terraform deployer"
fi

readonly FSBQ_TERRAFORM_ROLES=(
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

for fsbq_role in "${FSBQ_TERRAFORM_ROLES[@]}"; do
  gcloud projects add-iam-policy-binding "${FSBQ_PROJECT_ID}" \
    --member="${FSBQ_TERRAFORM_MEMBER}" \
    --role="${fsbq_role}" \
    --condition=None \
    --quiet >/dev/null
done

gcloud storage buckets add-iam-policy-binding "gs://${FSBQ_STATE_BUCKET}" \
  --member="${FSBQ_TERRAFORM_MEMBER}" \
  --role="roles/storage.objectAdmin" \
  --quiet >/dev/null

echo "Terraform bootstrap complete."
echo "State bucket: gs://${FSBQ_STATE_BUCKET}"
echo "Build identity: ${FSBQ_TERRAFORM_EMAIL}"
