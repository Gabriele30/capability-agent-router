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

## Implemented now

Milestone 2 implements the CLI boundary, Pydantic domain models, local CAR
configuration, deterministic repository intelligence, task analysis, risk and
scope heuristics, and the decision engine. `car analyze` and `car task` decide
a route but deliberately do not execute it. The decision includes rules and
reasons for explainability; confidence is heuristic rather than a model
probability.

## Planned

Future milestones may add L0 execution, auxiliary-agent and Codex integrations,
verification, and escalation. Gemini is the initial auxiliary L1 provider, but
its classification will be an additional signal rather than the sole routing
authority. The router core remains provider-independent.

Codex integration will use the locally installed Codex runtime authenticated
through the user's existing ChatGPT account. CAR must not require an OpenAI API
key for Codex, and must not read or manage Codex authentication credentials.
