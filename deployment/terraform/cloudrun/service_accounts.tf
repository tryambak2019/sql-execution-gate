resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = "sql-execution-gate-runtime"
  display_name = "SQL Execution Gate Cloud Run runtime"
}

resource "google_service_account" "build" {
  project      = var.project_id
  account_id   = "sql-execution-gate-build"
  display_name = "SQL Execution Gate Cloud Build deployer"
}
