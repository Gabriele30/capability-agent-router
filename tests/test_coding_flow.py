"""Offline end-to-end composition tests for coding failure to Codex diagnostics."""

import os
import subprocess
from pathlib import Path

import pytest

import car.application.coding_flow as coding_flow
from car.application.codex import CodexExecutionPolicy
from car.application.coding_execution import CodingPipelineExecutionPolicy
from car.application.coding_flow import CodingFlowOutcome, execute_coding_flow
from car.codex.models import (
    CodexExecutionResult,
    CodexRuntimeFailureKind,
    CodexRuntimeHealth,
    CodexRuntimeHealthStatus,
)
from car.coding.base import CodingProviderFailure
from car.coding.models import (
    CodingFileContext,
    CodingProposal,
    CodingTaskContext,
    FileChangeOperation,
    ProposedFileChange,
)
from car.coding.verification import CodingVerificationCoordinator
from car.execution.models import CommandResult, CommandSpec
from car.patching.apply import SafePatchApplier
from car.providers.models import (
    ProviderCapabilities,
    ProviderErrorKind,
    ProviderHealth,
    ProviderStatus,
    RepositoryClassificationContext,
)
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
from car.verification.models import VerificationPlan, VerificationResult, VerificationStatus


class FakeProvider:
    name = "fake-gemini"

    def __init__(self, proposal=None, error=None) -> None:
        self.proposal = proposal
        self.error = error
        self.calls = 0

    def capabilities(self):
        return ProviderCapabilities(supports_code_changes=True)

    def health(self):
        return ProviderHealth(status=ProviderStatus.CONFIGURED)

    def propose(self, context):
        self.calls += 1
        if self.error is not None:
            raise CodingProviderFailure(self.error)
        return self.proposal


class FakeVerificationEngine:
    def __init__(self, status: VerificationStatus) -> None:
        self.status = status
        self.calls = 0

    def verify(self, plan, stop_on_failure=True):
        self.calls += 1
        checks = []
        if self.status == VerificationStatus.FAILED:
            checks = [CommandResult(command=plan.commands[0], exit_code=1, stderr="test")]
        return VerificationResult(status=self.status, checks=checks, message="test")


class FailingRollbackApplier:
    def apply(self, repository_root, patch_set):
        transaction = SafePatchApplier().apply(repository_root, patch_set)
        transaction.rollback = lambda: False
        return transaction


class FakeCodexRuntime:
    def __init__(self, root: Path, execution=None) -> None:
        self.root = root
        self.execution = execution or CodexExecutionResult(
            attempted=True, succeeded=True, final_message="diagnostic"
        )
        self.health_calls = 0
        self.execute_calls = 0
        self.last_request = None
        self.workspace_at_execute = None

    def health(self):
        self.health_calls += 1
        return CodexRuntimeHealth(status=CodexRuntimeHealthStatus.READY)

    def execute(self, request):
        self.execute_calls += 1
        self.last_request = request
        self.workspace_at_execute = (self.root / "a.py").read_bytes()
        return self.execution


def _evaluation(route: Route) -> RoutingEvaluation:
    decision = RoutingDecision(
        route=route,
        risk=RiskAssessment(score=0.4, level=RiskLevel.MEDIUM),
        complexity=Complexity.MEDIUM,
        scope=ScopeEstimate(size=ScopeSize.MEDIUM),
        confidence=0.8,
        reasons=["test"],
        matched_rules=["test"],
        categories=[TaskCategory.BUGFIX],
    )
    return RoutingEvaluation(
        deterministic_decision=decision,
        provider_consultation=ProviderConsultationResult(attempted=False, succeeded=False),
        final_decision=decision,
        deterministic_risk=0.4,
        final_risk=0.4,
        fusion_reasons=["test"],
        decision_sources=[DecisionSource.DETERMINISTIC],
    )


def _repository(root: Path) -> RepositoryState:
    return RepositoryState(
        root=root,
        name="repo",
        git=GitState(available=True, is_repository=True, branch="main", modified_files=["a.py"]),
        languages=LanguageStats(counts={"Python": 1}),
        project_signals=ProjectSignals(systems=["Python"]),
    )


def _context(route: Route, content: str) -> CodingTaskContext:
    return CodingTaskContext(
        task="Fix parser regression",
        route=route,
        repository=RepositoryClassificationContext(
            name="repo", branch="main", dirty=True, languages={"Python": 1}, systems=["Python"]
        ),
        files=[CodingFileContext(path="a.py", content=content)],
    )


