# External Benchmark Validation Plan

## Goal

Validate the frozen CAR 0.6.0 configuration outside the internal synthetic
20-task pilot. This is a benchmark selection and adapter-design decision, not
an implementation or a tuning exercise. The three strategies must continue to
use the same models and settings: `gemini-3.5-flash-lite`, `gpt-5.6-terra`, and
Codex reasoning effort `medium`.

The result must report reference API-equivalent inference cost, never actual
provider billing. No provider is invoked as part of this plan.

## Implementation status

The benchmark-side SWE-bench Verified adapter is now implemented without a CAR
runtime change. Its tracked identity is
[`benchmark_specs/swebench-verified-v1.json`](../benchmark_specs/swebench-verified-v1.json):
it pins dataset revision `03e151cf5560b1af6a4363c6a9d766deaaea6b56`,
official harness revision `c7fd5abffe0b2086a8bb9389d23c47d930ef571f`, and
the ordered 24-instance sample identity
`9a0f50548c1c1747878ea340ff9d2da5060662742a01eeb5edcf70d69230a06c`.

The adapter has no provider construction or execution path. It has deterministic
selection, a provider-safe projection, base-commit and clean-checkout validation,
tracked-file scope derivation, evaluator result mapping, and a non-mutating Docker
preflight. The Docker evaluator smoke remains contingent on a separately prepared
official harness/image cache.

## Candidate datasets

