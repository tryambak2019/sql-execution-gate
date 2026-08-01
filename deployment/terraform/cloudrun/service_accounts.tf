resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = "fsbq-agent-runtime"
  display_name = "FSBQ Cloud Run runtime"
}

resource "google_service_account" "build" {
  project      = var.project_id
  account_id   = "fsbq-agent-build"
  display_name = "FSBQ Cloud Build deployer"
}
