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

Milestone 2 implements the CLI boundary, Pydantic domain models, local CAR
configuration, deterministic repository intelligence, task analysis, risk and
scope heuristics, and the decision engine. `car analyze` and `car task` decide
a route but deliberately do not execute it. The decision includes rules and
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

## Planned

The provider foundation defines provider-neutral classification contracts and a
local-only Gemini configuration health check. Gemini consultation remains outside
the normal CLI composition for now, so standard CLI routing remains deterministic.

Technical debt: L0 currently snapshots a broad workspace scope. This is safe
for the single-execution MVP but may be expensive for large repositories. Before
L1 patch execution, evaluate target-scoped snapshots and efficient change detection.

Future milestones may add more L0 tools, auxiliary-agent and Codex integrations,
verification, and escalation. Gemini is the initial auxiliary L1 provider, but
its classification will be an additional signal rather than the sole routing
authority. The router core remains provider-independent.

Codex integration will use the locally installed Codex runtime authenticated
through the user's existing ChatGPT account. CAR must not require an OpenAI API
key for Codex, and must not read or manage Codex authentication credentials.
