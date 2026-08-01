locals {
  runtime_roles = toset([
    "roles/aiplatform.user",
    "roles/bigquery.dataViewer",
    "roles/bigquery.jobUser",
    "roles/logging.logWriter",
    "roles/serviceusage.serviceUsageConsumer",
  ])
  build_roles = toset([
    "roles/artifactregistry.writer",
    "roles/logging.logWriter",
    "roles/run.admin",
  ])
}

resource "google_project_iam_member" "runtime" {
  for_each = local.runtime_roles

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_project_iam_member" "build" {
  for_each = local.build_roles

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.build.email}"
}

resource "google_service_account_iam_member" "build_uses_runtime" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.build.email}"
}
