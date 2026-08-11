"""Offline composition tests for verified failure to read-only Codex execution."""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path

from car.application.codex import CodexExecutionPolicy
from car.application.post_failure import PostFailurePipelineOutcome, process_verified_coding_outcome
from car.codex.models import (
    CodexExecutionResult,
    CodexRuntimeFailureKind,
    CodexRuntimeHealth,
    CodexRuntimeHealthStatus,
)
from car.codex_write.models import (
    CodexSourceState,
    CodexSourceVerificationResult,
    CodexWriteAuthorization,
    CodexWritePolicy,
    ControlledCodexWritePipelineResult,
    ControlledCodexWritePipelineStage,
)
from car.coding.models import (
    CodingAttemptResult,
    CodingFileContext,
    CodingProposal,
    CodingTaskContext,
    FileChangeOperation,
    ProposedFileChange,
)
from car.coding.verification import CodingVerificationFailureKind, CodingVerificationResult
from car.patching.models import PatchApplyResult, PatchValidationResult
from car.providers.models import RepositoryClassificationContext
from car.repository.models import GitState, LanguageStats, ProjectSignals, RepositoryState
from car.router.consultation import DecisionSource, ProviderConsultationResult, RoutingEvaluation
from car.router.models import (
    Complexity,
    RiskAssessment,
    RiskLevel,
    Route,
    RoutingDecision,
    ScopeEstimate,
    ScopeSize,
    TaskCategory,
)
from car.verification.models import VerificationPlan


class FakeCodexRuntime:
    def __init__(self, status=CodexRuntimeHealthStatus.READY, execution=None) -> None:
        self.status = status
        self.execution = execution or CodexExecutionResult(
            attempted=True, succeeded=True, final_message="corrective plan"
        )
        self.health_calls = 0
        self.execute_calls = 0
        self.last_request = None

    def health(self):
        self.health_calls += 1
        return CodexRuntimeHealth(status=self.status)

    def execute(self, request):
        self.execute_calls += 1
        self.last_request = request
        return self.execution


class FakeControlledPipeline:
    def __init__(self, accepted=True) -> None:
        self.accepted = accepted
        self.calls = []

    def execute(self, repository, task, paths, plan, policy, authorization, handoff):
        self.calls.append((paths, handoff))
        return ControlledCodexWritePipelineResult(
            attempted=True,
            accepted=self.accepted,
            source_state=(
                CodexSourceState.UPDATED_AND_ACCEPTED
                if self.accepted
                else CodexSourceState.RESTORED
            ),
            terminal_stage=(
                ControlledCodexWritePipelineStage.FINALIZED
                if self.accepted
                else ControlledCodexWritePipelineStage.ROLLED_BACK
            ),
            verification_result=(
                CodexSourceVerificationResult(
                    attempted=True,
                    verification_passed=True,
                    post_verification_integrity_valid=True,
                    finalized=True,
                    accepted=True,
                    source_state=CodexSourceState.UPDATED_AND_ACCEPTED,
                    message="synthetic finalized verification",
                )
                if self.accepted
                else None
            ),
            message="synthetic controlled result",
        )


