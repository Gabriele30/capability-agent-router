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
from .projection import (
    BaselineProjectionService,
    ProjectedIsolatedWorkspace,
    ProjectionResult,
)
from .workspace import (
    IsolatedCodexWorkspace,
    IsolatedWorkspaceManager,
    WorkspaceCleanupResult,
    WorkspaceCreationResult,
)

__all__ = [
    "BaselineCaptureResult",
    "BaselineProjectionService",
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
    "ProjectedIsolatedWorkspace",
    "ProjectionResult",
    "SourceBaseline",
    "SourceBaselineService",
    "WorkspaceCleanupResult",
    "WorkspaceCreationResult",
    "parse_porcelain_v2",
]
