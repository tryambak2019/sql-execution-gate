variable "project_id" {
  description = "Google Cloud project that hosts FSBQ."
  type        = string
}

variable "region" {
  description = "Cloud Run, Cloud Build, and Artifact Registry region."
  type        = string
  default     = "us-central1"
}

variable "service_name" {
  description = "Cloud Run service name."
  type        = string
  default     = "fsbq-agent"
}

variable "artifact_repository_id" {
  description = "Artifact Registry Docker repository used by Cloud Build."
  type        = string
  default     = "bigquery-bot-repo"
}

variable "create_artifact_repository" {
  description = "Create the repository. Keep false when reusing bigquery-bot-repo."
  type        = bool
  default     = false
}

variable "data_project_id" {
  description = "Project containing the demo BigQuery dataset. Defaults to project_id."
  type        = string
  default     = "bigquery-public-data"
}

variable "dataset_id" {
  description = "BigQuery dataset exposed by the demo."
  type        = string
  default     = "thelook_ecommerce"
}

variable "model_name" {
  description = "Vertex AI model used by FSBQ agents other than SQL planning."
  type        = string
  default     = "gemini-2.5-flash"
}

variable "sql_planner_model_name" {
  description = "Vertex AI reasoning model used only to generate SQL plans."
  type        = string
  default     = "gemini-3.1-pro-preview"
}

variable "cloud_build_repository" {
  description = "Optional Cloud Build v2 repository resource name for a main-branch trigger."
  type        = string
  default     = ""
}

variable "max_instances" {
  description = "Cloud Run maximum instance count. Keep at one while sessions are in memory."
  type        = number
  default     = 1
}

variable "min_instances" {
  description = "Cloud Run minimum warm instances. Keep at one for latency-sensitive demos."
  type        = number
  default     = 1
}

variable "engagement_alert_email" {
  description = "Email address notified when a tagged visitor submits a demo query. Empty disables the alert."
  type        = string
  default     = "dev.ocicloud26@yahoo.com"
}

variable "notification_rate_limit_seconds" {
  description = "Minimum seconds between notifications for matched demo visits."
  type        = number
  default     = 300

  validation {
    condition     = var.notification_rate_limit_seconds >= 300
    error_message = "notification_rate_limit_seconds must be at least 300."
  }
}
