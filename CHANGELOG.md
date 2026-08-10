# Changelog

## Unreleased

### Added

- Explicit, scoped Gemini coding execution through `car execute`, with
  CAR-controlled patch validation, verification, and rollback.
- Optional Gemini-to-Codex failure handoff with ephemeral read-only Codex
  diagnostics; a successful diagnostic never resolves the coding task.

### Validation

- Live-verified C1 Gemini success and C2 Gemini failure/rollback/read-only Codex
  flows, both using synthetic repositories and explicit opt-in test gates.

## [0.4.0] - 2026-08-09

### Added

- Deterministic repository intelligence, task analysis, and routing.
- Verified L0 formatting and lint-fix execution with rollback protection.
- Gemini provider foundation, structured classification, timeout/retry handling,
  error normalization, and opt-in live validation.
- Lazy provider consultation, conservative fusion, `RoutingEvaluation`, unified
  `car analyze` / `car task` routing, provider status UX, and structured JSON output.

### Safety

- Provider failures fall back safely to deterministic routing.
- Gemini credentials remain environment-only and are not serialized.
- L0 preserves user changes, verifies results, and rolls back failures.
- `car analyze` is read-only with respect to the workspace.

### Known limitations

- Gemini coding execution is not implemented.
- Codex execution and Gemini-to-Codex execution are not implemented.
- VS Code integration is not implemented.
