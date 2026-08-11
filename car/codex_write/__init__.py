"""Provider-neutral, no-execution contracts for future controlled Codex writes."""

from .application import AppliedCodexSourceTransaction, CodexSourceApplicationService
from .baseline import (
    BaselineCaptureResult,
    BaselineRevalidationResult,
    SourceBaseline,
    SourceBaselineService,
    parse_porcelain_v2,
)
from .delta import CodexWorkspaceDeltaDetector, CodexWorkspaceDeltaValidator
from .models import (
    CodexChangeSet,
    CodexChangeValidationResult,
    CodexFileDelta,
    CodexFileIdentity,
    CodexSourceApplicationResult,
    CodexSourceTransactionState,
    CodexWorkspaceBaseline,
    CodexWorkspaceDelta,
    CodexWorkspaceDeltaValidationResult,
    CodexWriteAuthorization,
    CodexWriteFailureKind,
    CodexWritePolicy,
    ValidatedCodexChangeSet,
    baseline_matches,
    validate_change_set,
)
from .projection import (
    BaselineProjectionService,
    ProjectedIsolatedWorkspace,
    ProjectionResult,
)
from .runtime import ControlledCodexWriteRuntime, SubprocessControlledCodexRunner
from .runtime_models import (
    ControlledCodexHealthStatus,
    ControlledCodexProcessResult,
    ControlledCodexWriteHealth,
    ControlledCodexWriteRequest,
    ControlledCodexWriteResult,
)
from .workspace import (
    IsolatedCodexWorkspace,
    IsolatedWorkspaceManager,
    WorkspaceCleanupResult,
    WorkspaceCreationResult,
)

__all__ = [
    "BaselineCaptureResult",
    "AppliedCodexSourceTransaction",
    "BaselineProjectionService",
    "ControlledCodexHealthStatus",
    "ControlledCodexProcessResult",
    "ControlledCodexWriteHealth",
    "ControlledCodexWriteRequest",
    "ControlledCodexWriteResult",
    "ControlledCodexWriteRuntime",
    "BaselineRevalidationResult",
    "CodexChangeSet",
    "CodexChangeValidationResult",
    "CodexSourceApplicationResult",
    "CodexSourceApplicationService",
    "CodexSourceTransactionState",
    "CodexFileDelta",
    "CodexFileIdentity",
    "CodexWriteAuthorization",
    "CodexWriteFailureKind",
    "CodexWritePolicy",
    "CodexWorkspaceBaseline",
    "CodexWorkspaceDelta",
    "CodexWorkspaceDeltaDetector",
    "CodexWorkspaceDeltaValidationResult",
    "CodexWorkspaceDeltaValidator",
    "baseline_matches",
    "validate_change_set",
    "IsolatedCodexWorkspace",
    "IsolatedWorkspaceManager",
    "ProjectedIsolatedWorkspace",
    "ProjectionResult",
    "SourceBaseline",
    "SourceBaselineService",
    "SubprocessControlledCodexRunner",
    "WorkspaceCleanupResult",
    "WorkspaceCreationResult",
    "ValidatedCodexChangeSet",
    "parse_porcelain_v2",
]
