"""Provider-neutral, no-execution contracts for future controlled Codex writes."""

from .baseline import (
    BaselineCaptureResult,
    BaselineRevalidationResult,
    SourceBaseline,
    SourceBaselineService,
    parse_porcelain_v2,
)
from .models import (
    CodexChangeSet,
    CodexChangeValidationResult,
    CodexFileDelta,
    CodexFileIdentity,
    CodexWorkspaceBaseline,
    CodexWriteAuthorization,
    CodexWriteFailureKind,
    CodexWritePolicy,
    baseline_matches,
    validate_change_set,
)
from .workspace import (
    IsolatedCodexWorkspace,
    IsolatedWorkspaceManager,
    WorkspaceCleanupResult,
    WorkspaceCreationResult,
)

__all__ = [
    "BaselineCaptureResult",
    "BaselineRevalidationResult",
    "CodexChangeSet",
    "CodexChangeValidationResult",
    "CodexFileDelta",
    "CodexFileIdentity",
    "CodexWriteAuthorization",
    "CodexWriteFailureKind",
    "CodexWritePolicy",
    "CodexWorkspaceBaseline",
    "baseline_matches",
    "validate_change_set",
    "IsolatedCodexWorkspace",
    "IsolatedWorkspaceManager",
    "SourceBaseline",
    "SourceBaselineService",
    "WorkspaceCleanupResult",
    "WorkspaceCreationResult",
    "parse_porcelain_v2",
]
