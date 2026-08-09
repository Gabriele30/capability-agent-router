# CAR — Capability Agent Router

Capability-aware orchestration for software engineering agents.

CAR is an early-stage tool for choosing an appropriate capability layer for a
software-engineering task:

```text
L0 → deterministic tools
L1 → auxiliary agents
L2 → frontier agent
```

Its guiding principle is: **use the least capable layer that can reliably
complete the task, without sacrificing correctness.** CAR complements Codex;
it does not replace it.

## Requirements

- Python 3.12 or newer
- Git (for repository-aware commands)

## Installation

```bash
pip install -e ".[dev]"
car --help
```

## Commands

Initialize CAR's local state in a Git repository:

```bash
car init
```

Inspect repository intelligence:

```bash
car status
```

Acquire a task (routing is intentionally not implemented yet):

```bash
car task "Fix Docker healthcheck"
```

## Development

```bash
ruff check .
ruff format --check .
pytest
```

## Deterministic routing and L0 execution

`car analyze` now composes deterministic routing with an optional Gemini
classification consultation when Gemini is explicitly configured. Gemini is
classification-only: it never edits the workspace, runs commands, or replaces
hard routing rules. A provider result may conservatively escalate an automatic
route, while `car task` retains its existing execution behavior and Codex
execution is not wired yet.

`car task` uses that same routing evaluation. Only a final L0 route can execute
the existing verified deterministic capability; Gemini, Gemini-to-Codex, Codex,
and PLAN routes currently report their execution as unavailable.

```bash
car analyze "Fix CSS spacing"
car analyze "Fix authentication bypass"
car analyze "Format src/app.py"
car analyze "Fix parser regression" --json
```

The router recognizes explicit modes (`auto`, `gemini`, `gemini_to_codex`,
`codex`, and `plan`), clearly deterministic formatter/lint tasks, and a small
set of high-risk domains. Its confidence is a rule-based heuristic, not a model
probability. Gemini and Codex routes are decisions only in this milestone.

CAR can now execute small, verified L0 capabilities: formatting and explicit
Ruff lint-fix requests for an explicit Python file with an already-installed
Ruff binary. It builds a structured plan,
validates an allowlisted command template, snapshots the workspace, executes,
and verifies formatting. Failed execution, verification, or scope checks trigger
byte-preserving rollback rather than Git restoration.

```bash
car analyze "Format car/router/engine.py"
car task "Format car/router/engine.py" --dry-run
car task "Format car/router/engine.py"
car providers
```

L0 never executes command text supplied by the user and does not install tools.
Implemented: deterministic routing, verified L0 execution, Gemini
classification, lazy consultation, conservative fusion, and unified
`analyze`/`task` routing. Gemini coding execution and Codex coding execution
remain unimplemented.

## Project status

Milestone 3 adds verified deterministic L0 formatting and rollback on top of
the routing core. It does not call auxiliary agents, Codex, or any LLM.

## Short roadmap

1. Foundation and repository intelligence (current)
2. Routing policy and provider interfaces
3. Verification and controlled execution
