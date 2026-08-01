# SQL Execution Gate Cloud Run infrastructure

This is the repository's active infrastructure stack. It creates:

- a public Cloud Run service;
- dedicated runtime and build identities;
- least-purpose IAM for Vertex AI, BigQuery, Artifact Registry, and deployment;
- required Google Cloud APIs;
- optional Artifact Registry repository and main-branch build trigger.

Run the one-time bootstrap from an owner or administrator account:

```bash
./deployment/terraform/cloudrun/bootstrap.sh gcplab20250706 us-central1
```

Apply through the locked remote backend:

```bash
gcloud builds submit \
  --project=gcplab20250706 \
  --region=us-central1 \
  --service-account=projects/gcplab20250706/serviceAccounts/fsbq-terraform@gcplab20250706.iam.gserviceaccount.com \
  --config=cloudbuild-infra.yaml .
```

The defaults bill query jobs to `gcplab20250706`, read
`bigquery-public-data.thelook_ecommerce`, enforce a 1 GB per-query ceiling, and
keep BQML disabled. Existing service-account, state-bucket, and Cloud Run names
are intentionally retained to avoid Terraform state migration.

Deploy the tested application image with `cloudbuild.yaml`. See
[docs/DEPLOYMENT.md](../../../docs/DEPLOYMENT.md) for the complete flow.
