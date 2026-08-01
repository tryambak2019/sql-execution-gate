# Deploy FSBQ in your own Google Cloud project

This guide reproduces the public FSBQ demo in a Google Cloud project controlled
by the reader. It does not require access to the author's project.

The default path keeps the BigQuery dataset, Vertex AI calls, Cloud Build,
Artifact Registry, Terraform state, and Cloud Run service in one project. That
is the easiest configuration to audit and remove.

## 1. Prerequisites

You need:

- a Google Cloud account with billing enabled;
- permission to create a project, enable APIs, create service accounts, and
  change project IAM;
- the Google Cloud CLI (`gcloud`) and Git;
- a BigQuery dataset containing at least one table;
- access to Gemini 2.5 Flash through Vertex AI in the chosen project.

Authenticate and clone the repository:

```bash
gcloud auth login
gcloud auth application-default login
git clone https://github.com/tryambak2019/fsbq-agent.git
cd fsbq-agent
```

Use a new GCP project if you want the cleanest teardown. Set your own values;
do not copy the example project ID literally:

```bash
export FSBQ_PROJECT="your-unique-gcp-project-id"
export FSBQ_REGION="us-central1"
export FSBQ_DATA_PROJECT="bigquery-public-data"
export FSBQ_DATASET="thelook_ecommerce"
export FSBQ_REPOSITORY="fsbq-images"
export FSBQ_SERVICE="fsbq-agent"

gcloud config set project "$FSBQ_PROJECT"
```

The project must already exist and have billing attached. Confirm the active
identity and project before creating anything:

```bash
gcloud auth list --filter=status:ACTIVE
gcloud config get-value project
gcloud billing projects describe "$FSBQ_PROJECT"
```

## 2. Prepare a BigQuery dataset

FSBQ discovers every table schema in the configured dataset at runtime. It does
not copy TheLook data into your project: queries run as jobs billed to
`FSBQ_PROJECT` and read the public US dataset from `FSBQ_DATA_PROJECT`.

Confirm that TheLook is visible:

```bash
bq show --project_id="$FSBQ_PROJECT" "$FSBQ_DATA_PROJECT:$FSBQ_DATASET"
bq ls --project_id="$FSBQ_PROJECT" "$FSBQ_DATA_PROJECT:$FSBQ_DATASET"
```

Do not point a public demo at confidential production data. The runtime service
account receives read access to the configured project, and the current demo
allows unauthenticated access to its Cloud Run URL.

## 3. Bootstrap Terraform

The one-time bootstrap creates:

- a versioned GCS bucket for Terraform state;
- the `fsbq-terraform` service account;
- the IAM roles needed for that identity to create the demo stack.

Review
[`deployment/terraform/cloudrun/bootstrap.sh`](../deployment/terraform/cloudrun/bootstrap.sh)
before running it because the script grants project-level administrative roles.

```bash
./deployment/terraform/cloudrun/bootstrap.sh \
  "$FSBQ_PROJECT" \
  "$FSBQ_REGION"
```

Set the generated identities:

```bash
export FSBQ_TERRAFORM_SA="fsbq-terraform@$FSBQ_PROJECT.iam.gserviceaccount.com"
export FSBQ_BUILD_SA="fsbq-agent-build@$FSBQ_PROJECT.iam.gserviceaccount.com"
export FSBQ_STATE_BUCKET="$FSBQ_PROJECT-fsbq-tf-state"
```

Older bootstrap runs may not have granted permission to create the optional
Cloud Monitoring email notification channel and log-based alert policy. Grant
all three roles once before applying:

```bash
gcloud projects add-iam-policy-binding "$FSBQ_PROJECT" \
  --member="serviceAccount:$FSBQ_TERRAFORM_SA" \
  --role="roles/monitoring.notificationChannelEditor" \
  --condition=None

gcloud projects add-iam-policy-binding "$FSBQ_PROJECT" \
  --member="serviceAccount:$FSBQ_TERRAFORM_SA" \
  --role="roles/monitoring.alertPolicyEditor" \
  --condition=None

gcloud projects add-iam-policy-binding "$FSBQ_PROJECT" \
  --member="serviceAccount:$FSBQ_TERRAFORM_SA" \
  --role="roles/logging.configWriter" \
  --condition=None
```

