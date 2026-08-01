locals {
  data_project_id = coalesce(var.data_project_id, var.project_id)
  runtime_environment = {
    GOOGLE_GENAI_USE_VERTEXAI = "true"
    GOOGLE_CLOUD_PROJECT      = var.project_id
    GOOGLE_CLOUD_LOCATION     = "global"
    VERTEX_AI_LOCATION        = var.region
    BQ_COMPUTE_PROJECT_ID     = var.project_id
    BQ_DATA_PROJECT_ID        = local.data_project_id
    BQ_DATASET_ID             = var.dataset_id
    BQ_MAXIMUM_BYTES_BILLED   = tostring(var.maximum_bytes_billed)
    DATASET_CONFIG_FILE       = "config/datasets/thelook_ecommerce_dataset_config.json"
    ENABLE_BQML               = "false"
    ROOT_AGENT_MODEL          = var.model_name
    BIGQUERY_AGENT_MODEL      = var.model_name
    BIGQUERY_PLANNER_MODEL    = var.sql_planner_model_name
    ANALYTICS_AGENT_MODEL     = var.model_name
    BQML_AGENT_MODEL          = var.model_name
    CRITIC_MODEL              = var.model_name
    WORKER_MODEL              = var.model_name
  }
}

resource "google_cloud_run_v2_service" "app" {
  project             = var.project_id
  name                = var.service_name
  location            = var.region
  deletion_protection = false

  template {
    service_account                  = google_service_account.runtime.email
    max_instance_request_concurrency = 20

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    containers {
      image = "us-docker.pkg.dev/cloudrun/container/hello"

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      dynamic "env" {
        for_each = local.runtime_environment
        content {
          name  = env.key
          value = env.value
        }
      }

      startup_probe {
        initial_delay_seconds = 2
        timeout_seconds       = 3
        period_seconds        = 5
        failure_threshold     = 12

        http_get {
          path = "/healthz"
          port = 8080
        }
      }

      liveness_probe {
        timeout_seconds   = 3
        period_seconds    = 30
        failure_threshold = 3

        http_get {
          path = "/healthz"
          port = 8080
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }

  depends_on = [
    google_project_service.required["run.googleapis.com"],
    google_project_iam_member.runtime,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.app.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
