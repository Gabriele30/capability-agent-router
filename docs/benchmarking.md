# Benchmark foundations

CAR B3-A defines local JSON benchmark manifests and three benchmark-only strategies:
`gemini_only`, `codex_only`, and `car`. A case has an exact repository-relative file
scope and non-empty verification identifiers. Fixtures must be local Git repositories.

The canonical manifest hash is reproducible. Each future strategy receives an independent
temporary copy with identical byte-content baseline; creation, mutation, and cleanup never
modify the source fixture. B3-A supports clean committed fixtures only. No provider is run,
no benchmark result is claimed, and no network access is used.

## Internal strategy runner

B3-B adds an internal runner over those isolated workspaces. `gemini_only` reuses the
authorized Gemini proposal/application/verification pipeline and stops after failure;
it never invokes Codex. `codex_only` is a benchmark-only adapter over the existing
controlled-write pipeline and accepts changes only after B2 finalization. `car` invokes
the normal production coding flow and preserves its routing and escalation behavior.

All strategies receive the same task, exact file authorization and CAR-controlled
verification plan. Success is verification-authoritative. Codex token usage remains
unavailable, so its reference cost is incomplete rather than zero. Tests inject
synthetic provider/runtime output without network access. A public benchmark CLI,
report and export are not implemented yet.
