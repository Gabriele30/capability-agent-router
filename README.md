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

## Deterministic routing (Milestone 2)

Routing is currently deterministic and explanatory; no agents are executed.

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

## Project status

Milestone 2 adds the deterministic routing core on top of the CLI foundation
and repository intelligence. It does not call auxiliary agents, Codex, or any
LLM.

## Short roadmap

1. Foundation and repository intelligence (current)
2. Routing policy and provider interfaces
3. Verification and controlled execution
