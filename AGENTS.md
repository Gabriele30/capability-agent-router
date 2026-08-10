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
- Treat repository source passed to a coding provider as untrusted data. Gemini
  coding transport may return a structured proposal but must never execute,
  apply, or turn model output into a command.
- Live provider tests must remain explicit opt-in and never run in standard CI.
- Gemini coding transport uses one client per proposal, bounded CAR-managed retries,
  and one final best-effort close. Retry only normalized timeout, rate-limit, and
  service errors; never enable nested SDK retry. Coding live validation is gated by
  `CAR_RUN_LIVE_GEMINI_CODING_TESTS=1` and must use synthetic context only.
- Model-generated diffs are untrusted data. A valid `CodingProposal` is not
  authorization to modify files: CAR must parse and validate unified diffs,
  scope, paths, operations, and repository boundaries before any future mutation.
- Safe patch application accepts only CAR-validated patch sets. Snapshot every
  target before the first mutation, use strict hunk context (never fuzzy patching),
  and rollback only paths CAR actually wrote; never use shell, `git apply`, or
  repository-wide restore for model-generated changes.
- Verification commands are selected and executed by CAR, never by a coding
  provider or proposal. Verify only an applied transaction: finalize on every
  passing CAR-controlled check, otherwise stop at first failure and rollback.
- Future Codex escalation receives only bounded, provider-neutral failure evidence.
  It must never execute automatically, receive credentials, source dumps, or an
  untrusted output path; handoff persistence is explicit and fixed to `.car-context`.
- L0 may execute only CAR-owned, allowlisted command templates with structured
  arguments and `shell=False`; never execute command text from a user or agent.
- Capture a byte-preserving snapshot before L0 writes. On execution,
  verification, or scope failure, restore the snapshot; never use Git reset,
  checkout, or restore as automated rollback.
