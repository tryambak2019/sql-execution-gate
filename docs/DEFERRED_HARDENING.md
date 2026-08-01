# Deferred alert hardening

During development, every `GET /app` or `GET /app/` request emits a
`demo_visit_started` event and can trigger the Cloud Monitoring email alert.
This intentionally includes the owner's visits, refreshes, bots, automated
scanners, link previews, and the deployment smoke test.

Before using this as a reliable visitor signal, add owner exclusion, bot and
scanner filtering, session-level deduplication, and smoke-test exclusion.
