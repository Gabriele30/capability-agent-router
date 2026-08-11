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
`--ignore-user-config`. It also passes `--cd` with the exact path of the validated,
CAR-owned `ProjectedIsolatedWorkspace`; the subprocess `cwd` is bound to that same
workspace. CAR supplies no additional writable roots, no network enablement, no
privilege fallback, and no user-config parsing. Task and optional structured handoff
evidence travel over stdin; output is bounded. The child environment is a positive
allowlist for process, platform, local login discovery, and locale variables,
excluding provider and token environment values.

On Windows, live validation with Codex CLI 0.147.0 observed workspace writes being
rejected until CAR selected the Windows sandbox backend explicitly with
`windows.sandbox="unelevated"`. The controlled-write runtime therefore pins that
backend on Windows only. Controlled-write validation also requires explicit Codex
`--cd` bound to the projected isolated workspace and a subprocess `cwd` bound to the
same path. CAR retains `workspace-write`, `approval=never`, `--ignore-user-config`,
and no additional writable roots. This is not an elevated sandbox and does not apply
to the separate read-only runtime; `--cd` remains explicit on POSIX as well.

Further live causal validation on Windows Codex CLI 0.147.0 found that a private
CAR temporary worktree parent could prevent Codex from writing, while the same parent
with normal ACL inheritance enabled allowed the isolated write. Before creating a
linked worktree, CAR therefore enables normal inherited ACLs only on its newly
created disposable Windows worktree parent. It does not grant broad access, modify
the source repository or `%TEMP%` ACLs, alter Codex identities, add writable roots,
or use an elevated sandbox. This is a tested Windows compatibility measure, not a
claim about all Codex versions or host-wide read isolation.

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

## 5E3-B: live validation implemented

An integration test now validates the complete B1 → B2 → controlled-write runtime
path against a synthetic temporary Git repository. It is independently gated by
`CAR_RUN_LIVE_CODEX_WRITE_TESTS=1`. Without that exact environment value, the test
skips at module import before temporary-repository creation, baseline capture,
worktree creation, health checks, or any Codex subprocess.

When explicitly enabled, the test may skip only when the locally installed Codex
CLI or its existing local login is unavailable. Once ready, runtime, sandbox,
filesystem, Git-state, source-invariance, and cleanup failures are test failures.
It checks a small authorized change only in the projected workspace, leaves source
files and Git state unchanged, requires the isolated change to remain unstaged and
detached, and then removes the owned worktree.

The canonical validation has been run successfully against a synthetic repository.
It does not accept a Codex delta, apply anything to the source repository, or
establish host-wide read isolation.

## 5E4-A: isolated delta detection and validation

The controlled-write pipeline is now B1 source baseline capture, B2 projection,
B3 Codex isolated write, then isolated workspace delta detection and validation.
Codex filesystem writes remain untrusted: an exit-zero Codex process does not mean
that a change is accepted. CAR independently compares the isolated filesystem with
the exact B1 state projected into that workspace, checks linked-worktree integrity,
revalidates the source baseline for concurrent user changes, and validates paths,
authorization, supported operations, symlinks, binary data, and configured bounds.

The result may be a `ValidatedCodexChangeSet`, but 5E4-A never applies it. CAR does
not copy files to the source repository, stage or commit changes, run source
verification, or set `changes_accepted=True` in this stage.

## 5E4-B1: transactional validated source application

CAR can now apply a `ValidatedCodexChangeSet` internally through
`CodexSourceApplicationService`, but this is not wired to a CLI or coding flow.
The service accepts only a CAR-owned projected workspace, its matching source
baseline, and a freshly validated change set. It revalidates the source baseline,
linked-worktree integrity, isolated delta, authorization, and exact isolated file
SHA-256/size immediately before applying any source bytes.

Only validated regular-file `MODIFY` and `CREATE` operations are supported. Paths
must be repository-relative, contained, unprotected, and free of source or parent
symlink traversal. A create requires an already-existing safe parent directory;
5E4-B1 deliberately does not create directory trees. Source writes copy validated
bytes exactly, using same-directory atomic replacement for modifications and an
exclusive non-clobbering create for new files. No Git mutation, provider call, or
verification command is used.

