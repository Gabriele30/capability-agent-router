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

Milestone 1 implements the CLI boundary, Pydantic domain models, local CAR
configuration, and deterministic repository intelligence. Repository scanning
collects Git state, file counts, language counts, and simple project signals.
`car task` validates and records an in-memory task request, but deliberately
does not route it.

## Planned

Future milestones may add a decision engine, provider abstractions, L0 tools,
auxiliary-agent and Codex integrations, verification, and escalation. These
components remain separate so provider-specific dependencies do not enter the
router core.
