"""Trusted Codex proposal pipeline over an untrusted disposable scratch workspace."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from car.authorization import classify_authorized_path
from car.coding.models import CodingProposal, CodingTaskContext
from car.patching.apply import SafePatchApplier
from car.patching.models import PatchValidationPolicy
from car.patching.validation import PatchValidator
from car.providers.models import RepositoryClassificationContext
from car.router.models import Route
from car.verification.models import VerificationPlan

from .application import CodexSourceApplicationService
from .baseline import SourceBaseline, SourceBaselineService
from .delta import CodexWorkspaceDeltaDetector, CodexWorkspaceDeltaValidator
from .models import (
    CodexSourceState,
    CodexWriteAuthorization,
    CodexWriteFailureKind,
    CodexWritePolicy,
    ControlledCodexWritePipelineResult,
    ControlledCodexWritePipelineStage,
)
from .projection import BaselineProjectionService, ProjectedIsolatedWorkspace
from .runtime import ControlledCodexWriteRuntime
from .runtime_models import ControlledCodexWriteRequest, ControlledCodexWriteResult
from .verification import CodexSourceVerificationCoordinator
from .workspace import IsolatedWorkspaceManager


class ControlledCodexWritePipeline:
    """Discard Codex scratch changes; trust only its final structured proposal."""

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
        authorization_summary: str | None = None,
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
        scratch = self._project(repository, baseline, policy, authorized_paths)
        if scratch is None:
            return _failure(
                CodexWriteFailureKind.PROJECTION_FAILED,
                baseline_digest=baseline.baseline_digest,
            )
        fresh: ProjectedIsolatedWorkspace | None = None
        result: ControlledCodexWritePipelineResult
        try:
            runtime = self._runtime or ControlledCodexWriteRuntime(
                workspace_manager=self._manager, policy=policy
            )
            codex = runtime.execute(
                ControlledCodexWriteRequest(
                    workspace=scratch,
                    task=task,
                    authorized_paths=authorized_paths,
                    authorization_summary=authorization_summary,
                    safe_auxiliary_paths=policy.safe_auxiliary_paths,
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
                result, fresh = self._accept_final_proposal(
                    repository=repository,
                    baseline=baseline,
                    scratch_result=codex,
                    task=task,
                    authorized_paths=authorized_paths,
                    verification_plan=verification_plan,
                    policy=policy,
                    authorization=authorization,
                )
        finally:
            cleanup_ok = self._manager.cleanup(scratch.workspace).removed
            if fresh is not None:
                cleanup_ok = self._manager.cleanup(fresh.workspace).removed and cleanup_ok
        return result.model_copy(
            update={
                "workspace_cleanup_attempted": True,
                "workspace_cleanup_succeeded": cleanup_ok,
                "message": result.message
                if cleanup_ok
                else f"{result.message}; isolated workspace cleanup failed",
            }
        )

    def _accept_final_proposal(
        self,
        *,
        repository: Path,
        baseline: SourceBaseline,
        scratch_result: ControlledCodexWriteResult,
        task: str,
        authorized_paths: tuple[str, ...],
        verification_plan: VerificationPlan,
        policy: CodexWritePolicy,
        authorization: CodexWriteAuthorization,
    ) -> tuple[ControlledCodexWritePipelineResult, ProjectedIsolatedWorkspace | None]:
        codex = scratch_result
        proposal = _proposal_from_final_message(codex.final_message)
        if proposal is None:
            return _failure(
                CodexWriteFailureKind.CODEX_INVALID_OUTPUT,
                baseline_digest=baseline.baseline_digest,
                workspace_created=True,
                codex_result=codex,
            ), None
        unauthorized = next(
            (
                change.path
                for change in proposal.changes
                if classify_authorized_path(
                    change.path,
                    authorized_paths,
                    safe_auxiliary_paths=policy.safe_auxiliary_paths,
                )
                is None
            ),
            None,
        )
        if unauthorized is not None:
            return _failure(
                CodexWriteFailureKind.UNAUTHORIZED_CHANGE,
                baseline_digest=baseline.baseline_digest,
                workspace_created=True,
                codex_result=codex,
                delta_result={"rejected_paths": [unauthorized]},
            ), None
        fresh = self._project(repository, baseline, policy, authorized_paths)
        if fresh is None:
            return _failure(
                CodexWriteFailureKind.PROJECTION_FAILED,
                baseline_digest=baseline.baseline_digest,
                workspace_created=True,
                codex_result=codex,
            ), None
        validation = PatchValidator(_patch_policy(policy)).validate(
            proposal, _proposal_context(task, authorized_paths, policy), fresh.workspace.path
        )
        if not validation.valid or validation.patch_set is None:
            return _failure(
                CodexWriteFailureKind.UNAUTHORIZED_CHANGE,
                baseline_digest=baseline.baseline_digest,
                workspace_created=True,
                codex_result=codex,
                delta_result=validation,
            ), fresh
        transaction = SafePatchApplier(_patch_policy(policy)).apply(
            fresh.workspace.path, validation.patch_set
        )
        if not transaction.result.succeeded:
            return _failure(
                CodexWriteFailureKind.SOURCE_APPLICATION_FAILED,
                baseline_digest=baseline.baseline_digest,
                workspace_created=True,
                codex_result=codex,
                delta_result=validation,
            ), fresh
        detected = self._detector.detect(fresh, baseline, policy)
        validated = self._validator.validate(
            detected, baseline, policy, authorization, authorized_paths, repository
        )
        if not validated.valid or validated.validated_change_set is None:
            return _failure(
                validated.failure_kind or CodexWriteFailureKind.VALIDATION_FAILED,
                baseline_digest=baseline.baseline_digest,
                workspace_created=True,
                codex_result=codex,
                delta_result=validated,
            ), fresh
        application, source_transaction = self._application.apply(
            repository, fresh, validated.validated_change_set, baseline, policy
        )
        if not application.applied or source_transaction is None:
            return _failure(
                application.failure_kind or CodexWriteFailureKind.SOURCE_APPLICATION_FAILED,
                baseline_digest=baseline.baseline_digest,
                workspace_created=True,
                codex_result=codex,
                delta_result=validated,
                application_result=application,
                source_state=(
                    CodexSourceState.UNCERTAIN
                    if application.rollback_attempted and not application.rollback_succeeded
                    else CodexSourceState.RESTORED
                    if application.rollback_attempted
                    else CodexSourceState.UNCHANGED
                ),
            ), fresh
        verification = self._verification.verify_and_finalize(
            source_transaction, verification_plan, repository, policy
        )
        return ControlledCodexWritePipelineResult(
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
        ), fresh

    def _project(
        self,
        repository: Path,
        baseline: SourceBaseline,
        policy: CodexWritePolicy,
        authorized_paths: tuple[str, ...],
    ) -> ProjectedIsolatedWorkspace | None:
        known_untracked = {item.path for item in baseline.files if item.untracked}
        result = self._projection.project(
            repository,
            baseline,
            policy,
            authorized_untracked_paths=tuple(
                path for path in authorized_paths if path in known_untracked
            ),
        )
        return result.projected_workspace if result.succeeded else None


def _proposal_from_final_message(message: str | None) -> CodingProposal | None:
    if message is None:
        return None
    try:
        return CodingProposal.model_validate_json(message)
    except (ValidationError, ValueError, json.JSONDecodeError):
        return None


def _proposal_context(task, paths, policy) -> CodingTaskContext:
    return CodingTaskContext(
        task=task,
        route=Route.CODEX,
        repository=RepositoryClassificationContext(
            name="isolated-workspace", branch="detached", dirty=False, languages={}, systems=[]
        ),
        authorized_paths=tuple(paths),
        files=[],
        safe_auxiliary_paths=policy.safe_auxiliary_paths,
    )


def _patch_policy(policy: CodexWritePolicy) -> PatchValidationPolicy:
    return PatchValidationPolicy(
        max_files=policy.max_files,
        max_patch_bytes_per_file=policy.max_file_bytes,
        max_total_patch_bytes=policy.max_file_bytes * policy.max_files,
        protected_prefixes=policy.protected_prefixes,
        safe_auxiliary_paths=policy.safe_auxiliary_paths,
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
) -> ControlledCodexWritePipelineResult:
    return ControlledCodexWritePipelineResult(
        attempted=workspace_created,
        source_state=source_state,
        terminal_stage=ControlledCodexWritePipelineStage.FAILED,
        failure_kind=failure_kind,
        baseline_digest=baseline_digest,
        workspace_created=workspace_created,
        codex_result=codex_result,
        delta_result=delta_result,
        application_result=application_result,
        message="controlled Codex scratch proposal was not accepted",
    )
