# FSBQ Cloud Run infrastructure

This stack creates the small public demo runtime:

- Cloud Run service and public invoker policy
- dedicated runtime and build service accounts
- least-purpose IAM for Vertex AI, BigQuery, Artifact Registry, and deployment
- required Google Cloud APIs
- optional Artifact Registry repository
- optional main-branch Cloud Build trigger
- a rate-limited email alert when the demo page is requested

It intentionally does not use Google Cloud Deploy or the existing Agent Engine
staging/production stack.

Each `GET /app` request emits an anonymous `demo_visit_started` event, and Cloud
Monitoring sends the alert to `engagement_alert_email`. The event includes the
timestamp, client IP, request path, and user agent; it never includes query
text, credentials, or BigQuery results. Set `engagement_alert_email = ""` to
disable notifications. Accepted development false positives are recorded in
`docs/DEFERRED_HARDENING.md`.

## One-time bootstrap

```bash
./deployment/terraform/cloudrun/bootstrap.sh \
  gcplab20250706 \
  us-central1
```

The script creates a versioned, uniform-access GCS state bucket and a dedicated
`fsbq-terraform` service account. It grants that identity the project-level
permissions required to create the resources in this stack, including the
optional Cloud Monitoring email notification channel and log-based alert
policy. Run it only from an owner/admin account and review the role list before
reusing it in another project.

These deployer permissions intentionally remain in the one-time bootstrap
rather than in this Terraform stack. Terraform cannot grant its execution
identity the permissions needed for its first apply unless the caller already
has permission to change project IAM. Although the bootstrapped identity later
has `roles/resourcemanager.projectIamAdmin`, letting the main stack modify its
own permissions creates a self-revocation and recovery risk. Bootstrap the
deployer identity from an owner/admin account; use Terraform for the application
infrastructure it deploys.

If the service account was bootstrapped before monitoring support was added,
grant all three missing roles once from an owner/admin account. The Logging role
is required because this is a log-based alert policy:

```bash
gcloud projects add-iam-policy-binding gcplab20250706 \
  --member="serviceAccount:fsbq-terraform@gcplab20250706.iam.gserviceaccount.com" \
  --role="roles/monitoring.notificationChannelEditor" \
  --condition=None

gcloud projects add-iam-policy-binding gcplab20250706 \
  --member="serviceAccount:fsbq-terraform@gcplab20250706.iam.gserviceaccount.com" \
  --role="roles/monitoring.alertPolicyEditor" \
  --condition=None

gcloud projects add-iam-policy-binding gcplab20250706 \
  --member="serviceAccount:fsbq-terraform@gcplab20250706.iam.gserviceaccount.com" \
  --role="roles/logging.configWriter" \
  --condition=None
```

Always use `--condition=None` for this permanent setup permission. If `gcloud`
instead displays existing policy conditions and asks which one to reuse, do not
select a temporary condition: it would make the role expire with that binding.
Rerun the command above with `--condition=None`.

If Terraform already created one monitoring resource before failing on the
other, rerun the infrastructure build after granting the missing role. Terraform
will preserve the completed resource and retry the incomplete operation.

Terraform uses a partial GCS backend configuration. The bucket is supplied by
Cloud Build, so no local state or project-specific backend file is committed.

## Apply infrastructure through Cloud Build

From the repository root:

```bash
gcloud builds submit \
  --project=gcplab20250706 \
  --region=us-central1 \
  --service-account=projects/gcplab20250706/serviceAccounts/fsbq-terraform@gcplab20250706.iam.gserviceaccount.com \
  --config=cloudbuild-infra.yaml .
```

`cloudbuild-infra.yaml` formats, initializes, validates, plans, and applies the
stack. The plan is saved inside the build workspace and the remote backend
locks state during plan/apply.

The defaults run BigQuery jobs in `gcplab20250706` and read tables from
`bigquery-public-data.thelook_ecommerce`. Override `_DATA_PROJECT_ID` and
`_DATASET_ID` together when deploying against another dataset.

Terraform applies can partially succeed. For example, Cloud Run may update
before notification-channel creation fails with HTTP 403. After correcting IAM,
rerun the same infrastructure build. Terraform reads the remote state, retains
the completed resources, and retries the missing change; do not delete the
Cloud Run revision or state bucket.

The default configuration reuses `bigquery-bot-repo`. Set
`create_artifact_repository = true` only when that repository does not exist.

The first Terraform apply deploys Google's placeholder container so the Cloud
Run service exists. Cloud Build then tests the frontend and backend, pushes an
immutable image tagged with `$BUILD_ID`, updates only the service image, and
smoke-tests `/api/list-apps` and `/app/`. Cloud Run separately uses `/healthz`
for startup and liveness probes inside the service.

Run the first build manually:

```bash
gcloud builds submit \
  --project=gcplab20250706 \
  --region=us-central1 \
  --service-account=projects/gcplab20250706/serviceAccounts/fsbq-agent-build@gcplab20250706.iam.gserviceaccount.com \
  --config=cloudbuild.yaml .
```

To automate later builds, connect the GitHub repository in Cloud Build and set
`cloud_build_repository` to its full v2 repository resource name.

Infrastructure and application deployment intentionally use different service
accounts. The Terraform identity can change IAM and APIs; the application build
identity can only push the image, update Cloud Run, and act as the runtime
identity.
