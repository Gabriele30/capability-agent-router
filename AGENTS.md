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
  untrusted output path; it must also exclude snapshot and environment dumps.
  Handoff persistence is explicit and fixed to `.car-context`.
- The local Codex runtime uses only the installed Codex CLI and its existing
  authentication. It must never read credentials or auth files, request an API key,
  or perform automatic login. Its initial execution mode is ephemeral and read-only.
- Live Codex runtime validation is opt-in only through `CAR_RUN_LIVE_CODEX_TESTS=1`.
  Standard tests must never invoke Codex; the live test uses only a synthetic Git
  repository and synthetic handoff, with byte-level workspace checks after execution.
- Application-layer Codex execution requires an explicit `CodexExecutionPolicy` and
  is disabled by default. It forwards an in-memory handoff to the read-only runtime;
  it must not parse Markdown, persist handoffs, inspect credentials, or wire `car task`.
- The post-failure coordinator may invoke that service only for an existing,
  authorized `EscalationDecision` targeting Codex and a non-uncertain `CodexHandoff`.
  It must fail closed without runtime calls for all other states.
- Verified-outcome composition must only consume existing structured evidence. It must
  not rerun verification, apply patches, call Gemini, persist a handoff, or wire the
  read-only Codex flow into `car task`.
- The internal coding pipeline may compose exactly one eligible, injected coding
  provider proposal with CAR-owned validation, safe application, and verification.
  It must remain separate from `car task`, Codex escalation, persistence, and command
  selection; only the patch applier may write target files after validation.
- Coding-pipeline execution requires an explicit application policy and is disabled
  by default. This gate may delegate once to the internal pipeline but must not add
  retries, fallbacks, Codex escalation, automatic planning, persistence, or CLI wiring.
- The internal coding flow may pass only already-verified failed coding evidence to
  the existing post-failure composition. A successful read-only Codex diagnosis is
  never a coding-task success and must not trigger a second patch, retry, persistence,
  or CLI action.
- Any caller of the internal coding flow must supply an explicit, runtime-only user
  authorization. Authorization defaults to false, is never model-derived or persisted,
  and cannot weaken validation, verification, rollback, sandbox, or execution policies.
- `car execute` is the only public coding-flow entry point. It must show a preview,
  require explicit per-invocation consent, require selected regular files and a
  CAR-controlled verification preset, and leave `car task` unchanged. Codex analysis
  remains an independently opted-in read-only diagnostic fallback.
- Live CLI coding-flow validation is opt-in only through
  `CAR_RUN_LIVE_CODING_FLOW_TESTS=1`. It uses a synthetic temporary Git repository,
  existing local Gemini configuration, and the real `car execute` path; standard CI
  must never enable it.
- Live Gemini-failure-to-Codex escalation validation is independently opt-in through
  `CAR_RUN_LIVE_CODING_ESCALATION_TESTS=1`. It uses a synthetic Git repository and
  requires locally configured Gemini plus a ready locally authenticated Codex CLI;
  Codex remains ephemeral and read-only.
- Controlled Codex write is experimental and disabled by default. It may be selected
  only after an eligible verified Gemini-to-Codex failure, with a separate enabled
  policy, invocation-local write authorization, explicit repository-relative file
  scope, and a non-empty CAR-controlled verification plan. Routing and read-only
  Codex authorization never imply write consent.
- Controlled Codex write must remain confined to a CAR-owned isolated workspace. CAR
  must validate the complete delta, apply permitted bytes transactionally, verify and
  recheck source integrity, and finalize before reporting acceptance. Never add a
  read-only-to-write chain, direct CODEX write, broad scope, or automatic retry.
- The public controlled-write live test is opt-in only through
  `CAR_RUN_LIVE_CLI_CONTROLLED_CODEX_TESTS=1`; standard CI must keep it skipped.
- L0 may execute only CAR-owned, allowlisted command templates with structured
  arguments and `shell=False`; never execute command text from a user or agent.
- Capture a byte-preserving snapshot before L0 writes. On execution,
  verification, or scope failure, restore the snapshot; never use Git reset,
  checkout, or restore as automated rollback.
