# Deployment

Two independent deployment paths are retained:

- [`terraform/cloudrun`](terraform/cloudrun) is the small public FSBQ demo:
  React, ADK API, Cloud Run, Artifact Registry, and Cloud Build.
- [`terraform`](terraform) is the original Agent Starter Pack infrastructure
  for Agent Engine and multi-environment delivery.

Use the Cloud Run stack for the SPC artifact. It produces a user-facing URL and
does not require Google Cloud Deploy.

The original Agent Engine stack remains available for later platform work but
does not host the React interface.
