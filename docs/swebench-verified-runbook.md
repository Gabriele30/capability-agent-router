# SWE-bench Verified External Benchmark Runbook

## Frozen identity

- Dataset: `SWE-bench/SWE-bench_Verified`, `test` split.
- Dataset revision: `03e151cf5560b1af6a4363c6a9d766deaaea6b56`.
- SWE-bench harness revision: `c7fd5abffe0b2086a8bb9389d23c47d930ef571f`.
- Sample specification:
  [`benchmark_specs/swebench-verified-v1.json`](../benchmark_specs/swebench-verified-v1.json).
- Sample SHA-256:
  `9a0f50548c1c1747878ea340ff9d2da5060662742a01eeb5edcf70d69230a06c`.
- Provider configuration to record, without changing it: Gemini
  `gemini-3.5-flash-lite`; Codex `gpt-5.6-terra`, effort `medium`.

The JSON specification contains only instance ID, public repository identity,
base commit, difficulty, version, and benchmark configuration. It contains no
task statement, patch, test patch, test identifier, prediction, credential, or
provider output.

## Prerequisites

Use Linux/x86_64 containers. On the Windows development host, use Docker Desktop
with the WSL2 backend and Linux containers, preferably from an Ubuntu WSL2 shell.
Do not alter Docker Desktop or WSL settings automatically.

- Docker must be installed and report Linux container mode.
- Reserve at least 120 GiB free disk, 16 GiB RAM, and eight CPU cores as the
  official SWE-bench planning baseline.
- Keep the external dataset, upstream repository clones, and Docker cache outside
  the CAR checkout.
- Use the pinned official harness revision; do not use a floating `main` or
  `latest` image identity.

Run the adapter's local-only preflight before acquisition. It checks Docker
availability, Linux mode when explicitly requested with a command runner,
x86_64 compatibility, and free-disk capacity; it changes no host setting.

## Acquisition

In an external cache directory, obtain the official harness at its pinned commit:

```text
git clone https://github.com/SWE-bench/SWE-bench.git swe-bench
git -C swe-bench checkout c7fd5abffe0b2086a8bb9389d23c47d930ef571f
```

Obtain the official Hugging Face dataset at revision
`03e151cf5560b1af6a4363c6a9d766deaaea6b56`. Record the acquisition tool version
and cache location only in local run metadata. Do not vendor dataset files,
upstream repositories, or Docker images into CAR.

Before the first provider execution, verify the downloaded metadata produces the
same ordered 24 IDs and sample SHA as the tracked specification. If it does not,
stop rather than silently updating the sample.

## Workspace and writable scope

For every `(instance, strategy)` pair, prepare a new checkout at the specification's
exact `base_commit` and require a clean Git working tree. Existing CAR benchmark
workspace isolation then gives GEMINI_ONLY, CODEX_ONLY, and CAR independent copies
of that same checkout.

The adapter derives task authorization only from `git ls-files` of that base
checkout: every existing tracked regular file becomes an explicit,
repository-relative benchmark task path. It does not inspect dataset patch fields,
test patches, changed-file lists, test identifiers, or provider performance. This
is a benchmark-only input to the existing explicit-path mechanisms; no production
authorization policy is altered. All three strategies receive exactly this same
scope.

The existing CAR change limits, protected-prefix checks, safe-apply validation,
and controlled-write checks remain in force. A future request to relax those
protections is out of scope and requires explicit approval.

## Provider-visible and evaluator-only data

The provider projection consists only of `instance_id`, `repo`, `base_commit`,
public `problem_statement`, `difficulty`, optional upstream `version`, normal
base-commit repository contents, and deterministic context selected equally for
all strategies.

The following fields are trusted evaluator-only data and must never be placed in
`CodingTaskContext`, prompts, Codex scratch preparation, routing, write scope,
telemetry, public result JSON, or provider logs:

- `patch`
- `test_patch`
- `FAIL_TO_PASS`
- `PASS_TO_PASS`

The base repository's existing tests may be read under the native checkout
contract. The official issue-specific test patch remains hidden from all providers.

## Dry evaluator smoke

Do not invoke Gemini or Codex for the smoke. For one selected instance, build the
official environment and invoke the pinned harness's evaluator-side single-instance
path with `skip_patch=True`. This uses the original buggy baseline with no candidate
patch; the expected result is **unresolved**, not resolved.

For a future actual candidate evaluation, the official harness invocation is
conceptually:

```text
python -m swebench.harness.run_evaluation
  --dataset_name SWE-bench/SWE-bench_Verified
  --predictions_path <evaluator-owned-empty-predictions.jsonl>
  --run_id car-swebench-dry
```

Use the exact arguments supported by the pinned harness checkout and record the
Docker image digest. The evaluator must run in an evaluator-side workspace. Never
copy its gold patch/test patch or raw evaluator output to a provider workspace.
Map an official resolved outcome to `verified_success=true`, an unresolved outcome
to a task failure, and Docker/setup/timeout failures to a distinct infrastructure
failure. Do not fake a successful smoke if image construction is unavailable.

An optional gold-patch self-check is allowed only wholly inside a disposable
evaluator-owned workspace and only if the official harness naturally supports it.
It must be destroyed afterward and none of its patch data, changed paths, or test
details may enter CAR/provider state.

## Future external run

Only after a successful smoke and all Go criteria in
[the external benchmark plan](external-benchmark-plan.md) are met:

1. Record CAR commit/version, tag SHA, sample SHA, dataset/harness revision,
   Docker image digest, platform, resource allocation, and price catalog identity.
2. Generate one fresh base workspace for each selected instance and strategy.
3. Give the three strategies the same issue text, context, scope, timeout budget,
   baseline, and native evaluator.
4. Keep provider outputs and candidate workspaces separate from evaluator assets.
5. Publish only privacy-safe structured results and aggregate metrics; never publish
   gold patches, test patches, raw source, credentials, or raw provider output.

No benchmark performance result is produced by this adapter milestone.
