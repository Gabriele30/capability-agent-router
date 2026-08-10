# Architecture

CAR's target architecture is deliberately capability-aware:

```text
USER
 ↓
CAR
 ↓
Repository Intelligence
 ↓
Decision Engine
 ├── L0: deterministic tools
 ├── L1: auxiliary agent
 └── L2: Codex
 ↓
Verification
 ↓
Result / Escalation
```

For an executable L0 decision, the current flow is:

```text
Task
 ↓
Router
 ↓
Decision (L0)
 ↓
Execution Plan
 ↓
Safety
 ↓
Snapshot
 ↓
Execute
 ↓
Verify
 ├── PASS → DONE
 └── FAIL → ROLLBACK
```

## Implemented now

Milestone 2 established the CLI boundary, Pydantic domain models, local CAR
configuration, deterministic repository intelligence, task analysis, risk and
scope heuristics, and the decision engine. The decision includes rules and
reasons for explainability; confidence is heuristic rather than a model
probability.

Milestone 3 adds reusable `ExecutionPlan`, `CommandRunner`,
`VerificationEngine`, and `WorkspaceSnapshot` infrastructure. Only internally
constructed, allowlisted Ruff format/lint-fix commands for explicit Python
files may execute. Commands use structured arguments and no shell. Snapshot
rollback preserves pre-existing file bytes and never uses Git reset/restore.

Milestone 4C adds an optional, provider-neutral consultation and fusion layer.
The deterministic decision is always retained as evidence. A successful provider
classification can conservatively escalate only along `GEMINI → GEMINI_TO_CODEX
→ CODEX`, provided its confidence meets the configured heuristic threshold
(default `0.70`). Provider evidence cannot downgrade a route; `PLAN` suggestions
do not alter an implementation route; L0, hard rules, and explicit user modes
skip consultation. The final reported risk is `max(deterministic, provider)` when
provider evidence succeeds, but risk alone never changes the route.

Milestone 4D1 wires that evaluation into `car analyze` through a small
application composition layer. It reads the local CAR configuration, constructs
the Gemini adapter, and injects it into the provider-neutral consultation gate.
The normal CLI remains safe when Gemini is disabled, incomplete, or unavailable;
L0, hard rules, and explicit modes do not call the provider. `car analyze` is
read-only with respect to the workspace, and Gemini remains classification-only.

Milestone 4D2-A makes `car task` consume the same `RoutingEvaluation`. Only a
final L0 route enters the existing execution and verification pipeline. All
other routes report that coding execution is not implemented. `car providers`
reports local-only Gemini configuration health and the planned external Codex
runtime boundary; it does not make network calls or invoke Codex.

## Release 0.4.0 status

CAR 0.4.0 is an alpha Routing Intelligence release. It implements deterministic
routing, verified L0 execution, optional Gemini classification, lazy consultation,
and conservative fusion. Gemini/Codex coding execution and Gemini-to-Codex
handoff remain planned; the current provider integration is classification-only.

Milestone 5C2-A adds an isolated local Codex runtime foundation for future handoff
diagnostics. It renders an existing `CodexHandoff` to stdin and invokes the installed
Codex CLI with `codex exec --ephemeral`, a read-only sandbox, and no interactive
approval. CAR does not own Codex credentials: authentication remains delegated to the
installed CLI, no OpenAI API key is required, and credential files are never read.
This foundation returns only a bounded final diagnostic message; it does not modify
the workspace, persist a handoff automatically, apply patches, or wire execution to
the CLI.

An integration validation is available only with `CAR_RUN_LIVE_CODEX_TESTS=1`. It
uses a synthetic temporary Git repository and a synthetic handoff to exercise the
same read-only runtime transport. Standard test runs never invoke Codex, and the
live validation neither logs in nor requires an API key.

Milestone 5C2-C adds an explicit application service above the runtime:

```text
CodexHandoff
  ↓
CodexExecutionService
  ↓
explicit disabled-by-default policy
  ↓
runtime health
  ↓
LocalCodexRuntime (read-only)
```

The service receives the structured in-memory handoff and an injected runtime; it
does not parse Markdown or persist `.car-context` artifacts. A ready local runtime
is required before one execution can occur. This remains separate from `car task`,
does not retry or alter runtime sandbox settings, and cannot enable workspace writes.

Milestone 5C3 adds a narrow post-failure coordinator:

```text
EscalationDecision + CodexHandoff
  ↓
Read-only Codex escalation coordinator
  ↓
CodexExecutionPolicy
  ↓
CodexExecutionService
  ↓
LocalCodexRuntime (read-only)
```

It accepts only an existing authorized decision targeting Codex and blocks uncertain
workspace evidence as defense in depth. It does not build handoffs, re-evaluate
routing, parse Markdown, persist artifacts, or wire escalation into `car task`.

Milestone 5C4 composes an already verified coding outcome without repeating any
earlier work:

```text
Verified coding outcome
  ↓
Post-failure preparation
  ↓
EscalationDecision + CodexHandoff
  ↓
Post-failure pipeline coordinator
  ↓
Read-only Codex escalation coordinator
  ↓
CodexExecutionService → LocalCodexRuntime
```

It consumes structured evidence only. It does not call Gemini, apply a new patch,
rerun verification, persist `.car-context`, or connect the flow to `car task`; Codex
remains read-only throughout.

Milestone 5D1-A adds a narrow internal composition boundary for an already eligible
Gemini route:

