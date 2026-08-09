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