def _inputs(root: Path, route: Route = Route.GEMINI_TO_CODEX, *, passed=False, rollback=True):
    decision = RoutingDecision(
        route=route,
        risk=RiskAssessment(score=0.4, level=RiskLevel.MEDIUM),
        complexity=Complexity.MEDIUM,
        scope=ScopeEstimate(size=ScopeSize.MEDIUM),
        confidence=0.7,
        reasons=["parser regression"],
        matched_rules=["test"],
        categories=[TaskCategory.BUGFIX],
    )
    evaluation = RoutingEvaluation(
        deterministic_decision=decision,
        provider_consultation=ProviderConsultationResult(attempted=False, succeeded=False),
        final_decision=decision,
        deterministic_risk=0.4,
        final_risk=0.4,
        fusion_reasons=["deterministic-only"],
        decision_sources=[DecisionSource.DETERMINISTIC],
    )
    repository = RepositoryState(
        root=root,
        name="repo",
        git=GitState(available=True, is_repository=True, branch="main"),
        languages=LanguageStats(counts={"Python": 1}),
        project_signals=ProjectSignals(systems=["Python"]),
    )
    context = CodingTaskContext(
        task="Fix parser regression",
        route=route,
        repository=RepositoryClassificationContext(
            name="repo", branch="main", dirty=False, languages={"Python": 1}, systems=["Python"]
        ),
        files=[CodingFileContext(path="car/parser.py", content="old")],
    )
    proposal = CodingProposal(
        summary="Fix parser",
        changes=[
            ProposedFileChange(
                path="car/parser.py",
                operation=FileChangeOperation.MODIFY,
                patch="@@ -1 +1 @@\n-old\n+new",
            )
        ],
    )
    verification = CodingVerificationResult(
        attempted=True,
        passed=passed,
        checks_passed=passed,
        finalized=passed,
        rolled_back=not passed and rollback,
        failure_kind=(None if passed else CodingVerificationFailureKind.CHECK_FAILED),
        rollback_failure=(None if rollback else CodingVerificationFailureKind.ROLLBACK_FAILED),
        message="verification completed",
    )
    return dict(
        task="Fix parser regression",
        routing_evaluation=evaluation,
        repository_state=repository,
        coding_context=context,
        coding_attempt=CodingAttemptResult(
            provider="gemini", attempted=True, succeeded=True, proposal=proposal
        ),
        patch_validation=PatchValidationResult(valid=True),
        patch_apply=PatchApplyResult(attempted=True, succeeded=True, message="applied"),
        verification=verification,
    )


def test_pass_and_gemini_failure_do_not_call_codex(tmp_path: Path):
    for inputs in (_inputs(tmp_path, passed=True), _inputs(tmp_path, Route.GEMINI)):
        runtime = FakeCodexRuntime()
        result = process_verified_coding_outcome(
            **inputs,
            codex_runtime=runtime,
            codex_execution_policy=CodexExecutionPolicy(enabled=True),
        )
        assert runtime.health_calls == runtime.execute_calls == 0
        assert not result.attempted_codex
    assert result.outcome == PostFailurePipelineOutcome.ESCALATION_NOT_ALLOWED


def test_gemini_to_codex_disabled_success_and_rollback_failure(tmp_path: Path):
    disabled_runtime = FakeCodexRuntime()
    disabled = process_verified_coding_outcome(
        **_inputs(tmp_path),
        codex_runtime=disabled_runtime,
        codex_execution_policy=CodexExecutionPolicy(enabled=False),
    )
    assert disabled.handoff is not None and disabled.escalation.should_escalate
    assert disabled.outcome == PostFailurePipelineOutcome.CODEX_EXECUTION_DISABLED
    assert disabled_runtime.health_calls == disabled_runtime.execute_calls == 0

    success_runtime = FakeCodexRuntime()
    success = process_verified_coding_outcome(
        **_inputs(tmp_path),
        codex_runtime=success_runtime,
        codex_execution_policy=CodexExecutionPolicy(enabled=True, timeout_seconds=37),
    )
    assert success.outcome == PostFailurePipelineOutcome.CODEX_EXECUTION_SUCCEEDED
    assert success_runtime.health_calls == success_runtime.execute_calls == 1
    assert success_runtime.last_request.handoff.task == "Fix parser regression"
    assert success_runtime.last_request.handoff.coding_attempt.proposal_summary == "Fix parser"
    assert success_runtime.last_request.handoff.patch_attempt.diffs == ["@@ -1 +1 @@\n-old\n+new"]
    assert success_runtime.last_request.handoff.verification.failure_kind == "check_failed"
    assert success_runtime.last_request.handoff.verification.rollback_succeeded is True
    assert success_runtime.last_request.timeout_seconds == 37

    uncertain_runtime = FakeCodexRuntime()
    uncertain = process_verified_coding_outcome(
        **_inputs(tmp_path, rollback=False),
        codex_runtime=uncertain_runtime,
        codex_execution_policy=CodexExecutionPolicy(enabled=True),
    )
    assert uncertain.outcome == PostFailurePipelineOutcome.WORKSPACE_UNCERTAIN
    assert uncertain_runtime.health_calls == uncertain_runtime.execute_calls == 0