Before the first source write CAR captures a target-scoped byte snapshot. The
returned `AppliedCodexSourceTransaction` is `APPLIED_PENDING_VERIFICATION`, not an
accepted task result. On a partial write failure it rolls back every file written by
the transaction. Explicit rollback restores only when each applied target still has
the exact transaction-written identity; it fails closed rather than overwriting a
subsequent user edit. A successful filesystem application leaves
`changes_accepted=False`; 5E4-B2 must perform CAR-controlled verification before a
future caller may finalize the transaction.

## 5E4-B2: verification-gated source finalization

`CodexSourceVerificationCoordinator` now consumes only a matching B1 transaction
in `APPLIED_PENDING_VERIFICATION`. It requires a non-empty CAR-selected
`VerificationPlan`, uses the shared `VerificationEngine`, and accepts only the
existing read-only Ruff or pytest command families. It verifies the real source
repository with the existing pytest environment hygiene; it never invokes Codex,
Gemini, a provider, a shell, or a Git mutation command.

Before and after verification, CAR checks the transaction-written file identities
and exact source integrity: captured HEAD, branch, index, pre-existing dirty state,
and untracked state may differ only by the B1 transaction targets. A passing test
command is therefore insufficient if it stages, changes Git state, writes another
file, creates an artifact, or changes a transaction target. Such a mismatch is not
accepted and invokes identity-aware rollback when safe.

Only verification pass plus post-verification integrity pass plus a successful
`transaction.finalize()` produces `accepted=True`. Verification failure, timeout,
an empty plan, an unsafe command, or integrity failure leaves changes unaccepted
and attempts rollback. If rollback detects a later user edit it preserves that edit
and reports an uncertain source state. This remains an internal foundation: no CLI
path applies or finalizes controlled Codex writes.

## 5E5-A: internal end-to-end composition

`ControlledCodexWritePipeline` composes the existing internal boundaries only:

```text
authorization -> baseline -> isolated projection -> controlled Codex write
-> delta validation -> transactional source apply -> CAR verification
-> finalize or rollback -> isolated workspace cleanup
```

It is **internal only** and is not wired to CLI, routing, Gemini escalation, or
any user-visible write behavior. The pipeline owns only sequencing and owned
workspace cleanup; baseline capture, projection, Windows ACL handling, runtime
sandboxing, delta validation, application, rollback, verification, and finalization
remain delegated to their existing components.

Policy and runtime authorization are checked before workspace creation. A non-empty
verification plan is likewise required before isolated Codex execution. Codex process
success remains insufficient: no delta, rejected delta, application failure,
verification failure, or integrity uncertainty is unaccepted. On every path after
projection, cleanup of the CAR-owned isolated worktree is attempted and reported.
Cleanup failure is visible even if a source transaction was already accepted.

Only the B2 verification/finalization result can make pipeline `accepted=True`.
The controlled Codex runtime's own `changes_accepted` remains false.

## 5E5-B: internal verified Gemini-failure escalation

The existing verified-failure post-processing boundary can now select one Codex mode
for an eligible `GEMINI_TO_CODEX` failure. A controlled write is selected only after
Gemini verification failed with a completed rollback, and only with an explicitly
enabled write policy, runtime write authorization, non-empty explicit path scope,
and non-empty verification plan. Routing to Codex is not write consent.

The existing read-only Codex escalation remains intact and is selected only when
controlled write is not eligible under its separate policy. The modes are mutually
exclusive per escalation event; read-only analysis never upgrades to a write. The
existing bounded `CodexHandoff` is passed in memory as context only and cannot widen
paths, change policy, or alter verification. This remains internal only: no CLI or
routing behavior has changed.

## Next sequence

Current status: 5E3-B is locally live-verified; 5E4-A validates the untrusted
isolated delta; and 5E4-B1 can make a reversible internal source application that
remains pending verification. The historical roadmap entries below do not authorize
CLI source writes or acceptance.

1. **5E3-B — Opt-in Live Codex Isolated Write Validation.** Implemented; pending
   explicit local execution with `CAR_RUN_LIVE_CODEX_WRITE_TESTS=1`.
2. **5E4 — Exact Delta Extraction and Validation**
3. **5E4 — Exact Delta Extraction and Validation**
4. **5E5 — CAR-controlled Apply, Verification, and Rollback**
5. **5E6 — Explicit CLI Authorization and End-to-End Flow**
