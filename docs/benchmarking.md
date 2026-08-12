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
synthetic provider/runtime output without network access.

## Running a benchmark

`car benchmark` runs a validated local manifest using the selected strategy over an
independent temporary Git workspace for every case. Select one strategy explicitly or
request all three:

```powershell
car benchmark .\benchmarks\manifest.json --strategy gemini-only
car benchmark .\benchmarks\manifest.json --all
car benchmark .\benchmarks\manifest.json --all --json-out .\benchmark-result.json
```

The command is an explicit live execution entry point: selected strategies may invoke
configured Gemini and the locally installed Codex runtime. It never runs in normal
tests or in the background. `gemini-only` stops after the existing CAR verification and
rollback path. `codex-only` is benchmark-only and continues to use the controlled-write
pipeline. `car` uses the ordinary production coding flow, including its existing
escalation policy.

The terminal report aggregates only verification-authoritative successes. The JSON export
contains reproducibility metadata (schema version, run ID, UTC timestamp, CAR version,
manifest hash, selected strategies, and the price-catalog snapshot) plus privacy-safe task
results and summaries. It excludes source, patches, output, credentials, environment values,
and absolute paths.

Reference inference cost is public API list-price accounting, not actual provider billing.
Unknown usage is never zero: Codex structured usage is currently unavailable, so Codex-only
and CAR runs that escalate to Codex report incomplete cost as `N/A`. Synthetic fixtures used
by tests exercise the harness only and are not benchmark evidence. There is currently no
public benchmark dataset, report export beyond JSON, or benchmark claim.
