"""Internal-only composition of the controlled Codex source-write stages."""

from pathlib import Path

from car.verification.models import VerificationPlan

from .application import CodexSourceApplicationService
from .baseline import SourceBaselineService
from .delta import CodexWorkspaceDeltaDetector, CodexWorkspaceDeltaValidator
from .models import (
    CodexSourceState,
    CodexWriteAuthorization,
    CodexWriteFailureKind,
    CodexWritePolicy,
    ControlledCodexWritePipelineResult,
    ControlledCodexWritePipelineStage,
)
from .projection import BaselineProjectionService
from .runtime import ControlledCodexWriteRuntime
from .runtime_models import ControlledCodexWriteRequest
from .verification import CodexSourceVerificationCoordinator
from .workspace import IsolatedWorkspaceManager


class ControlledCodexWritePipeline:
    """Compose existing controlled-write boundaries without adding a public entry point."""

    def __init__(
        self,
        *,
        workspace_manager: IsolatedWorkspaceManager | None = None,
        baseline_service: SourceBaselineService | None = None,
        projection_service: BaselineProjectionService | None = None,
        runtime: ControlledCodexWriteRuntime | None = None,
        detector: CodexWorkspaceDeltaDetector | None = None,
        validator: CodexWorkspaceDeltaValidator | None = None,
        application_service: CodexSourceApplicationService | None = None,
        verification_coordinator: CodexSourceVerificationCoordinator | None = None,
    ) -> None:
        self._manager = workspace_manager or IsolatedWorkspaceManager()
        self._baseline = baseline_service or SourceBaselineService()
        self._projection = projection_service or BaselineProjectionService(
            baseline_service=self._baseline, workspace_manager=self._manager
        )
        self._runtime = runtime
        self._detector = detector or CodexWorkspaceDeltaDetector(self._manager)
        self._validator = validator or CodexWorkspaceDeltaValidator(self._baseline)
        self._application = application_service or CodexSourceApplicationService(
            self._detector, self._validator, self._baseline
        )
        self._verification = verification_coordinator or CodexSourceVerificationCoordinator()

    def execute(
        self,
        repository: Path,
        task: str,
        authorized_paths: tuple[str, ...],
        verification_plan: VerificationPlan,
        policy: CodexWritePolicy,
        authorization: CodexWriteAuthorization,
        handoff=None,
        codex_model: str | None = None,
        codex_reasoning_effort=None,
    ) -> ControlledCodexWritePipelineResult:
        if not policy.enabled:
            return _failure(CodexWriteFailureKind.DISABLED)
        if not authorization.authorized:
            return _failure(CodexWriteFailureKind.NOT_AUTHORIZED)
        if not verification_plan.commands:
            return _failure(CodexWriteFailureKind.VERIFICATION_REQUIRED)
        captured = self._baseline.capture(repository, policy)
        if not captured.captured or captured.baseline is None:
            return _failure(captured.failure_kind or CodexWriteFailureKind.INVALID_BASELINE)
        baseline = captured.baseline
        known_untracked = {item.path for item in baseline.files if item.untracked}
        projection = self._projection.project(
            repository,
            baseline,
            policy,
            authorized_untracked_paths=tuple(
                path for path in authorized_paths if path in known_untracked
            ),
        )
        if not projection.succeeded or projection.projected_workspace is None:
            return _failure(
                projection.failure_kind or CodexWriteFailureKind.PROJECTION_FAILED,
                baseline_digest=baseline.baseline_digest,
                stage=ControlledCodexWritePipelineStage.FAILED,
            )
        workspace = projection.projected_workspace
        result: ControlledCodexWritePipelineResult
        try:
            runtime = self._runtime or ControlledCodexWriteRuntime(
                workspace_manager=self._manager, policy=policy
            )
            codex = runtime.execute(
                ControlledCodexWriteRequest(
                    workspace=workspace,
                    task=task,
                    authorized_paths=authorized_paths,
                    handoff=handoff,
                    model=codex_model,
                    reasoning_effort=codex_reasoning_effort,
                ),
                authorization,
            )
            if not codex.process_succeeded:
                result = _failure(
                    codex.failure_kind or CodexWriteFailureKind.CODEX_PROCESS_ERROR,
                    baseline_digest=baseline.baseline_digest,
                    workspace_created=True,
                    codex_result=codex,
                )
            else:
                detected = self._detector.detect(workspace, baseline, policy)
                validated = self._validator.validate(
                    detected, baseline, policy, authorization, authorized_paths, repository
                )
                if not validated.valid or validated.validated_change_set is None:
                    result = _failure(
                        validated.failure_kind or CodexWriteFailureKind.VALIDATION_FAILED,
                        baseline_digest=baseline.baseline_digest,
                        workspace_created=True,
                        codex_result=codex,
                        delta_result=validated,
                    )
                else:
                    application, transaction = self._application.apply(
                        repository, workspace, validated.validated_change_set, baseline, policy
                    )
                    if not application.applied or transaction is None:
                        result = _failure(
                            application.failure_kind
                            or CodexWriteFailureKind.SOURCE_APPLICATION_FAILED,
                            baseline_digest=baseline.baseline_digest,
                            workspace_created=True,
                            codex_result=codex,
                            delta_result=validated,
                            application_result=application,
                            source_state=CodexSourceState.UNCERTAIN
                            if application.rollback_attempted and not application.rollback_succeeded
                            else CodexSourceState.RESTORED
                            if application.rollback_attempted
                            else CodexSourceState.UNCHANGED,
                        )
                    else:
                        verification = self._verification.verify_and_finalize(
                            transaction, verification_plan, repository, policy
                        )
                        result = ControlledCodexWritePipelineResult(
                            attempted=True,
                            accepted=verification.accepted,
                            source_state=verification.source_state,
                            terminal_stage=(
                                ControlledCodexWritePipelineStage.FINALIZED
                                if verification.accepted
                                else ControlledCodexWritePipelineStage.ROLLED_BACK
                                if verification.source_state == CodexSourceState.RESTORED
                                else ControlledCodexWritePipelineStage.FAILED
                            ),
                            failure_kind=verification.failure_kind,
                            baseline_digest=baseline.baseline_digest,
                            workspace_created=True,
                            codex_result=codex,
                            delta_result=validated,
                            application_result=application,
                            verification_result=verification,
                            changed_paths=application.changed_paths,
                            created_paths=application.created_paths,
                            modified_paths=application.modified_paths,
                            task_changed_paths=validated.task_changed_paths,
                            auxiliary_changed_paths=validated.auxiliary_changed_paths,
                            message=verification.message,
                        )
        finally:
            cleanup = self._manager.cleanup(workspace.workspace)
        return result.model_copy(
            update={
                "workspace_cleanup_attempted": True,
                "workspace_cleanup_succeeded": cleanup.removed,
                "message": result.message
                if cleanup.removed
                else f"{result.message}; isolated workspace cleanup failed",
            }
        )


def _failure(
    failure_kind: CodexWriteFailureKind,
    *,
    baseline_digest: str | None = None,
    workspace_created: bool = False,
    codex_result: object | None = None,
    delta_result: object | None = None,
    application_result=None,
    source_state: CodexSourceState = CodexSourceState.UNCHANGED,
    stage: ControlledCodexWritePipelineStage = ControlledCodexWritePipelineStage.FAILED,
) -> ControlledCodexWritePipelineResult:
    return ControlledCodexWritePipelineResult(
        attempted=workspace_created,
        source_state=source_state,
        terminal_stage=stage,
        failure_kind=failure_kind,
        baseline_digest=baseline_digest,
        workspace_created=workspace_created,
        codex_result=codex_result,
        delta_result=delta_result,
        application_result=application_result,
        message="controlled Codex write pipeline did not accept source changes",
    )
