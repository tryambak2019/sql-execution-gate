# Deploy SQL Execution Gate to Cloud Run

This path deploys the React interface and ADK API as one public Cloud Run
service. BigQuery jobs are billed to your compute project while the default
configuration reads `bigquery-public-data.thelook_ecommerce`.

## Prerequisites

- Google Cloud project with billing enabled
- `gcloud`, `bq`, Git, and permission to manage project IAM
- Vertex AI access to the configured Gemini models

```bash
gcloud auth login
gcloud auth application-default login
git clone https://github.com/tryambak2019/sql-execution-gate.git
cd sql-execution-gate

export SQL_GATE_PROJECT="your-unique-gcp-project-id"
export SQL_GATE_REGION="us-central1"
export SQL_GATE_DATA_PROJECT="bigquery-public-data"
export SQL_GATE_DATASET="thelook_ecommerce"
export SQL_GATE_REPOSITORY="sql-execution-gate-images"

# Existing deployments may retain this internal service identifier.
export SQL_GATE_SERVICE="fsbq-agent"
gcloud config set project "$SQL_GATE_PROJECT"
```

Confirm billing and dataset access:

```bash
gcloud billing projects describe "$SQL_GATE_PROJECT"
bq show --project_id="$SQL_GATE_PROJECT" \
  "$SQL_GATE_DATA_PROJECT:$SQL_GATE_DATASET"
```

Do not point an unauthenticated demo at confidential data.

## Bootstrap Terraform

Review and run the one-time bootstrap from an owner or administrator account:

```bash
./deployment/terraform/cloudrun/bootstrap.sh \
  "$SQL_GATE_PROJECT" \
  "$SQL_GATE_REGION"
```

The script creates the versioned state bucket and dedicated Terraform identity
outside the main stack. Keep their existing identifiers when updating an
already deployed environment.

```bash
export SQL_GATE_TERRAFORM_SA="fsbq-terraform@$SQL_GATE_PROJECT.iam.gserviceaccount.com"
export SQL_GATE_BUILD_SA="fsbq-agent-build@$SQL_GATE_PROJECT.iam.gserviceaccount.com"
export SQL_GATE_STATE_BUCKET="$SQL_GATE_PROJECT-fsbq-tf-state"
```

## Apply infrastructure

For a new Artifact Registry repository, set
`_CREATE_ARTIFACT_REPOSITORY=true`. Reuse `false` after Terraform owns it.

```bash
gcloud builds submit \
  --project="$SQL_GATE_PROJECT" \
  --region="$SQL_GATE_REGION" \
  --service-account="projects/$SQL_GATE_PROJECT/serviceAccounts/$SQL_GATE_TERRAFORM_SA" \
  --config=cloudbuild-infra.yaml \
  --substitutions="_STATE_BUCKET=$SQL_GATE_STATE_BUCKET,_REGION=$SQL_GATE_REGION,_REPOSITORY=$SQL_GATE_REPOSITORY,_DATA_PROJECT_ID=$SQL_GATE_DATA_PROJECT,_DATASET_ID=$SQL_GATE_DATASET,_CREATE_ARTIFACT_REPOSITORY=true" \
  .
```

The stack creates the Cloud Run service, runtime and build identities, required
APIs and IAM, and optional repository and main-branch trigger. It keeps one
instance because sessions currently use in-memory storage.

## Build and deploy

```bash
gcloud builds submit \
  --project="$SQL_GATE_PROJECT" \
  --region="$SQL_GATE_REGION" \
  --service-account="projects/$SQL_GATE_PROJECT/serviceAccounts/$SQL_GATE_BUILD_SA" \
  --config=cloudbuild.yaml \
  --substitutions="_REGION=$SQL_GATE_REGION,_REPOSITORY=$SQL_GATE_REPOSITORY,_SERVICE=$SQL_GATE_SERVICE" \
  .
```

Resolve and verify the deployed URL:

```bash
export SQL_GATE_URL="$(
  gcloud run services describe "$SQL_GATE_SERVICE" \
    --project="$SQL_GATE_PROJECT" \
    --region="$SQL_GATE_REGION" \
    --format='value(status.url)'
)"

curl --fail --show-error "$SQL_GATE_URL/api/list-apps"
curl --fail --show-error --head "$SQL_GATE_URL/app/"
open "$SQL_GATE_URL/app/"
```

Verify both paths:

1. Ask `Which 10 products generated the most revenue?`, inspect the dry-run
   evidence, approve, and confirm results.
2. Ask `Delete all cancelled orders and return the remaining revenue.` and
   confirm it is blocked before a BigQuery query job is submitted.

## Troubleshooting

Inspect service status and recent logs:

```bash
gcloud run services describe "$SQL_GATE_SERVICE" \
  --project="$SQL_GATE_PROJECT" \
  --region="$SQL_GATE_REGION" \
  --format='yaml(status.url,status.latestReadyRevisionName,status.traffic)'

gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=$SQL_GATE_SERVICE" \
  --project="$SQL_GATE_PROJECT" \
  --limit=100 \
  --order=desc
```

The runtime identity needs permission to create jobs in the compute project and
read the configured dataset. It does not need BigQuery write roles.

## Teardown

For a disposable project, deleting the project is the cleanest teardown.
Otherwise run `terraform destroy` from an audited environment against the same
remote backend, then separately remove the bootstrap identity and state bucket.
