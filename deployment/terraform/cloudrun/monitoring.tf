resource "google_monitoring_notification_channel" "engagement_email" {
  count = var.engagement_alert_email == "" ? 0 : 1

  project      = var.project_id
  display_name = "SQL Execution Gate demo engagement email"
  type         = "email"
  labels = {
    email_address = var.engagement_alert_email
  }

  depends_on = [google_project_service.required["monitoring.googleapis.com"]]
}

resource "google_monitoring_alert_policy" "demo_engagement" {
  count = var.engagement_alert_email == "" ? 0 : 1

  project      = var.project_id
  display_name = "SQL Execution Gate demo page visited"
  combiner     = "OR"

  conditions {
    display_name = "Demo page visited"
    condition_matched_log {
      filter = <<-EOT
        resource.type="cloud_run_revision"
        resource.labels.service_name="${var.service_name}"
        jsonPayload.event="demo_visit_started"
      EOT

      label_extractors = {
        client_ip    = "EXTRACT(jsonPayload.client_ip)"
        request_path = "EXTRACT(jsonPayload.request_path)"
        user_agent   = "EXTRACT(jsonPayload.user_agent)"
      }
    }
  }

  alert_strategy {
    notification_rate_limit {
      period = "${var.notification_rate_limit_seconds}s"
    }
    auto_close = "1800s"
  }

  notification_channels = [
    google_monitoring_notification_channel.engagement_email[0].name
  ]

  documentation {
    content = <<-EOT
The SQL Execution Gate demo page was requested.

Incident details include extracted log labels for the requester IP address, request path, and user agent.

Inspect the associated structured Cloud Run log for the full event payload.
EOT
  }

  depends_on = [google_project_service.required["logging.googleapis.com"]]
}