def _proposal(content: str, replacement: str = "value = 2\n") -> CodingProposal:
    return CodingProposal(
        summary="Fix parser regression",
        changes=[
            ProposedFileChange(
                path="a.py",
                operation=FileChangeOperation.MODIFY,
                patch=(
                    "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n"
                    f"-{content.rstrip()}\n+{replacement.rstrip()}\n"
                ),
            )
        ],
    )


def _plan(root: Path) -> VerificationPlan:
    return VerificationPlan(
        commands=[CommandSpec(args=["ruff", "check", "a.py"], cwd=str(root), timeout_seconds=10)]
    )


def _flow(root: Path, route: Route, provider: FakeProvider, runtime: FakeCodexRuntime, **kwargs):
    content = kwargs.pop("content", "value = 1\n")
    return execute_coding_flow(
        repository_root=root,
        routing_evaluation=_evaluation(route),
        repository_state=_repository(root),
        coding_context=kwargs.pop("context", _context(route, content)),
        coding_provider=provider,
        coding_policy=None,
        patch_validation_policy=None,
        verification_plan=_plan(root),
        coding_execution_policy=kwargs.pop(
            "coding_execution_policy", CodingPipelineExecutionPolicy(enabled=True)
        ),
        handoff_policy=None,
        codex_runtime=runtime,
        codex_execution_policy=kwargs.pop(
            "codex_execution_policy", CodexExecutionPolicy(enabled=True)
        ),
        **kwargs,
    )


