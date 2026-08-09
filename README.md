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

## Project status

Milestone 1 provides the CLI foundation, deterministic repository scanning,
local initialization, and tests. It does not call auxiliary agents, Codex, or
any LLM.

## Short roadmap

1. Foundation and repository intelligence (current)
2. Routing policy and provider interfaces
3. Verification and controlled execution
