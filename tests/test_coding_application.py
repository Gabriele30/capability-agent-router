"""Offline tests for the fail-closed application coding-execution gate."""

import os
import subprocess
from pathlib import Path

import pytest

import car.application.coding_execution as coding_execution
from car.application.coding_execution import (
    CodingPipelineApplicationFailureKind,
    CodingPipelineExecutionPolicy,
    execute_authorized_coding_pipeline,
)
from car.coding.base import CodingProviderFailure
from car.coding.models import (
    CodingFileContext,
    CodingProposal,
    CodingTaskContext,
    FileChangeOperation,
    ProposedFileChange,
)
from car.coding.verification import CodingVerificationCoordinator, CodingVerificationFailureKind
from car.execution.models import CommandResult, CommandSpec
from car.patching.apply import SafePatchApplier
from car.providers.models import (
    ProviderCapabilities,
    ProviderErrorKind,
    ProviderHealth,
    ProviderStatus,
    RepositoryClassificationContext,
)
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


class FakeEngine:
    def __init__(self, status: VerificationStatus, *, timed_out: bool = False) -> None:
        self.status = status
        self.timed_out = timed_out

    def verify(self, plan, stop_on_failure=True):
        checks = [CommandResult(command=plan.commands[0], timed_out=True)] if self.timed_out else []
        return VerificationResult(status=self.status, checks=checks, message="test")


class FailingRollbackApplier:
    def apply(self, repository_root, patch_set):
        transaction = SafePatchApplier().apply(repository_root, patch_set)
        transaction.rollback = lambda: False
        return transaction


def _evaluation(route: Route) -> RoutingEvaluation:
    decision = RoutingDecision(
        route=route,
        risk=RiskAssessment(score=0.2, level=RiskLevel.LOW),
        complexity=Complexity.LOW,
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
        deterministic_risk=0.2,
        final_risk=0.2,
        fusion_reasons=["test"],
        decision_sources=[DecisionSource.DETERMINISTIC],
    )


def _context(route: Route, content: str = "value = 1\n") -> CodingTaskContext:
    return CodingTaskContext(
        task="change",
        route=route,
        repository=RepositoryClassificationContext(
            name="repo", dirty=True, languages={"Python": 1}, systems=["Python"]
        ),
        files=[CodingFileContext(path="a.py", content=content)],
    )


def _proposal(path="a.py", patch=None, operation=FileChangeOperation.MODIFY) -> CodingProposal:
    return CodingProposal(
        summary="change",
        changes=[
            ProposedFileChange(
                path=path,
                operation=operation,
                patch=patch or "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n",
            )
        ],
    )


def _plan(root: Path) -> VerificationPlan:
    return VerificationPlan(
        commands=[CommandSpec(args=["ruff", "check"], cwd=str(root), timeout_seconds=10)]
    )


def _execute(root: Path, route: Route, provider: FakeProvider, **kwargs):
    return execute_authorized_coding_pipeline(
        repository_root=root,
        routing_evaluation=_evaluation(route),
        coding_context=kwargs.pop("context", _context(route)),
        coding_provider=provider,
        coding_policy=None,
        patch_validation_policy=None,
        verification_plan=kwargs.pop("verification_plan", _plan(root)),
        execution_policy=kwargs.pop(
            "execution_policy", CodingPipelineExecutionPolicy(enabled=True)
        ),
        **kwargs,
    )


