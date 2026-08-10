# CAR agent guidance

Project: CAR — Capability Agent Router

## Priorities

1. Correctness
2. Safety
3. Maintainability
4. User control
5. Minimizing unnecessary frontier-agent usage
6. Performance

## Engineering rules

- Keep modules small and focused.
- Prefer typed Python.
- Use Pydantic for domain models crossing module boundaries.
- Do not introduce provider-specific dependencies into the router core.
- Preserve provider abstraction.
- Never overwrite user changes destructively.
- Never use `git reset --hard` as an automatic recovery mechanism.
- Prefer deterministic mechanisms over LLM calls when possible.
- Every automated modification must eventually have a verification path.
- Secrets must never be logged.
- Keep Windows compatibility and use `pathlib` for filesystem access.
- Add or update tests for meaningful behavior changes.
- Do not implement future milestones unless explicitly requested.
- Keep CLI presentation separate from repository and routing domain logic.
- Codex integration must use the locally installed Codex runtime authenticated
  through the user's existing ChatGPT account; do not require an OpenAI API key
  or read/manage Codex credentials.
- Gemini is the initial auxiliary L1 provider. Keep the router core provider-
  independent; Gemini classification is an additional signal, not sole authority.
- Provider SDK dependencies must remain isolated behind adapters; router core
  modules must not import Gemini/OpenAI SDKs or persist provider credentials.
- Gemini is advisory and never authoritative over hard routing rules.
- Provider evidence may only conservatively escalate an eligible automatic route;
  it must never downgrade L0, hard-rule, or explicit user decisions.
- Provider confidence is a routing heuristic, not a calibrated probability.
- Provider risk is reported conservatively but must not independently change a route.
- Construct concrete providers only in application or CLI composition layers;
  never inside the deterministic router, fusion engine, or consultation gate.
- `car analyze` may consult a configured classifier but must remain read-only
  with respect to the repository. `car task` wiring changes require an explicit milestone.
- `car task` must consume the shared routing evaluation and may write only when
  its final route is L0. Provider classification is advisory, never coding execution.
- Provider status commands are local-only; never probe provider networks or
  external Codex authentication merely to display status.
- Keep coding-provider proposals separate from classification and routing.
  Providers propose structured code-change data only; CAR owns validation,
  application, verification, rollback, and all command selection.
- Never apply a coding proposal or execute provider-suggested commands without
  an explicitly implemented, CAR-controlled safe-apply milestone.
- Live provider tests must remain explicit opt-in and never run in standard CI.
- L0 may execute only CAR-owned, allowlisted command templates with structured
  arguments and `shell=False`; never execute command text from a user or agent.
- Capture a byte-preserving snapshot before L0 writes. On execution,
  verification, or scope failure, restore the snapshot; never use Git reset,
  checkout, or restore as automated rollback.
