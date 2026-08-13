"""Minimal, strict parser for CAR's supported unified-diff subset."""

import re

from car.patching.models import (
    ParsedFilePatch,
    ParsedHunk,
    ParsedHunkLine,
    ParsedPatchOperation,
    PatchViolationKind,
)

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$")
_MODE_PREFIXES = ("old mode ", "new mode ", "new file mode ", "deleted file mode ")


class PatchParseError(ValueError):
    """A safe parser error: its message never includes raw patch content."""

    def __init__(self, kind: PatchViolationKind, summary: str) -> None:
        self.kind = kind
        super().__init__(summary)


def parse_file_patch(patch: str) -> ParsedFilePatch:
    """Parse exactly one text unified diff; never access the filesystem."""
    lines = patch.splitlines()
    if not lines:
        raise PatchParseError(PatchViolationKind.INVALID_DIFF, "patch is empty")
    if any(line == "GIT binary patch" or line.startswith("Binary files ") for line in lines):
        raise PatchParseError(PatchViolationKind.BINARY_PATCH_NOT_SUPPORTED, "binary patch")
    if any(line.startswith(_MODE_PREFIXES) for line in lines):
        raise PatchParseError(PatchViolationKind.MODE_CHANGE_NOT_SUPPORTED, "mode change")
    if len(lines) < 3 or not lines[0].startswith("--- ") or not lines[1].startswith("+++ "):
        raise PatchParseError(PatchViolationKind.INVALID_DIFF, "missing unified diff file headers")

    old_path = _strip_diff_prefix(lines[0][4:])
    new_path = _strip_diff_prefix(lines[1][4:])
    if not old_path or not new_path:
        raise PatchParseError(PatchViolationKind.INVALID_DIFF, "empty diff file path")
    hunks = _parse_hunks(lines[2:])
    operation = _operation_for_paths(old_path, new_path)
    path = new_path if new_path != "/dev/null" else old_path
    return ParsedFilePatch(
        path=path,
        operation=operation,
        old_path=old_path,
        new_path=new_path,
        hunks=hunks,
    )


def _strip_diff_prefix(path: str) -> str:
    if path == "/dev/null":
        return path
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def _operation_for_paths(old_path: str, new_path: str) -> ParsedPatchOperation:
    if old_path == "/dev/null" and new_path != "/dev/null":
        return ParsedPatchOperation.CREATE
    if new_path == "/dev/null" and old_path != "/dev/null":
        return ParsedPatchOperation.DELETE
    if old_path != new_path:
        return ParsedPatchOperation.RENAME
    return ParsedPatchOperation.MODIFY


def _parse_hunks(lines: list[str]) -> list[ParsedHunk]:
    hunks: list[ParsedHunk] = []
    position = 0
    while position < len(lines):
        header = lines[position]
        if header.startswith("--- ") or header.startswith("+++ "):
            raise PatchParseError(
                PatchViolationKind.MULTIPLE_FILES_IN_CHANGE,
                "a change must contain exactly one file diff",
            )
        match = _HUNK_HEADER.match(header)
        if not match:
            raise PatchParseError(PatchViolationKind.HUNK_INVALID, "invalid hunk header")
        old_start, old_count, new_start, new_count = (
            int(match.group(1)),
            int(match.group(2) or 1),
            int(match.group(3)),
            int(match.group(4) or 1),
        )
        position += 1
        hunk_lines: list[ParsedHunkLine] = []
        while position < len(lines) and not lines[position].startswith("@@ "):
            line = lines[position]
            if line.startswith(("--- ", "+++ ")):
                raise PatchParseError(
                    PatchViolationKind.MULTIPLE_FILES_IN_CHANGE,
                    "a change must contain exactly one file diff",
                )
            if not line or line[0] not in {" ", "+", "-"}:
                raise PatchParseError(PatchViolationKind.HUNK_INVALID, "invalid hunk line")
            hunk_lines.append(ParsedHunkLine(prefix=line[0], content=line[1:]))
            position += 1
        if not hunk_lines:
            raise PatchParseError(PatchViolationKind.HUNK_INVALID, "empty hunk")
        # Provider-authored headers sometimes contain stale line counts.  The body
        # is the sole deterministic source for those counts: it is already fully
        # parsed here and every line has an unambiguous unified-diff prefix.
        # Start locations and all patch content remain provider supplied and are
        # still checked strictly by the validator and applier.
        old_count, new_count = _canonical_hunk_counts(hunk_lines)
        hunks.append(
            ParsedHunk(
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                lines=hunk_lines,
            )
        )
    if not hunks:
        raise PatchParseError(PatchViolationKind.HUNK_INVALID, "patch has no hunks")
    _reject_overlapping_hunks(hunks)
    return hunks


def _canonical_hunk_counts(lines: list[ParsedHunkLine]) -> tuple[int, int]:
    """Derive the only repairable hunk metadata from an unambiguous body."""
    return (
        sum(line.prefix in {" ", "-"} for line in lines),
        sum(line.prefix in {" ", "+"} for line in lines),
    )


def _reject_overlapping_hunks(hunks: list[ParsedHunk]) -> None:
    previous_start: int | None = None
    previous_end: int | None = None
    for hunk in hunks:
        if previous_start is not None and (
            hunk.old_start <= previous_start or hunk.old_start < previous_end
        ):
            raise PatchParseError(PatchViolationKind.HUNK_OVERLAP, "overlapping or unordered hunks")
        previous_start = hunk.old_start
        previous_end = hunk.old_start + hunk.old_count