- **SWE-bench Verified.** The SWE-bench maintainers publish this 500-instance,
  human-verified subset of real GitHub issue resolution tasks. Instances are
  Python repository issues pinned to a base commit and include an official
  Docker-based evaluator. The official dataset documentation describes its
  schema and availability on Hugging Face; the project is MIT-licensed.
  [Dataset guide](https://github.com/SWE-bench/SWE-bench/blob/main/docs/guides/datasets.md)
  and [official repository](https://github.com/SWE-bench/SWE-bench).
- **SWE-bench Lite.** A smaller 534-instance SWE-bench subset with the same
  family of Python issue/commit/evaluator mechanics. It is useful for local
  adapter development, but it is not the primary validation set because it
  lacks Verified's human solvability review.
  [Dataset guide](https://github.com/SWE-bench/SWE-bench/blob/main/docs/guides/datasets.md).
- **BugsInPy.** A public Python bug benchmark containing real bugs from Python
  projects and a checkout/test framework. It is a credible secondary option,
  but its project-specific environments and licensing must be reviewed per
  selected upstream project before a fresh-clone study. Its repository is
  maintained by the SOAR group.
  [Official repository](https://github.com/soarsmu/BugsInPy).
- **Defects4J.** A mature Java benchmark with reproducible buggy/fixed project
  revisions and its own framework. It is not selected for the first CAR study:
  CAR's current coding path and benchmark fixtures are Python-oriented, so a
  Java-first evaluation would confound benchmark adaptation with language
  expansion.
  [Official repository](https://github.com/rjust/defects4j).
- **SWE-bench Live.** A useful future contamination-resistant complement: its
  paper describes newer issue tasks and dedicated containers. It is not the
  first target because the current CAR benchmark harness has no Live adapter
  and the protocol needs a separate compatibility review.
  [SWE-bench Live paper](https://arxiv.org/abs/2505.23419).

## Comparison table

| Dataset | Maintainer / availability / license | Tasks and language | Native verification and agent-visible tests | Setup, offline execution, Windows | Selection and leakage notes |
| --- | --- | --- | --- | --- | --- |
| SWE-bench Verified | SWE-bench; public Hugging Face dataset and GitHub harness; MIT | 500 human-verified Python issue tasks | Official SWE-bench Docker evaluator. Base-repository tests may be visible under the native checkout contract; issue-specific `test_patch` and gold `patch` must be evaluator-only. | Linux Docker images. On Windows, Docker Desktop with WSL2 Linux containers is the faithful path. Cached repos/images permit offline evaluation after setup. | Stable `instance_id`, `repo`, and `base_commit`; gold patch and test patch are present in dataset records and require strict isolation. |
| SWE-bench Lite | SWE-bench; public; MIT | 534 Python issue tasks | Same evaluator family; less strong human-validation signal | Same Docker/WSL2 constraints | Reproducible IDs; use only as adapter smoke set, not outcome selection. |
| BugsInPy | SOAR / public repository; upstream project licenses must be recorded per selected project | 493 bugs across 17 Python projects | BugsInPy checkout and test tooling; test visibility depends on the selected project | Project-specific Python/dependency environments; Docker helpful but less uniform | Bug IDs are reproducible; per-project setup and licensing increase protocol variance. |
| Defects4J | University of Paderborn / public framework; framework and upstream licenses require recording | 854 active Java bugs in the current framework line | Defects4J test framework | Java/Linux-oriented; Windows is not the preferred faithful route | Reproducible bug IDs, but language mismatch would measure a new CAR capability. |
| SWE-bench Live | SWE-bench Live authors / public research release; license and exact release revision must be verified before acquisition | 1,319 newer tasks reported across 93 repositories | Dedicated containers described by the paper | Linux containers; additional protocol work required | Better freshness objective, but an unimplemented adapter and release review make it a follow-up validation. |

Counts and the SWE-bench record fields are frozen from the official dataset
guide at acquisition time; the eventual run must record the exact dataset and
harness revisions rather than relying on these planning figures.

## Recommended dataset

**SWE-bench Verified** is the primary dataset for the first external CAR
validation: a 24-instance, pre-registered stratified sample from the official
`SWE-bench/SWE-bench_Verified` test split.

It provides real issue text, public repositories, pinned base commits,
human-reviewed solvability, and a recognized authoritative evaluation harness.
It is therefore a much stronger initial external comparison than a synthetic
fixture set while retaining a feasible Python-first adapter path. This is an
external validation set, **not** proof that tasks were unseen during a model's
pretraining; public-GitHub contamination remains a reported limitation of the
SWE-bench family. A later SWE-bench Live study is the appropriate complementary
freshness/contamination test.

## Why it is methodologically stronger than the internal pilot

The internal pilot has CAR-authored task fixtures, authorization, and hidden
oracles. SWE-bench Verified instead supplies independently authored issue
statements, upstream repository histories, base commits, and an external
evaluation protocol. Its sample is frozen before any provider execution, and
all strategies receive identical inputs and evaluator behavior. The design
therefore tests generalization beyond CAR's internal task construction without
changing the meaning of verified success.

## Proposed 20–30 task sample

Use **24 tasks**. This is large enough to cover multiple repositories and
official difficulty values while limiting initial Docker-image, provider-quota,
and manual protocol risk. It is a pilot validation sample, not a claim of
population-level statistical certainty.

## Sampling protocol

Before any provider is created or any strategy runs:

1. Pin the Hugging Face dataset revision for `SWE-bench/SWE-bench_Verified`
   and the SWE-bench harness Git commit. Record both in a committed selection
   manifest.
2. Form an eligibility list using only non-solution metadata: `instance_id`,
   `repo`, `base_commit`, `problem_statement`, official `difficulty`, and
   successful local creation of the official environment. Do **not** read
   `patch`, `test_patch`, `FAIL_TO_PASS`, or `PASS_TO_PASS` to select tasks or
   derive scope.
3. Group eligible instances by the official `difficulty` value. Allocate 24
   proportionally, with a minimum of three per non-empty stratum where the
   population permits it. Cap a repository at three selected cases; redistribute
   a capped slot using the same rule.
4. Within every stratum, rank `instance_id` by
   `SHA-256("car-external-v1:" + instance_id)` and take the required prefix.
   This seed and algorithm are the selection rule; no outcome, model response,
   cost, or latency may influence it.
5. Commit a small selection manifest containing only dataset/harness revisions,
   the ordered instance IDs, repositories, base commits, public difficulty, and
   the selection algorithm. It must be committed before the first provider run.

If a selected case cannot build its official environment, retain it as an
ineligible pre-run record and replace it by the next ranked eligible item in
the same stratum. Record the reason before provider execution.

## Agent-visible information

Every strategy receives the identical public issue `problem_statement`, the
same base-commit checkout, the same deterministic repository-context selection,
the same model configuration, and the same timeout policy. The external adapter
must define that context selection without consulting solution data.

The target contract should match SWE-bench as closely as CAR safely can:
base-repository source and pre-existing tests may be readable; no test or source
file is writable unless explicitly authorized by the adapter's deterministic,
non-gold scope policy. The dataset `test_patch`, `FAIL_TO_PASS`, `PASS_TO_PASS`,
gold `patch`, evaluator logs, and all post-run diffs are never provider-visible.

## Writable scope

CAR's current benchmark cases require an explicit repository-relative
`authorized_paths` tuple. SWE-bench does not safely supply that scope without
looking at its gold patch, so the external adapter must introduce a documented,
strategy-neutral scope procedure based only on agent-visible information.

The initial design is **not approved to broaden write authorization**. Its Go
criterion is a deterministic scope mechanism that can produce explicit regular
files without gold-patch inspection and without weakening the current controlled
write validation. If such a mechanism cannot be shown faithful and safe, the
external run is No-Go rather than a reason to modify CAR runtime behavior.

## Verification

Verified success is determined by the native SWE-bench evaluator for the
selected pinned harness revision. The evaluator runs in a separate, post-attempt
environment and applies/uses benchmark-held test data there; CAR must not replace
it with a CAR-authored expected answer or hidden oracle.

For each strategy, the adapter captures the final candidate workspace state,
invokes the identical official evaluation command with the same instance and
resource limits, and maps only its structured pass/fail result to the benchmark
report. CAR's own validation and rollback protections remain prerequisites to
producing that candidate state; they do not redefine SWE-bench success.

## Gold-solution isolation

Treat the following SWE-bench fields as evaluator-only sensitive benchmark
assets: `patch`, `test_patch`, `FAIL_TO_PASS`, and `PASS_TO_PASS`.

- Fetch or mount them only inside an evaluator-side store/container, separate
  from provider workspaces and prompts.
- Exclude them from task statements, context builders, repository scanning,
  routing signals, authorized-path construction, telemetry, report JSON, and
  provider logs.
- Do not use changed-file lists, patch size, or test names derived from solution
  artifacts to filter, stratify, or repair an instance.
- Apply the same redaction rule to provider-visible scratch workspaces and to
  Codex final-proposal inputs.

This prevents both direct patch leakage and indirect scope/test leakage. It does
not eliminate public-data model contamination; the report must state that limit.

## Reproduction environment

Use a fresh clone of CAR plus an external cache directory outside the repository:

1. Record CAR commit, `v0.6.0` tag SHA, dataset revision, harness commit,
   selected-ID manifest hash, base commits, Docker image digests, Python version,
   Docker version, host architecture, CPU/RAM allocation, and strategy model IDs.
2. Acquire the official dataset and harness at those pinned revisions. Clone each
   upstream repository at its recorded base commit; never vendor upstream repos
   or Docker layers into CAR.
3. Build/pull official evaluator images before execution. Once the dataset,
   repository clones, Python environments, and images are cached, execute without
   network access where the official harness permits it.
4. Create a fresh isolated workspace for every `(instance_id, strategy)` from the
   same base commit. Preserve the source clone and evaluator assets read-only.
5. Run the three strategies in a pre-recorded order (or a deterministic shuffled
   order based only on the selection seed), then run the same native evaluator.
   Publish input revisions, task IDs, commands, resource settings, and aggregate
   results without solution patches or raw provider output.

## Platform requirements

SWE-bench's official evaluation is containerized and is most faithfully run on
Linux/x86_64. On the current Windows development host, use Docker Desktop with
the WSL2 backend and Linux containers, preferably through an Ubuntu WSL2
environment. Native Windows execution is not the recommended protocol.

The SWE-bench README recommends at least 16 GiB RAM, eight CPU cores, and
approximately 120 GiB of free disk for Docker evaluation. These are minimum
planning constraints, not a promise that every selected task needs that amount.
[Official README](https://github.com/SWE-bench/SWE-bench/blob/main/README.md).

## Estimated runtime/storage

Reserve **at least 120 GiB** free storage for Docker images, cloned repositories,
dataset cache, and isolated workspaces, following the official harness guidance.
Use one worker for the first external pilot to maximize reproducibility and avoid
host contention; no provider is run until two representative official-environment
dry builds have recorded their image-build and evaluation durations.

An honest wall-clock estimate is not yet empirical for this host. Budget a full
day for 24 serialized environments/evaluations after image preparation, and
record measured per-instance setup and evaluator time in the run metadata. Do not
publish a throughput or cost claim until that measurement exists.

## Required CAR adapter work

Classification: **B — generic benchmark harness capability**, not a CAR runtime
change.

The existing local-fixture benchmark runner already provides isolated workspaces,
strategy execution, structured result accounting, and explicit path/verification
boundaries. It does not yet provide:

- a pinned external-dataset acquisition/selection manifest;
- base-commit checkout preparation for upstream repositories;
- a provider-visible context and explicit writable-scope adapter that is
  deterministic and independent of gold data;
- an evaluator-side, gold-isolated invocation of the native SWE-bench harness;
- external-environment provenance in the benchmark report.

Implement these as an adapter around the benchmark layer only. Do not alter
routing, provider behavior, prompts, patch validation, safe application,
authorization, economics, telemetry, controlled-write safety, or model settings.
A need to relax existing authorization or verification invariants is a **C — CAR
runtime change** blocker requiring explicit approval.

## Risks and limitations

- SWE-bench Verified is public and can be contaminated by training data; it
  cannot establish model-unseen performance. SWE-bench Live is the planned
  complementary freshness study.
- Native Docker environments have substantial storage, Linux/WSL2, and setup
  requirements. Some pre-selected tasks can fail environment construction.
- Current CAR requires explicit file scope; a safe scope adapter is the main
  methodological and implementation risk.
- A 24-task pilot has limited precision and should report per-task outcomes,
  uncertainty, failures, and no causal claims from small differences.
- Provider availability/quota failures must be reported separately from native
  verification failures and must not trigger sample replacement after execution.
- The external test patch must remain hidden even if public in the dataset;
  isolation protects benchmark protocol, not training-data contamination.

## Go / No-Go criteria

**Go** only when all of the following are documented and tested in an
adapter-only branch before live providers:

- exact official dataset and harness revisions are pinned;
- the 24 IDs are selected and committed by the stated pre-provider algorithm;
- every selected repository is pinned to its public base commit;
- official Docker environments build and run for two non-provider dry cases in
  the WSL2/Linux configuration;
- the provider-visible context and explicit writable scope are reproducible,
  equal across strategies, and demonstrably independent of gold data;
- the native evaluator is isolated from provider workspaces and gives the same
  authoritative success rule to all strategies;
- no gold fields, absolute paths, credentials, raw patches, or evaluator logs
  enter provider inputs or public reports;
- all model IDs, timeouts, cost semantics, and CAR runtime behaviors remain
  frozen at the recorded CAR commit.

**No-Go** if the adapter would require inspecting gold patches for scope,
loosening controlled-write authorization, replacing the native evaluator,
changing CAR runtime behavior, or running a materially different contract for
one strategy. In that event, stop and obtain explicit approval rather than tune
or modify CAR.
