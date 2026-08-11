# Changelog

## [0.6.0] - 2026-08-11

### Added

- Experimental controlled Codex writes after eligible verified Gemini-to-Codex
  failures, with explicit per-invocation authorization and repository-relative scope.
- Exact source baseline capture, isolated worktree projection, strict delta validation,
  transactional source application, and verification-gated finalization.
- Opt-in live validation for both the internal chain and the public `car execute` path.
- Acceptance-invariant hardening so only finalized B2 verification can represent
  accepted source changes.

### Safety

- Codex writes only in CAR-owned isolated workspaces; CAR validates the whole delta.
- Routing and read-only Codex authorization do not grant write permission.
- Source acceptance requires verification, post-verification integrity, and finalization;
  source application and the Codex runtime cannot accept changes independently.
- Windows ACL preparation is limited to CAR-owned temporary worktree parents.

### Known limitations

- Controlled write is disabled by default and requires explicit existing-file scope.
- Direct `CODEX` routes do not use controlled write; DELETE, RENAME, symlink,
  protected-path, and binary changes fail closed.

## [0.5.0] - 2026-08-10

### Added

- Explicit, scoped Gemini coding execution through `car execute`, with
  CAR-controlled patch validation, verification, and rollback.
- Optional Gemini-to-Codex failure handoff with ephemeral read-only Codex
  diagnostics; a successful diagnostic never resolves the coding task.
- Windows resolved-executable support and current Codex CLI global-option ordering
  for the local read-only runtime.
- Compact structured execution-result presentation for `car execute`.

### Safety

- Transactional patch application and verification-gated finalization preserve
  pre-existing user state and roll back failed verified attempts.
- Verification presets remain CAR-controlled; pytest verification avoids new cache
  and bytecode artifacts in the repository.
- Codex uses the user's existing local authentication only and remains read-only.

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
