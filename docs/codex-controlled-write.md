# Controlled Codex Write Design

This document defines the trust boundary for future Codex coding. It does not
enable Codex writing in CAR 0.5.x.

## Boundary and strategy

Codex is an untrusted code-modification producer. Future coding runs will use an
isolated temporary Git worktree derived from a captured repository baseline, not
the user's working tree. The isolated workspace excludes `.car-context` and is
removed independently of the user repository. CAR will derive the exact filesystem
delta from that workspace; Codex text is diagnostic evidence, never the authority
for changed files.

The future apply boundary receives only a validated `CodexChangeSet`. CAR retains
authorization, allowed scope, protected paths, verification, finalization, and
rollback decisions. The existing read-only `LocalCodexRuntime` is unchanged; a
separate explicit runtime and policy boundary will be required for controlled
coding.

## Contracts and defaults

`CodexWritePolicy` and `CodexWriteAuthorization` default to disabled/false.
Current contracts represent baseline identities and exact deltas without absolute
paths or file contents. The first write path may accept only text `MODIFY` and
`CREATE`; `DELETE` and `RENAME` are blocked. Protected paths include existing CAR
patch protections (`.git`, `.env`, `.car-context`); traversal, absolute paths,
unsafe symlinks, binaries, oversized files, and excessive change counts fail
closed.

## Baseline, dirty state, and concurrency

The baseline records repository-relative file identities, tracked/untracked and
dirty metadata, plus staged and untracked paths. A dirty repository is supported:
the isolated workspace is derived from the precise baseline, so pre-existing user
state is not confused with Codex output. Before any future real-repository apply,
CAR compares a fresh baseline. A mismatch is `CONCURRENT_MODIFICATION` and blocks
the apply; CAR never overwrites user changes silently.

## Verification and recovery

Future verification must reuse `VerificationEngine`, `CommandSpec`, the existing
allowlist, and child-process workspace hygiene. Codex generation cannot affect the
user repository, so abandoning an isolated workspace is its rollback. Only a later
CAR-controlled application of an accepted change set may use the existing
transactional snapshot/apply/verification/rollback mechanisms.

Timeouts, crashes, partial output, unexpected deltas, Git metadata changes, and
external-process side effects fail closed. No credentials are copied deliberately;
the isolated workspace lifecycle is bounded and removable without touching the user
repository. A successful Codex process is not task success: changes must be
detected, accepted, verified, and finalized.

## Planned sequence

1. **5E2 — Isolated Codex Workspace Foundation**
2. **5E3 — Controlled Codex Coding Execution in Isolation**
3. **5E4 — Exact Delta Extraction and Validation**
4. **5E5 — CAR-controlled Apply, Verification, and Rollback**
5. **5E6 — Explicit CLI Authorization and End-to-End Flow**
