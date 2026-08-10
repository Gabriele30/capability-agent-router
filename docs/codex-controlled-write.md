# Controlled Codex Write Design

This document defines the trust boundary for future Codex coding. It does not
enable Codex writing in CAR 0.5.x.

## Boundary and strategy

Codex is an untrusted code-modification producer. Future coding runs use an
isolated temporary Git worktree derived from a captured repository baseline, not
the user's working tree. The isolated workspace excludes `.car-context` and is
removed independently of the user repository. CAR derives the exact filesystem
delta from that workspace; Codex text is diagnostic evidence, never authority for
changed files.

The future apply boundary receives only a validated `CodexChangeSet`. CAR retains
authorization, allowed scope, protected paths, verification, finalization, and
rollback decisions. The existing read-only `LocalCodexRuntime` is unchanged; a
separate explicit runtime and policy boundary is required for controlled coding.

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

## 5E2-A: implemented isolated lifecycle

CAR now creates a temporary, detached Git worktree at the exact resolved `HEAD`
OID using structured Git arguments. The temporary worktree is outside the source
repository and is removed only through `git worktree remove --force` for its
CAR-owned path. CAR then removes only its exact temporary parent directory.

The source worktree is never checked out, stashed, reset, cleaned, committed, or
otherwise changed by this lifecycle. Git's transient worktree metadata may exist
while the isolated workspace is active and is removed with it. This first slice
represents committed `HEAD` only: it deliberately does not project dirty, staged,
untracked, ignored, or `.car-context` state into the detached worktree.

No Codex process, provider, network call, delta extraction, patch validation,
application, verification, or rollback is enabled by this lifecycle.

## 5E2-B1: implemented exact source baseline

CAR now captures a read-only, content-free `SourceBaseline` for the exact source
working tree. It resolves the full `HEAD` OID, parses NUL-delimited Git porcelain
v2 status, observes tracked and untracked paths, and records repository-relative
file identities only: SHA-256, size, file presence, symlink identity, and staged,
unstaged, and untracked classification. Hashing streams regular-file bytes and the
serialized model never contains file content or absolute paths.

Revalidation recaptures the same bounded baseline and compares a deterministic
digest. A changed `HEAD`, content, file presence, untracked set, or index/working
tree classification yields `CONCURRENT_MODIFICATION`; future application must
never proceed from that mismatch. Protected local paths are never content-hashed:
a dirty or untracked protected path fails closed instead. Renames, type changes,
merge states, submodule ambiguity, unsafe outside symlinks, and unsupported special
files also fail closed.

Capture and revalidation do not create a worktree, alter source files, Git index,
branch, HEAD, or Git worktree metadata, and do not call a provider, Codex, network,
verification, patching, or apply service. This is observation only.

The future isolated workspace will represent user-visible working-tree content:

```text
HEAD worktree + validated projection of WORKING TREE state
```

Index metadata remains a concurrency-protection signal; it is not future Codex
content.

## 5E2-B2: implemented safe baseline projection

CAR now composes a B1 baseline with a 5E2-A detached worktree created from the
baseline's exact `HEAD` OID. It revalidates the source before reading any
working-tree content, projects only the required working-tree overlay into the
disposable workspace, validates each destination identity, and revalidates the
source again before returning a usable `ProjectedIsolatedWorkspace`.

The projected workspace is:

```text
baseline HEAD + authorized current WORKING TREE overlay
```

Dirty tracked files are copied byte-for-byte from the source filesystem; staged
metadata is never copied from the source index. A user-deleted tracked file is
removed only from the temporary workspace. Untracked files are excluded by default
and can be projected only through an explicit repository-relative authorization
list that must match the captured baseline. Protected paths and projected symlinks
fail closed. No `git add` is run in the isolated workspace.

Any pre- or post-projection baseline mismatch makes the workspace unusable and it
is disposed through the owned worktree lifecycle. A partially projected workspace
is likewise disposable rather than finalized. The user repository, its index,
branch, HEAD, and files remain untouched.

No Codex execution, provider call, delta extraction, or real-repository apply is
enabled.

## 5E3-A: implemented controlled write runtime foundation

CAR now has a separate `ControlledCodexWriteRuntime`; the existing
`LocalCodexRuntime` remains a read-only diagnostic adapter. The write runtime
accepts only a currently CAR-owned `ProjectedIsolatedWorkspace`, requires both an
enabled `CodexWritePolicy` and runtime `CodexWriteAuthorization`, and otherwise
does not perform health checks or start a process.

Its fixed structured command uses the resolved local Codex executable with global
approval set before `exec`, `--ephemeral`, `--sandbox workspace-write`, and
`--ignore-user-config`. CAR supplies no additional writable roots, no network
enablement, no privilege fallback, and no user-config parsing. Task and optional
structured handoff evidence travel over stdin; output is bounded. The child
environment is a positive allowlist for process, platform, local login discovery,
and locale variables, excluding provider and token environment values.

An exit-zero process with a final message means only **Codex execution completed**.
It does not accept a change, validate a delta, verify a task, or modify the source
repository. Workspace lifecycle remains caller-owned so a future stage can inspect
the isolated filesystem delta.

### Isolation claim

CAR's current security design guarantees controlled **write confinement** through
the disposable projected workspace, Codex `workspace-write`, no extra writable
roots, and future delta validation. CAR does **not** claim host-wide read
isolation: read confinement depends on Codex and OS sandbox behavior and is not a
CAR guarantee in 5E3-A.

## Next sequence

1. **5E3-B — Opt-in Live Codex Isolated Write Validation.** It will use a
   dedicated opt-in flag to prove a real small workspace-write change in a
   projected disposable workspace while preserving the source repository and
   isolated Git state. It will still not accept a delta, apply to source, or claim
   task success.
2. **5E4 — Exact Delta Extraction and Validation**
3. **5E4 — Exact Delta Extraction and Validation**
4. **5E5 — CAR-controlled Apply, Verification, and Rollback**
5. **5E6 — Explicit CLI Authorization and End-to-End Flow**
