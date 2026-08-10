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

Technical debt: L0 currently snapshots a broad workspace scope. This is safe
for the single-execution MVP but may be expensive for large repositories. Before
L1 patch execution, evaluate target-scoped snapshots and efficient change detection.

The router core remains provider-independent.

Codex integration will use the locally installed Codex runtime authenticated
through the user's existing ChatGPT account. CAR must not require an OpenAI API
key for Codex, and must not read or manage Codex authentication credentials.
