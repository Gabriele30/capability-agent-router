# Execution telemetry

CAR 0.7-A introduces an in-memory, provider-neutral telemetry foundation. It observes
structured execution boundaries; it does not influence routing, providers, retries,
verification, or source application.

`ExecutionTelemetry` records an opaque locally generated execution ID, routes, bounded
attempt metadata, monotonic-clock durations, verification summary, escalation summary,
source safety state, and final outcome. Attempt usage uses optional token fields:
`None` means unknown, never zero. Usage is marked provider-reported, runtime-reported,
estimated (reserved for later), or unavailable. This milestone performs no estimation
and records no costs.

`verified_success` is true only for CAR-authoritative verified outcomes: a finalized
Gemini coding verification or a finalized B2 controlled-Codex result. Provider success,
process exit, source changes, rejected deltas, restored failures, and read-only Codex
diagnoses are not verified coding success.

Telemetry excludes task prompts, source and patch contents, repository absolute paths,
environment and credentials, handoff bodies, provider output, and command stdout/stderr.
It has no persistence, network exporter, or analytics endpoint in 0.7-A.