def test_default_policy_is_disabled_without_provider_or_workspace_side_effects(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_bytes(b"value = 1\n")
    provider = FakeProvider(_proposal())

    result = _execute(tmp_path, Route.GEMINI, provider, execution_policy=None)

    assert CodingPipelineExecutionPolicy().enabled is False
    assert result.failure_kind == CodingPipelineApplicationFailureKind.EXECUTION_DISABLED
    assert result.pipeline_result is None and provider.calls == 0
    assert target.read_bytes() == b"value = 1\n"
    assert not (tmp_path / ".car-context").exists()


@pytest.mark.parametrize("route", [Route.GEMINI, Route.GEMINI_TO_CODEX])
def test_enabled_eligible_routes_complete_once(tmp_path: Path, route: Route):
    target = tmp_path / "a.py"
    target.write_text("value = 1\n", encoding="utf-8")
    provider = FakeProvider(_proposal())

    result = _execute(
        tmp_path,
        route,
        provider,
        verification_coordinator=CodingVerificationCoordinator(
            FakeEngine(VerificationStatus.PASSED)
        ),
    )

    assert result.attempted and result.succeeded and result.pipeline_result is not None
    assert provider.calls == 1 and target.read_text(encoding="utf-8") == "value = 2\n"
    assert not (tmp_path / ".car-context").exists()


def test_enabled_create_success_persists_after_verification(tmp_path: Path):
    (tmp_path / "generated").mkdir()
    proposal = _proposal(
        "generated/new.py",
        "--- /dev/null\n+++ b/generated/new.py\n@@ -0,0 +1 @@\n+value = 1\n",
        FileChangeOperation.CREATE,
    )
    result = _execute(
        tmp_path,
        Route.GEMINI,
        FakeProvider(proposal),
        context=CodingTaskContext(
            task="create",
            route=Route.GEMINI,
            repository=RepositoryClassificationContext(
                name="repo", dirty=False, languages={}, systems=[]
            ),
        ),
        verification_coordinator=CodingVerificationCoordinator(
            FakeEngine(VerificationStatus.PASSED)
        ),
    )

    assert result.succeeded
    assert (tmp_path / "generated" / "new.py").read_text(encoding="utf-8") == "value = 1\n"


def test_enabled_provider_validation_and_apply_failures_are_preserved(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_text("value = 1\n", encoding="utf-8")
    provider_failure = _execute(
        tmp_path, Route.GEMINI, FakeProvider(error=ProviderErrorKind.TIMEOUT)
    )
    assert provider_failure.pipeline_result is not None
    assert provider_failure.pipeline_result.coding_attempt.error_kind == ProviderErrorKind.TIMEOUT

    invalid = _execute(
        tmp_path,
        Route.GEMINI,
        FakeProvider(_proposal("other.py")),
    )
    assert invalid.pipeline_result is not None
    assert invalid.pipeline_result.patch_validation is not None

    apply_failure = _execute(
        tmp_path,
        Route.GEMINI,
        FakeProvider(_proposal()),
        patch_applier=SafePatchApplier(
            write_bytes=lambda path, content: (_ for _ in ()).throw(OSError())
        ),
    )
    assert apply_failure.pipeline_result is not None
    assert apply_failure.pipeline_result.patch_apply is not None
    assert apply_failure.pipeline_result.verification is None
    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_verification_failures_timeout_and_dirty_rollback_are_preserved(tmp_path: Path):
    target, unrelated = tmp_path / "a.py", tmp_path / "notes.txt"
    dirty = "value = 1  # user\n"
    target.write_text(dirty, encoding="utf-8")
    unrelated.write_text("keep\n", encoding="utf-8")
    patch = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-value = 1  # user\n+value = 2  # user\n"
    context = _context(Route.GEMINI, dirty)

    failed = _execute(
        tmp_path,
        Route.GEMINI,
        FakeProvider(_proposal(patch=patch)),
        context=context,
        verification_coordinator=CodingVerificationCoordinator(
            FakeEngine(VerificationStatus.FAILED)
        ),
    )
    assert failed.pipeline_result is not None and failed.pipeline_result.verification is not None
    assert failed.pipeline_result.verification.rolled_back
    assert target.read_text(encoding="utf-8") == dirty
    assert unrelated.read_text(encoding="utf-8") == "keep\n"

    timeout = _execute(
        tmp_path,
        Route.GEMINI,
        FakeProvider(_proposal(patch=patch)),
        context=context,
        verification_coordinator=CodingVerificationCoordinator(
            FakeEngine(VerificationStatus.FAILED, timed_out=True)
        ),
    )
    assert timeout.pipeline_result is not None and timeout.pipeline_result.verification is not None
    assert (
        timeout.pipeline_result.verification.failure_kind
        == CodingVerificationFailureKind.CHECK_TIMEOUT
    )
    assert target.read_text(encoding="utf-8") == dirty


def test_rollback_failure_is_preserved_without_escalation(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_text("value = 1\n", encoding="utf-8")
    result = _execute(
        tmp_path,
        Route.GEMINI_TO_CODEX,
        FakeProvider(_proposal()),
        patch_applier=FailingRollbackApplier(),
        verification_coordinator=CodingVerificationCoordinator(
            FakeEngine(VerificationStatus.FAILED)
        ),
    )

    assert result.failure_kind == CodingPipelineApplicationFailureKind.PIPELINE_FAILED
    assert result.pipeline_result is not None and result.pipeline_result.verification is not None
    assert (
        result.pipeline_result.verification.rollback_failure
        == CodingVerificationFailureKind.ROLLBACK_FAILED
    )


@pytest.mark.parametrize("route", [Route.L0, Route.CODEX, Route.PLAN])
def test_ineligible_routes_do_not_call_provider(tmp_path: Path, route: Route):
    provider = FakeProvider(_proposal())
    result = _execute(tmp_path, route, provider)

    assert result.failure_kind == CodingPipelineApplicationFailureKind.ROUTE_NOT_ELIGIBLE
    assert result.pipeline_result is not None and provider.calls == 0


def test_wrapper_has_no_direct_subprocess_environment_or_concrete_provider_access(
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

    result = _execute(
        tmp_path,
        Route.GEMINI,
        FakeProvider(_proposal()),
        verification_coordinator=CodingVerificationCoordinator(
            FakeEngine(VerificationStatus.PASSED)
        ),
    )
    source = Path(coding_execution.__file__).read_text(encoding="utf-8")

    assert result.succeeded
    assert all(
        token not in source
        for token in (
            "GeminiCodingProvider",
            "google.genai",
            "car.codex",
            "subprocess",
            ".car-context",
        )
    )
    assert all(
        token not in source for token in ("write_text", "write_bytes", "open(", "VerificationPlan(")
    )
