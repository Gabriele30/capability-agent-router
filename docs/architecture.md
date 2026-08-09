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

## Planned

The provider foundation defines provider-neutral classification contracts and a
local-only Gemini configuration health check. Live Gemini classification is not
implemented yet; no provider requests or repository source uploads occur.

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
