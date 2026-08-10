"""Offline tests for explicit user authorization of the coding flow."""

import os
import subprocess
from pathlib import Path

import pytest

import car.application.execution_gateway as execution_gateway
from car.application.codex import CodexExecutionPolicy
from car.application.coding_execution import CodingPipelineExecutionPolicy
from car.application.coding_flow import CodingFlowOutcome
from car.application.execution_gateway import (
    CodingFlowAuthorization,
    CodingFlowExecutionRequest,
    CodingFlowGateway,
    CodingFlowGatewayFailureKind,
)
from car.codex.models import CodexExecutionResult, CodexRuntimeHealth, CodexRuntimeHealthStatus
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
    name = "fake"

    def __init__(self, proposal) -> None:
        self.proposal = proposal
        self.calls = 0

    def capabilities(self):
        return ProviderCapabilities(supports_code_changes=True)

    def health(self):
        return ProviderHealth(status=ProviderStatus.CONFIGURED)

    def propose(self, context):
        self.calls += 1
        return self.proposal


class FakeVerificationEngine:
    def __init__(self, status: VerificationStatus) -> None:
        self.status = status

    def verify(self, plan, stop_on_failure=True):
        checks = []
        if self.status == VerificationStatus.FAILED:
            checks = [CommandResult(command=plan.commands[0], exit_code=1)]
        return VerificationResult(status=self.status, checks=checks, message="test")


class FakeCodexRuntime:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.health_calls = 0
        self.execute_calls = 0
        self.workspace_at_execute = None

    def health(self):
        self.health_calls += 1
        return CodexRuntimeHealth(status=CodexRuntimeHealthStatus.READY)

    def execute(self, request):
        self.execute_calls += 1
        self.workspace_at_execute = (self.root / "a.py").read_bytes()
        return CodexExecutionResult(attempted=True, succeeded=True, final_message="diagnostic")


class FailingRollbackApplier:
    def apply(self, repository_root, patch_set):
        transaction = SafePatchApplier().apply(repository_root, patch_set)
        transaction.rollback = lambda: False
        return transaction


def _evaluation(route: Route) -> RoutingEvaluation:
    decision = RoutingDecision(
        route=route,
        risk=RiskAssessment(score=0.3, level=RiskLevel.MEDIUM),
        complexity=Complexity.MEDIUM,
        scope=ScopeEstimate(size=ScopeSize.SMALL),
        confidence=0.8,
        reasons=["test"],
        matched_rules=["test"],
        categories=[TaskCategory.BUGFIX],
    )
    return RoutingEvaluation(
        deterministic_decision=decision,
        provider_consultation=ProviderConsultationResult(attempted=False, succeeded=False),
        final_decision=decision,
        deterministic_risk=0.3,
        final_risk=0.3,
        fusion_reasons=["test"],
        decision_sources=[DecisionSource.DETERMINISTIC],
    )


def _request(root: Path, route: Route, content: str, *, enabled=True, codex_enabled=True):
    return CodingFlowExecutionRequest(
        repository_root=root,
        routing_evaluation=_evaluation(route),
        repository_state=RepositoryState(
            root=root,
            name="repo",
            git=GitState(available=True, is_repository=True, branch="main"),
            languages=LanguageStats(counts={"Python": 1}),
            project_signals=ProjectSignals(systems=["Python"]),
        ),
        coding_context=CodingTaskContext(
            task="Fix parser regression",
            route=route,
            repository=RepositoryClassificationContext(
                name="repo", dirty=True, languages={"Python": 1}, systems=["Python"]
            ),
            files=[CodingFileContext(path="a.py", content=content)],
        ),
        coding_policy=None,
        patch_validation_policy=None,
        verification_plan=VerificationPlan(
            commands=[
                CommandSpec(args=["ruff", "check", "a.py"], cwd=str(root), timeout_seconds=10)
            ]
        ),
        coding_execution_policy=CodingPipelineExecutionPolicy(enabled=enabled),
        handoff_policy=None,
        codex_execution_policy=CodexExecutionPolicy(enabled=codex_enabled),
    )