`roles/logging.configWriter` supplies
`logging.notificationRules.create`, which Cloud Monitoring needs when it
creates this log-based alert.

The explicit `--condition=None` matters. If the project policy already contains
conditional bindings, omitting it causes `gcloud` to ask whether to reuse one.
Do not select a temporary condition for this role, because access will expire
at that condition's timestamp.

If an apply creates the notification channel but then fails while creating the
alert policy, grant the permission named in the error and rerun the same
infrastructure build. Terraform state preserves the completed channel and
retries the alert policy; do not delete the channel or Cloud Run revision.

The deployer roles are bootstrap permissions, not resources managed by the main
Terraform stack. Terraform cannot establish its own first-run authority, and a
stack that manages its execution identity can accidentally revoke the access
needed to repair itself. An owner/admin runs the bootstrap once; subsequent
infrastructure applies run as the dedicated Terraform service account.

## 4. Create the infrastructure

Submit the Terraform build. `_CREATE_ARTIFACT_REPOSITORY=true` is required for
a new repository; set it to `false` on later runs after Terraform owns it.

```bash
gcloud builds submit \
  --project="$FSBQ_PROJECT" \
  --region="$FSBQ_REGION" \
  --service-account="projects/$FSBQ_PROJECT/serviceAccounts/$FSBQ_TERRAFORM_SA" \
  --config=cloudbuild-infra.yaml \
  --substitutions="_STATE_BUCKET=$FSBQ_STATE_BUCKET,_REGION=$FSBQ_REGION,_REPOSITORY=$FSBQ_REPOSITORY,_DATA_PROJECT_ID=$FSBQ_DATA_PROJECT,_DATASET_ID=$FSBQ_DATASET,_CREATE_ARTIFACT_REPOSITORY=true" \
  .
```

This creates the runtime and build identities, enables the required APIs,
creates Artifact Registry, and creates a public Cloud Run service with a
placeholder image. Terraform intentionally limits the service to one instance
because ADK sessions currently use in-memory storage. It also keeps one minimum
instance warm so the evaluation UI does not pay a Cloud Run cold-start penalty.

An apply can complete the Cloud Run update and then fail while creating another
resource. After fixing the reported permission, rerun the same build. Terraform
uses the remote state to preserve completed changes and retry the missing ones;
do not manually remove a successful Cloud Run revision.

To apply the same setting directly, without rerunning Terraform:

```bash
gcloud run services update "$FSBQ_SERVICE" \
  --project="$FSBQ_PROJECT" \
  --region="$FSBQ_REGION" \
  --min=1
```

Confirm the service-level setting:

```bash
gcloud run services describe "$FSBQ_SERVICE" \
  --project="$FSBQ_PROJECT" \
  --region="$FSBQ_REGION" \
  --format='value(scaling.minInstanceCount)'
```

This reduces container startup delay but does not remove Gemini processing
latency. To stop idle-instance charges after an evaluation period, set the
minimum back to zero:

```bash
gcloud run services update "$FSBQ_SERVICE" \
  --project="$FSBQ_PROJECT" \
  --region="$FSBQ_REGION" \
  --min=0
```

If Terraform is applied later, its configured `_MIN_INSTANCES` value becomes
authoritative again. Pass `_MIN_INSTANCES=0` in the infrastructure build
substitutions if zero should remain the desired state.

## 5. Build and deploy FSBQ

Build the frontend and backend test stages, publish an immutable container,
update Cloud Run, and run the smoke tests:

