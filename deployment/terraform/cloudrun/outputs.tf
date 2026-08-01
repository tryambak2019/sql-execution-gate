output "service_url" {
  description = "Public SQL Execution Gate application URL."
  value       = google_cloud_run_v2_service.app.uri
}

output "runtime_service_account" {
  description = "Cloud Run runtime identity."
  value       = google_service_account.runtime.email
}

output "build_service_account" {
  description = "Cloud Build deployer identity."
  value       = google_service_account.build.email
}

output "image_repository" {
  description = "Artifact Registry image prefix used by cloudbuild.yaml."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_repository_id}/${var.service_name}"
}
