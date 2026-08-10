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

## Next sequence

1. **5E2-B2 — Safe Dirty/Staged/Untracked Projection.** It will create the HEAD
   worktree, revalidate a B1 baseline before projection, overlay working-tree
   content, apply user deletions only in isolation, and selectively copy bounded
   safe untracked files. It will never copy protected local state, mutate the
   source repository, or invoke Codex.
2. **5E3 — Controlled Codex Coding Execution in Isolation**
3. **5E4 — Exact Delta Extraction and Validation**
4. **5E5 — CAR-controlled Apply, Verification, and Rollback**
5. **5E6 — Explicit CLI Authorization and End-to-End Flow**