```text
RoutingEvaluation (GEMINI or GEMINI_TO_CODEX)
  ↓
one injected CodingProvider proposal
  ↓
PatchValidator → SafePatchApplier → Applied Transaction
  ↓
CodingVerificationCoordinator
  ├── PASS → finalize
  └── FAIL → rollback
```

It exposes only structured attempt, validation, apply, and verification evidence.
It does not select files, generate plans, modify CLI behavior, invoke Codex, persist
artifacts, or turn provider output into a command. The transaction and snapshot
ownership remain entirely inside the existing safe-patch boundary.

Milestone 5D1-B adds the explicit authorization boundary above that internal flow:

```text
Explicit CodingPipelineExecutionPolicy (disabled by default)
  ↓
CodingPipelineExecutionService
  ↓
Internal Coding Pipeline
  ↓
proposal → validate → apply → verify
  ├── PASS → finalize
  └── FAIL → rollback
```

The service returns the nested structured pipeline result and delegates at most once
when explicitly enabled. It does not plan verification, select files, retry, fall
back, persist state, change CLI behavior, or escalate failures to Codex.

Milestone 5D1-C composes an actual verified coding failure with the existing
post-failure and read-only Codex boundaries:

```text
Explicit coding policy → Gemini coding pipeline
  ├── PASS → coding task done
  └── FAIL → rollback → post-failure evidence
                       ├── no authorized escalation → stop
                       └── GEMINI_TO_CODEX → Codex read-only diagnostic
```

Only verified failure evidence enters this path. A successful Codex diagnostic does
not mean that the coding task is fixed: its top-level result remains unsuccessful,
the workspace remains rolled back, and no new patch is requested or applied.

## Planned

Future work may add more L0 tools, Gemini coding execution, Codex handoff and
runtime integration, and verification-driven escalation. Provider classification
will remain advisory rather than the sole routing authority.

The future coding boundary is deliberately separate from routing:

```text
Routing
 ↓
CodingProvider
 ↓
Structured CodingProposal
 ↓
future Safe Apply
 ↓
future Verification
```

A coding provider proposes changes; it never directly writes repository files.
CAR owns validation, application, verification, and rollback.

The Gemini coding adapter sends only an already-selected `CodingTaskContext` to
the Interactions API and receives a locally validated `CodingProposal`. Repository
source is untrusted provider input, and no model output is executed or applied by
the transport layer.

The coding transport uses a single client for each proposal and performs bounded,
CAR-managed retries only for normalized timeout, rate-limit, and service errors.
It always makes one final best-effort client close. An optional live coding test is
separately gated by `CAR_RUN_LIVE_GEMINI_CODING_TESTS=1`; it sends synthetic context
only and stops after proposal validation. Patch application, verification, CLI coding
integration, and Codex escalation remain unimplemented.

CAR now has a separate, read-only safe-patch boundary:

```text
CodingProvider
  ↓
CodingProposal
  ↓
Patch Parser
  ↓
Patch Validator
  ↓
ValidatedPatchSet
  ↓
Snapshot
  ↓
Safe Apply
  ↓
Applied Transaction
  ↓
[future verification]
  ├── PASS → finalize
  └── FAIL → rollback
```

Model-generated diffs are untrusted data. A valid `CodingProposal` is not
authorization to mutate a repository. CAR accepts only a strict text unified-diff
subset for CREATE and MODIFY, checks selected-file authorization, paths, symlinks,
protected locations, target existence, size and hunk limits, and returns structured
violations. Parsing and validation never write files, invoke tools, or contact a
provider. Default limits are 10 files, 64 KiB per patch, 256 KiB in total, and
100 hunks per file.

Safe application starts only from `ValidatedPatchSet`, snapshots all targets in
memory before the first write, and rechecks targets immediately before mutation.
It uses strict hunk context with no fuzzy matching; a partial failure restores only
files CAR actually modified or created. The controlled Python implementation does
not invoke a shell, `git apply`, or external patch tool. A successful transaction
retains its in-memory rollback handle for future verification. The first apply
implementation accepts UTF-8 text with a uniform LF or CRLF style and preserves
that style for MODIFY; CREATE uses UTF-8 LF. Verification itself, CLI coding
integration, and provider-triggered application remain planned.

Applied coding transactions are finalized only through CAR-controlled verification:

```text
CodingProposal → Patch Validation → Safe Apply → Applied Transaction
                                               ↓
                                        CAR Verification
                                        ├── PASS → finalize
                                        └── FAIL → rollback
```

The coding model never supplies executable verification commands. CAR accepts a
repository-scoped plan of allowlisted structured commands, runs checks in order, and
stops after the first failure. Empty, timeout, unavailable-executable, and non-zero
checks all fail closed and trigger target-scoped rollback. Verification currently has
no CLI coding integration or escalation path.

For a future `GEMINI_TO_CODEX` escalation, CAR can retain bounded evidence after
verification failure and rollback: routing, proposal summary, attempted diffs,
executed checks, and rollback status form a `CodexHandoff`. The handoff is data only
and may be explicitly written to `.car-context/current-task.md`; it does not invoke
Codex. Evidence is bounded and excludes repository roots, source contents, snapshot
state, environment values, and credentials. A rollback failure marks the workspace
uncertain and blocks automatic future escalation.

Technical debt: L0 currently snapshots a broad workspace scope. This is safe
for the single-execution MVP but may be expensive for large repositories. Before
L1 patch execution, evaluate target-scoped snapshots and efficient change detection.

The router core remains provider-independent.

Codex integration will use the locally installed Codex runtime authenticated
through the user's existing ChatGPT account. CAR must not require an OpenAI API
key for Codex, and must not read or manage Codex authentication credentials.
