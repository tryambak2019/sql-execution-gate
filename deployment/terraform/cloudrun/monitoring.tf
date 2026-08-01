resource "google_monitoring_notification_channel" "engagement_email" {
  count = var.engagement_alert_email == "" ? 0 : 1

  project      = var.project_id
  display_name = "FSBQ demo engagement email"
  type         = "email"
  labels = {
    email_address = var.engagement_alert_email
  }

  depends_on = [google_project_service.required["monitoring.googleapis.com"]]
}

resource "google_monitoring_alert_policy" "demo_engagement" {
  count = var.engagement_alert_email == "" ? 0 : 1

  project      = var.project_id
  display_name = "FSBQ demo page visited"
  combiner     = "OR"

  conditions {
    display_name = "Demo page visited"
    condition_matched_log {
      filter = <<-EOT
        resource.type="cloud_run_revision"
        resource.labels.service_name="${var.service_name}"
        jsonPayload.event="demo_visit_started"
      EOT
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
    content = "The FSBQ demo page was requested. During development this can include the owner, refreshes, bots, scanners, link previews, and deployment smoke tests. Inspect the structured Cloud Run log for timestamp, IP address, and user agent."
  }

  depends_on = [google_project_service.required["logging.googleapis.com"]]
}
