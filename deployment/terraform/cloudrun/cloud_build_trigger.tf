resource "google_cloudbuild_trigger" "main" {
  count = var.cloud_build_repository == "" ? 0 : 1

  project         = var.project_id
  location        = var.region
  name            = "fsbq-agent-main"
  description     = "Build, test, and deploy FSBQ when main changes"
  filename        = "cloudbuild.yaml"
  service_account = google_service_account.build.id

  repository_event_config {
    repository = var.cloud_build_repository
    push {
      branch = "^main$"
    }
  }

  substitutions = {
    _REGION     = var.region
    _REPOSITORY = var.artifact_repository_id
    _SERVICE    = var.service_name
  }

  included_files = [
    "app/**",
    "config/**",
    "frontend/**",
    "tests/unit/**",
    "Dockerfile",
    "cloudbuild.yaml",
    "pyproject.toml",
    "server.py",
    "uv.lock",
  ]

  depends_on = [
    google_project_service.required["cloudbuild.googleapis.com"],
    google_project_iam_member.build,
    google_service_account_iam_member.build_uses_runtime,
  ]
}
