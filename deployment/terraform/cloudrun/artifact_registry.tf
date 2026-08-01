resource "google_artifact_registry_repository" "images" {
  count = var.create_artifact_repository ? 1 : 0

  project       = var.project_id
  location      = var.region
  repository_id = var.artifact_repository_id
  description   = "Container images for FSBQ"
  format        = "DOCKER"

  depends_on = [
    google_project_service.required["artifactregistry.googleapis.com"],
  ]
}