def test_explicit_write_authorization_selects_only_controlled_mode(tmp_path: Path):
    controlled = FakeControlledPipeline()
    read_only = FakeCodexRuntime()
    result = process_verified_coding_outcome(
        **_inputs(tmp_path),
        codex_runtime=read_only,
        codex_execution_policy=CodexExecutionPolicy(enabled=True),
        codex_write_policy=CodexWritePolicy(enabled=True),
        codex_write_authorization=CodexWriteAuthorization(authorized=True),
        codex_write_paths=("car/parser.py",),
        verification_plan=VerificationPlan(),
        controlled_write_pipeline=controlled,
    )
    assert not controlled.calls and read_only.execute_calls == 1

    plan = VerificationPlan(commands=[])
    # A non-empty plan is required; use the existing handoff plan shape only for scope gating.
    plan.commands.append(
        type("Command", (), {"args": ["pytest"], "cwd": str(tmp_path), "timeout_seconds": 1})()
    )
    result = process_verified_coding_outcome(
        **_inputs(tmp_path),
        codex_runtime=read_only,
        codex_execution_policy=CodexExecutionPolicy(enabled=True),
        codex_write_policy=CodexWritePolicy(enabled=True),
        codex_write_authorization=CodexWriteAuthorization(authorized=True),
        codex_write_paths=("car/parser.py",),
        verification_plan=plan,
        controlled_write_pipeline=controlled,
    )
    assert result.succeeded and result.selected_codex_mode == "controlled_write"
    assert controlled.calls == [(("car/parser.py",), result.handoff)]


def test_runtime_readiness_and_failure_evidence_are_preserved(tmp_path: Path):
    for status in (
        CodexRuntimeHealthStatus.CLI_NOT_FOUND,
        CodexRuntimeHealthStatus.NOT_AUTHENTICATED,
        CodexRuntimeHealthStatus.UNKNOWN,
    ):
        runtime = FakeCodexRuntime(status)
        result = process_verified_coding_outcome(
            **_inputs(tmp_path),
            codex_runtime=runtime,
            codex_execution_policy=CodexExecutionPolicy(enabled=True),
        )
        assert result.outcome == PostFailurePipelineOutcome.CODEX_EXECUTION_FAILED
        assert result.codex_execution.application_result.health_status == status
        assert runtime.health_calls == 1 and runtime.execute_calls == 0

    for failure in (CodexRuntimeFailureKind.TIMEOUT, CodexRuntimeFailureKind.NONZERO_EXIT):
        runtime = FakeCodexRuntime(
            execution=CodexExecutionResult(attempted=True, succeeded=False, failure_kind=failure)
        )
        result = process_verified_coding_outcome(
            **_inputs(tmp_path),
            codex_runtime=runtime,
            codex_execution_policy=CodexExecutionPolicy(enabled=True),
        )
        assert result.codex_execution.application_result.execution.failure_kind == failure
        assert runtime.execute_calls == 1


def test_pipeline_is_read_only_without_subprocess_or_persistence(tmp_path: Path, monkeypatch):
    tracked = tmp_path / "tracked.py"
    tracked.write_bytes(b"original")
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*")}

    def fail(*args, **kwargs):
        raise AssertionError("pipeline must not execute subprocesses")

    monkeypatch.setattr(subprocess, "run", fail)
    runtime = FakeCodexRuntime()
    result = process_verified_coding_outcome(
        **_inputs(tmp_path),
        codex_runtime=runtime,
        codex_execution_policy=CodexExecutionPolicy(enabled=True),
    )
    after = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*")}
    assert result.succeeded and after == before
    assert not (tmp_path / ".car-context").exists()


def test_pipeline_source_has_no_provider_patch_verification_or_markdown_execution():
    source = inspect.getsource(process_verified_coding_outcome)
    for forbidden in (
        "Gemini",
        "SafePatchApplier",
        "VerificationEngine",
        "render_codex_handoff_markdown",
        "write_codex_handoff",
        "subprocess",
        "OPENAI_API_KEY",
    ):
        assert forbidden not in source
