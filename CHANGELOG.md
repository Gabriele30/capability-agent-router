# Changelog

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