```bash
gcloud builds submit \
  --project="$FSBQ_PROJECT" \
  --region="$FSBQ_REGION" \
  --service-account="projects/$FSBQ_PROJECT/serviceAccounts/$FSBQ_BUILD_SA" \
  --config=cloudbuild.yaml \
  --substitutions="_REGION=$FSBQ_REGION,_REPOSITORY=$FSBQ_REPOSITORY,_SERVICE=$FSBQ_SERVICE" \
  .
```

Resolve the service URL instead of hard-coding it:

```bash
export FSBQ_URL="$(
  gcloud run services describe "$FSBQ_SERVICE" \
    --project="$FSBQ_PROJECT" \
    --region="$FSBQ_REGION" \
    --format='value(status.url)'
)"

echo "$FSBQ_URL"
```

## 6. Verify the deployment

Verify the registered ADK app and compiled UI separately:

```bash
curl --fail --show-error "$FSBQ_URL/api/list-apps"
curl --fail --show-error --head "$FSBQ_URL/app/"
```

Expected results:

- `/api/list-apps` returns HTTP 200 and includes `"app"`;
- `/app/` returns HTTP 200.

Open the application:

```bash
open "$FSBQ_URL/app/"
```

On Linux, use `xdg-open` or paste the URL into a browser. Ask a read-only
question such as `Which 10 products generated the most revenue?`. Confirm that
FSBQ:

1. generates SQL without executing it;
2. displays the SQL and referenced table schema;
3. waits for Approve or Reject;
4. executes only after approval;
5. returns the result.

Also test Reject. A successful query proves the happy path; rejection proves
the safety boundary that distinguishes this project.

## 7. Troubleshooting

### The build deploys but the smoke test reports 404

First query the current service URL and test each route manually:

```bash
gcloud run services describe "$FSBQ_SERVICE" \
  --project="$FSBQ_PROJECT" \
  --region="$FSBQ_REGION" \
  --format='yaml(status.url,status.latestReadyRevisionName,status.traffic)'

curl -i "$FSBQ_URL/api/list-apps"
curl -I "$FSBQ_URL/app/"
```

Cloud Run uses `/healthz` for internal startup and liveness probes. The public
deployment smoke test intentionally checks the ADK API and compiled frontend.

### The browser reports a failed load

Retry once after the Cloud Run instance is warm and inspect the request logs:

```bash
gcloud logging read \
  "resource.type=cloud_run_revision AND
   resource.labels.service_name=$FSBQ_SERVICE" \
  --project="$FSBQ_PROJECT" \
  --limit=100 \
  --order=desc
```

An interrupted SSE request can surface as different browser messages. Confirm
whether `/api/run_sse` reached Cloud Run before diagnosing it as a browser-only
problem.

### BigQuery permission errors

Confirm the runtime identity and its dataset access:

```bash
gcloud run services describe "$FSBQ_SERVICE" \
  --project="$FSBQ_PROJECT" \
  --region="$FSBQ_REGION" \
  --format='value(spec.template.spec.serviceAccountName)'
```

The runtime needs permission to run jobs in the compute project and read the
configured dataset. Avoid granting BigQuery write roles; FSBQ is designed for
read-only execution.

## 8. Reproduce locally instead

If you only want to evaluate the code, use the shorter
[local setup](../README.md#run-locally). Cloud deployment is not required for
unit tests or frontend development.

## 9. Tear down

Terraform owns the deployed stack, but the bootstrap state bucket and bootstrap
identity are intentionally outside that stack. The current infrastructure build
applies desired state; it does not expose a destroy mode. Do not rerun
`cloudbuild-infra.yaml` expecting it to delete resources.

For a disposable project, deleting the entire project is the cleanest complete
teardown. Otherwise run `terraform destroy` against the same remote backend from
an audited environment, then separately remove the state bucket and bootstrap
service account. Inspect the Terraform plan before approving destruction.

Review billable resources in Cloud Billing after teardown. Cloud Run can scale
to zero, but Artifact Registry, Cloud Build, BigQuery, logs, and stored
Terraform state can still incur usage or storage costs.