def _proposal(content: str, replacement: str = "value = 2\n") -> CodingProposal:
    return CodingProposal(
        summary="Fix parser",
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


def _gateway(root: Path, provider: FakeProvider, runtime: FakeCodexRuntime, status):
    return CodingFlowGateway(
        provider,
        runtime,
        verification_coordinator=CodingVerificationCoordinator(FakeVerificationEngine(status)),
    )


def test_default_authorization_is_fail_closed_without_side_effects(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_bytes(b"value = 1\n")
    provider, runtime = FakeProvider(_proposal("value = 1\n")), FakeCodexRuntime(tmp_path)
    result = _gateway(tmp_path, provider, runtime, VerificationStatus.PASSED).execute(
        _request(tmp_path, Route.GEMINI, "value = 1\n")
    )
    assert CodingFlowAuthorization().authorized is False
    assert result.failure_kind == CodingFlowGatewayFailureKind.NOT_AUTHORIZED
    assert result.flow_result is None and provider.calls == runtime.execute_calls == 0
    assert target.read_bytes() == b"value = 1\n" and not (tmp_path / ".car-context").exists()


def test_authorized_execution_policy_remains_separate(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_bytes(b"value = 1\n")
    provider, runtime = FakeProvider(_proposal("value = 1\n")), FakeCodexRuntime(tmp_path)
    result = _gateway(tmp_path, provider, runtime, VerificationStatus.PASSED).execute(
        _request(tmp_path, Route.GEMINI, "value = 1\n", enabled=False),
        CodingFlowAuthorization(authorized=True),
    )
    assert result.authorized and not result.attempted and not result.succeeded
    assert result.flow_result.outcome == CodingFlowOutcome.CODING_EXECUTION_DISABLED
    assert provider.calls == runtime.execute_calls == 0


@pytest.mark.parametrize("route", [Route.GEMINI, Route.GEMINI_TO_CODEX])
def test_authorized_success_keeps_verified_patch_and_skips_codex(tmp_path: Path, route: Route):
    target = tmp_path / "a.py"
    target.write_text("value = 1\n", encoding="utf-8")
    provider, runtime = FakeProvider(_proposal("value = 1\n")), FakeCodexRuntime(tmp_path)
    result = _gateway(tmp_path, provider, runtime, VerificationStatus.PASSED).execute(
        _request(tmp_path, route, "value = 1\n"), CodingFlowAuthorization(authorized=True)
    )
    assert result.authorized and result.attempted and result.succeeded
    assert provider.calls == 1 and runtime.health_calls == runtime.execute_calls == 0
    assert target.read_text(encoding="utf-8") == "value = 2\n"


def test_authorized_failures_preserve_rollback_and_cannot_change_task_success(tmp_path: Path):
    target, unrelated = tmp_path / "a.py", tmp_path / "notes.txt"
    dirty = "value = 1  # user\n"
    target.write_bytes(dirty.encode())
    unrelated.write_bytes(b"keep\n")
    provider, runtime = (
        FakeProvider(_proposal(dirty, "value = 2  # user\n")),
        FakeCodexRuntime(tmp_path),
    )
    gemini_failure = _gateway(tmp_path, provider, runtime, VerificationStatus.FAILED).execute(
        _request(tmp_path, Route.GEMINI, dirty), CodingFlowAuthorization(authorized=True)
    )
    assert gemini_failure.flow_result.post_failure is not None
    assert runtime.execute_calls == 0 and target.read_bytes() == dirty.encode()

    provider, runtime = (
        FakeProvider(_proposal(dirty, "value = 2  # user\n")),
        FakeCodexRuntime(tmp_path),
    )
    codex_disabled = _gateway(tmp_path, provider, runtime, VerificationStatus.FAILED).execute(
        _request(tmp_path, Route.GEMINI_TO_CODEX, dirty, codex_enabled=False),
        CodingFlowAuthorization(authorized=True),
    )
    assert codex_disabled.flow_result.outcome == CodingFlowOutcome.CODEX_EXECUTION_DISABLED
    assert runtime.health_calls == runtime.execute_calls == 0

    provider, runtime = (
        FakeProvider(_proposal(dirty, "value = 2  # user\n")),
        FakeCodexRuntime(tmp_path),
    )
    codex_success = _gateway(tmp_path, provider, runtime, VerificationStatus.FAILED).execute(
        _request(tmp_path, Route.GEMINI_TO_CODEX, dirty), CodingFlowAuthorization(authorized=True)
    )
    assert not codex_success.succeeded
    assert codex_success.flow_result.outcome == CodingFlowOutcome.CODEX_ANALYSIS_SUCCEEDED
    assert runtime.health_calls == runtime.execute_calls == 1
    assert runtime.workspace_at_execute == dirty.encode()
    assert target.read_bytes() == dirty.encode() and unrelated.read_bytes() == b"keep\n"
    assert not (tmp_path / ".car-context").exists()


def test_rollback_failure_blocks_codex_without_gateway_recovery(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_text("value = 1\n", encoding="utf-8")
    provider, runtime = FakeProvider(_proposal("value = 1\n")), FakeCodexRuntime(tmp_path)
    gateway = CodingFlowGateway(
        provider,
        runtime,
        patch_applier=FailingRollbackApplier(),
        verification_coordinator=CodingVerificationCoordinator(
            FakeVerificationEngine(VerificationStatus.FAILED)
        ),
    )

    result = gateway.execute(
        _request(tmp_path, Route.GEMINI_TO_CODEX, "value = 1\n"),
        CodingFlowAuthorization(authorized=True),
    )

    assert result.flow_result.outcome == CodingFlowOutcome.WORKSPACE_UNCERTAIN
    assert runtime.health_calls == runtime.execute_calls == 0


def test_gateway_has_no_direct_environment_subprocess_or_filesystem_access(
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
    result = _gateway(
        tmp_path,
        FakeProvider(_proposal("value = 1\n")),
        FakeCodexRuntime(tmp_path),
        VerificationStatus.PASSED,
    ).execute(
        _request(tmp_path, Route.GEMINI, "value = 1\n"), CodingFlowAuthorization(authorized=True)
    )
    source = Path(execution_gateway.__file__).read_text(encoding="utf-8")
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
            "CAR_ALLOW_EXECUTION",
        )
    )
