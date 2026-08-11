# Benchmark foundations

CAR B3-A defines local JSON benchmark manifests and three benchmark-only strategies:
`gemini_only`, `codex_only`, and `car`. A case has an exact repository-relative file
scope and non-empty verification identifiers. Fixtures must be local Git repositories.

The canonical manifest hash is reproducible. Each future strategy receives an independent
temporary copy with identical byte-content baseline; creation, mutation, and cleanup never
modify the source fixture. B3-A supports clean committed fixtures only. No provider is run,
no benchmark result is claimed, and no network access is used.