def test_disabled_and_ineligible_routes_do_not_enter_post_failure(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_bytes(b"value = 1\n")
    disabled_provider = FakeProvider(_proposal("value = 1\n"))
    disabled_runtime = FakeCodexRuntime(tmp_path)
    disabled = _flow(
        tmp_path,
        Route.GEMINI,
        disabled_provider,
        disabled_runtime,
        coding_execution_policy=CodingPipelineExecutionPolicy(),
    )
    assert disabled.outcome == CodingFlowOutcome.CODING_EXECUTION_DISABLED
    assert (
        disabled.post_failure is None
        and disabled_provider.calls == disabled_runtime.execute_calls == 0
    )

    for route in (Route.L0, Route.CODEX, Route.PLAN):
        provider, runtime = FakeProvider(_proposal("value = 1\n")), FakeCodexRuntime(tmp_path)
        result = _flow(tmp_path, route, provider, runtime)
        assert result.outcome == CodingFlowOutcome.ROUTE_NOT_ELIGIBLE
        assert result.post_failure is None and provider.calls == runtime.execute_calls == 0
    assert target.read_bytes() == b"value = 1\n" and not (tmp_path / ".car-context").exists()


@pytest.mark.parametrize("route", [Route.GEMINI, Route.GEMINI_TO_CODEX])
def test_coding_success_never_calls_codex(tmp_path: Path, route: Route):
    target = tmp_path / "a.py"
    target.write_text("value = 1\n", encoding="utf-8")
    runtime = FakeCodexRuntime(tmp_path)
    result = _flow(
        tmp_path,
        route,
        FakeProvider(_proposal("value = 1\n")),
        runtime,
        verification_coordinator=CodingVerificationCoordinator(
            FakeVerificationEngine(VerificationStatus.PASSED)
        ),
    )
    assert result.succeeded and result.outcome == CodingFlowOutcome.CODING_SUCCEEDED
    assert result.post_failure is None and runtime.health_calls == runtime.execute_calls == 0
    assert target.read_text(encoding="utf-8") == "value = 2\n"


def test_gemini_failure_and_verified_failure_do_not_escalate(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_text("value = 1\n", encoding="utf-8")
    provider_failure_runtime = FakeCodexRuntime(tmp_path)
    provider_failure = _flow(
        tmp_path,
        Route.GEMINI,
        FakeProvider(error=ProviderErrorKind.TIMEOUT),
        provider_failure_runtime,
    )
    assert provider_failure.outcome == CodingFlowOutcome.CODING_FAILED_NO_ESCALATION
    assert provider_failure.post_failure is None and provider_failure_runtime.execute_calls == 0

    runtime = FakeCodexRuntime(tmp_path)
    verification_failure = _flow(
        tmp_path,
        Route.GEMINI,
        FakeProvider(_proposal("value = 1\n")),
        runtime,
        verification_coordinator=CodingVerificationCoordinator(
            FakeVerificationEngine(VerificationStatus.FAILED)
        ),
    )
    assert verification_failure.post_failure is not None
    assert not verification_failure.post_failure.escalation.should_escalate
    assert runtime.execute_calls == 0 and target.read_text(encoding="utf-8") == "value = 1\n"


def test_gemini_to_codex_verified_failure_builds_handoff_before_one_read_only_analysis(
    tmp_path: Path,
):
    target, unrelated = tmp_path / "a.py", tmp_path / "notes.txt"
    dirty = "value = 1  # user\n"
    target.write_bytes(dirty.encode())
    unrelated.write_bytes(b"keep\n")
    runtime = FakeCodexRuntime(tmp_path)
    result = _flow(
        tmp_path,
        Route.GEMINI_TO_CODEX,
        FakeProvider(_proposal(dirty, "value = 2  # user\n")),
        runtime,
        content=dirty,
        verification_coordinator=CodingVerificationCoordinator(
            FakeVerificationEngine(VerificationStatus.FAILED)
        ),
    )
    handoff = runtime.last_request.handoff
    assert result.outcome == CodingFlowOutcome.CODEX_ANALYSIS_SUCCEEDED and not result.succeeded
    assert runtime.health_calls == runtime.execute_calls == 1
    assert runtime.workspace_at_execute == dirty.encode()
    assert (
        target.read_text(encoding="utf-8") == dirty
        and unrelated.read_text(encoding="utf-8") == "keep\n"
    )
    assert handoff.task == "Fix parser regression"
    assert handoff.coding_attempt.provider == "fake-gemini"
    assert handoff.coding_attempt.proposal_summary == "Fix parser regression"
    assert handoff.patch_attempt.diffs and handoff.verification.rollback_succeeded is True
    assert not (tmp_path / ".car-context").exists()


def test_codex_disabled_runtime_failures_and_rollback_failure_are_preserved(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_text("value = 1\n", encoding="utf-8")
    disabled_runtime = FakeCodexRuntime(tmp_path)
    disabled = _flow(
        tmp_path,
        Route.GEMINI_TO_CODEX,
        FakeProvider(_proposal("value = 1\n")),
        disabled_runtime,
        verification_coordinator=CodingVerificationCoordinator(
            FakeVerificationEngine(VerificationStatus.FAILED)
        ),
        codex_execution_policy=CodexExecutionPolicy(enabled=False),
    )
    assert disabled.outcome == CodingFlowOutcome.CODEX_EXECUTION_DISABLED
    assert disabled.post_failure.handoff is not None and disabled_runtime.health_calls == 0

    for failure in (CodexRuntimeFailureKind.TIMEOUT, CodexRuntimeFailureKind.NONZERO_EXIT):
        runtime = FakeCodexRuntime(
            tmp_path,
            CodexExecutionResult(attempted=True, succeeded=False, failure_kind=failure),
        )
        result = _flow(
            tmp_path,
            Route.GEMINI_TO_CODEX,
            FakeProvider(_proposal("value = 1\n")),
            runtime,
            verification_coordinator=CodingVerificationCoordinator(
                FakeVerificationEngine(VerificationStatus.FAILED)
            ),
        )
        assert result.outcome == CodingFlowOutcome.CODEX_ANALYSIS_FAILED
        assert (
            result.post_failure.codex_execution.application_result.execution.failure_kind == failure
        )

    uncertain_runtime = FakeCodexRuntime(tmp_path)
    uncertain = _flow(
        tmp_path,
        Route.GEMINI_TO_CODEX,
        FakeProvider(_proposal("value = 1\n")),
        uncertain_runtime,
        patch_applier=FailingRollbackApplier(),
        verification_coordinator=CodingVerificationCoordinator(
            FakeVerificationEngine(VerificationStatus.FAILED)
        ),
    )
    assert uncertain.outcome == CodingFlowOutcome.WORKSPACE_UNCERTAIN
    assert uncertain_runtime.health_calls == uncertain_runtime.execute_calls == 0


def test_flow_has_no_direct_environment_subprocess_or_provider_specific_access(
    monkeypatch, tmp_path: Path
):
    target = tmp_path / "a.py"
    target.write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError())
    )
    monkeypatch.setattr(
        os, "getenv", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError())
    )
    result = _flow(
        tmp_path,
        Route.GEMINI,
        FakeProvider(_proposal("value = 1\n")),
        FakeCodexRuntime(tmp_path),
        verification_coordinator=CodingVerificationCoordinator(
            FakeVerificationEngine(VerificationStatus.PASSED)
        ),
    )
    source = Path(coding_flow.__file__).read_text(encoding="utf-8")
    assert result.succeeded
    assert all(
        token not in source
        for token in (
            "GeminiCodingProvider",
            "google.genai",
            "subprocess",
            "os.getenv",
            ".car-context",
            "VerificationPlan(",
            "write_text",
            "write_bytes",
            "open(",
        )
    )
