# CAR — Capability Agent Router

Route software-engineering tasks to the least expensive capability that can
solve them safely.

```text
Deterministic tools → Gemini → Codex
```

CAR is an orchestration and routing layer for software-engineering capabilities,
not another coding model, chat wrapper, or language-only router. It considers
task type, complexity, scope, risk, deterministic evidence, and optional
provider evidence before selecting a route.

## Project status: Alpha

CAR is under active development. The routing-intelligence layer is usable, while
L1/L2 coding execution is still being implemented.

## Routing architecture

```text
Task
 │
 ▼
Deterministic Analysis
 ├── L0 authoritative
 ├── Hard risk → Codex authoritative
 ▼
Lazy Provider Consultation
 ▼
Gemini Classification
 ▼
Conservative Fusion
 ▼
Final Route: L0 | Gemini | Gemini → Codex | Codex | Plan
```

Provider evidence may escalate an eligible automatic route, but cannot downgrade
it. Gemini, Gemini-to-Codex, and Codex routes are ordered conservatively.

## Quick start

```powershell
git clone https://github.com/Gabriele30/capability-agent-router.git
cd capability-agent-router

python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

car init
car status
car analyze "Fix parser regression"
car providers
```

## Examples

```powershell
# Deterministic L0 candidate; Gemini is not consulted.
car analyze "Format car/router/engine.py"

# Hard risk; Codex is selected without provider consultation.
car analyze "Fix authentication bypass"

# Provider consultation is optional and fusion remains conservative.
car analyze "Fix parser regression"

# Only a final L0 route may currently execute.
car task "Format car/router/engine.py"

# Explicitly preview a scoped coding attempt. Confirmation defaults to no.
car execute "Fix parser regression" --file car/parser.py --verify pytest

# --yes authorizes only this invocation; Codex fallback remains off.
car execute "Fix parser regression" --file car/parser.py --verify pytest --yes

# Optional fallback is diagnostic and read-only.
car execute "Fix parser regression" --file car/parser.py --verify pytest --yes --codex-analysis
```

`car analyze` is read-only with respect to the repository, even when Gemini is
configured for classification.

## Gemini configuration

Gemini classification is disabled by default. To opt in, set
`providers.gemini.enabled` to `true`, configure `providers.gemini.model` in
`.car-context/config.json`, and make its configured credential environment
variable available:

```powershell
$env:GEMINI_API_KEY="<your-key>"
```

CAR never persists the API key value. `car providers` reports local-only status:
it does not send a Gemini request or invoke a Codex process.

## Execution capabilities

| Capability | Status |
| --- | --- |
| L0 deterministic execution | Implemented and verified |
| Gemini classification | Implemented, optional |
| Scoped Gemini coding execution | Explicit preview, authorization, verification, and rollback |
| Codex routing | Implemented |
| Codex coding execution | Not implemented; read-only diagnostics only |
| Gemini → Codex failure handoff | Optional in-memory read-only diagnostic fallback |

`car execute` may modify only files selected with `--file`, and CAR retains a
change only after selected CAR-controlled verification checks pass. Failed
verification is rolled back. Execution requires per-invocation confirmation (or
`--yes`), which is never persisted. `--codex-analysis` enables only a read-only
diagnostic fallback and does not allow Codex to fix files.

Codex is currently a routing target, not an executable CAR provider. Future
Codex execution will use the locally installed Codex runtime authenticated by the
user's existing Codex/ChatGPT session; CAR will not require an OpenAI API key.

## Safety

CAR uses structured routing and provider evidence, preserves user changes during
L0 work, verifies deterministic changes, and rolls back failed L0 operations.
It does not use `git reset --hard` for recovery. Provider failures degrade to the
deterministic route, and Gemini credentials stay environment-only.

## Development

```powershell
ruff check .
ruff format --check .
pytest
```

Standard tests are offline-first. Live Gemini validation is explicit opt-in and
is not enabled in CI. The end-to-end `car execute` coding-flow validation requires
`CAR_RUN_LIVE_CODING_FLOW_TESTS=1` and uses a synthetic temporary repository.

## Roadmap

- Current: 0.4 — routing intelligence.
- Next: broaden explicit coding execution coverage while keeping verification,
  rollback, and read-only Codex diagnostics under user authorization.
- Later: repository memory, deeper retrieval, VS Code integration, telemetry,
  adaptive routing, and verifiability-aware routing.
