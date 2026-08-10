"""CAR-owned parsing, validation, and controlled application of coding patches."""

from car.patching.apply import PatchApplyTransaction, SafePatchApplier
from car.patching.models import (
    ParsedFilePatch,
    ParsedHunk,
    ParsedHunkLine,
    ParsedPatchSet,
    PatchApplyFailureKind,
    PatchApplyResult,
    PatchValidationPolicy,
    PatchValidationResult,
    PatchViolation,
    PatchViolationKind,
    ValidatedPatchSet,
)
from car.patching.validation import PatchValidator

__all__ = [
    "PatchApplyFailureKind",
    "PatchApplyResult",
    "PatchApplyTransaction",
    "ParsedFilePatch",
    "ParsedHunk",
    "ParsedHunkLine",
    "ParsedPatchSet",
    "PatchValidationPolicy",
    "PatchValidationResult",
    "PatchValidator",
    "SafePatchApplier",
    "PatchViolation",
    "PatchViolationKind",
    "ValidatedPatchSet",
]
