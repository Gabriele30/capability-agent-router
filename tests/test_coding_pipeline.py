"""Offline integration tests for the internal provider-neutral coding pipeline."""

from pathlib import Path

import pytest

from car.application.coding import CodingPipelineOutcome, execute_coding_pipeline
from car.coding.base import CodingProviderFailure
from car.coding.models import (
    CodingExecutionPolicy,
    CodingFileContext,
    CodingProposal,
    CodingTaskContext,
    FileChangeOperation,
    ProposedFileChange,
)
from car.coding.verification import CodingVerificationCoordinator, CodingVerificationFailureKind
from car.execution.models import CommandResult, CommandSpec
from car.patching.apply import SafePatchApplier
from car.patching.models import PatchValidationPolicy
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

    def __init__(self, proposal=None, error=None, status=ProviderStatus.CONFIGURED):
        self.proposal, self.error, self.status = proposal, error, status
        self.calls = 0

    def capabilities(self):
        return ProviderCapabilities(supports_code_changes=True)

    def health(self):
        return ProviderHealth(status=self.status)

    def propose(self, context):
        self.calls += 1
        if self.error:
            raise CodingProviderFailure(self.error)
        return self.proposal


class FakeEngine:
    def __init__(self, status):
        self.status = status

    def verify(self, plan, stop_on_failure=True):
        return VerificationResult(status=self.status, message="test")


class TimeoutEngine:
    def verify(self, plan, stop_on_failure=True):
        return VerificationResult(
            status=VerificationStatus.FAILED,
            checks=[CommandResult(command=plan.commands[0], timed_out=True)],
            message="test timeout",
        )


class CountingPatchApplier:
    def __init__(self) -> None:
        self.calls = 0
        self._applier = SafePatchApplier()

    def apply(self, repository_root, patch_set):
        self.calls += 1
        return self._applier.apply(repository_root, patch_set)


class CountingVerificationCoordinator:
    def __init__(self, coordinator) -> None:
        self.calls = 0
        self._coordinator = coordinator

    def verify(self, repository_root, transaction, plan):
        self.calls += 1
        return self._coordinator.verify(repository_root, transaction, plan)


def _evaluation(route):
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


def _context(route, files):
    return CodingTaskContext(
        task="change",
        route=route,
        repository=RepositoryClassificationContext(
            name="repo", dirty=False, languages={}, systems=[]
        ),
        files=files,
    )


def _proposal(path, patch, operation=FileChangeOperation.MODIFY):
    return CodingProposal(
        summary="change", changes=[ProposedFileChange(path=path, operation=operation, patch=patch)]
    )


def _plan(root):
    return VerificationPlan(
        commands=[CommandSpec(args=["ruff", "check"], cwd=str(root), timeout_seconds=10)]
    )


@pytest.mark.parametrize("route", [Route.L0, Route.CODEX, Route.PLAN])
def test_route_ineligible_never_calls_provider(tmp_path: Path, route: Route):
    provider = FakeProvider()
    result = execute_coding_pipeline(
        repository_root=tmp_path,
        routing_evaluation=_evaluation(route),
        coding_context=_context(route, []),
        coding_provider=provider,
        coding_policy=None,
        patch_validation_policy=None,
        verification_plan=VerificationPlan(),
    )
    assert result.outcome == CodingPipelineOutcome.ROUTE_NOT_ELIGIBLE and provider.calls == 0


def test_success_modify_and_verification_rollback(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_text("value = 1\n", encoding="utf-8")
    patch = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"
    context = _context(Route.GEMINI, [CodingFileContext(path="a.py", content="value = 1\n")])
    provider = FakeProvider(_proposal("a.py", patch))
    success = execute_coding_pipeline(
        repository_root=tmp_path,
        routing_evaluation=_evaluation(Route.GEMINI),
        coding_context=context,
        coding_provider=provider,
        coding_policy=CodingExecutionPolicy(),
        patch_validation_policy=PatchValidationPolicy(),
        verification_plan=_plan(tmp_path),
        verification_coordinator=CodingVerificationCoordinator(
            FakeEngine(VerificationStatus.PASSED)
        ),
    )
    assert success.succeeded and target.read_text(encoding="utf-8") == "value = 2\n"
    target.write_text("value = 1\n", encoding="utf-8")
    failed = execute_coding_pipeline(
        repository_root=tmp_path,
        routing_evaluation=_evaluation(Route.GEMINI_TO_CODEX),
        coding_context=_context(
            Route.GEMINI_TO_CODEX, [CodingFileContext(path="a.py", content="value = 1\n")]
        ),
        coding_provider=FakeProvider(_proposal("a.py", patch)),
        coding_policy=CodingExecutionPolicy(),
        patch_validation_policy=PatchValidationPolicy(),
        verification_plan=_plan(tmp_path),
        verification_coordinator=CodingVerificationCoordinator(
            FakeEngine(VerificationStatus.FAILED)
        ),
    )
    assert (
        failed.outcome == CodingPipelineOutcome.VERIFICATION_FAILED
        and target.read_text(encoding="utf-8") == "value = 1\n"
    )


def test_success_create_calls_each_pipeline_boundary_once(tmp_path: Path):
    (tmp_path / "generated").mkdir()
    patch = "--- /dev/null\n+++ b/generated/new.py\n@@ -0,0 +1 @@\n+value = 1\n"
    provider = FakeProvider(_proposal("generated/new.py", patch, FileChangeOperation.CREATE))
    applier = CountingPatchApplier()
    coordinator = CountingVerificationCoordinator(
        CodingVerificationCoordinator(FakeEngine(VerificationStatus.PASSED))
    )

    result = execute_coding_pipeline(
        repository_root=tmp_path,
        routing_evaluation=_evaluation(Route.GEMINI),
        coding_context=_context(Route.GEMINI, []),
        coding_provider=provider,
        coding_policy=None,
        patch_validation_policy=None,
        verification_plan=_plan(tmp_path),
        patch_applier=applier,
        verification_coordinator=coordinator,
    )

    assert result.outcome == CodingPipelineOutcome.SUCCEEDED
    assert (tmp_path / "generated" / "new.py").read_text(encoding="utf-8") == "value = 1\n"
    assert provider.calls == 1 and applier.calls == 1 and coordinator.calls == 1


def test_provider_and_validation_failures_do_not_mutate(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_text("value = 1\n", encoding="utf-8")
    context = _context(Route.GEMINI, [CodingFileContext(path="a.py", content="value = 1\n")])
    unavailable = execute_coding_pipeline(
        repository_root=tmp_path,
        routing_evaluation=_evaluation(Route.GEMINI),
        coding_context=context,
        coding_provider=FakeProvider(status=ProviderStatus.DISABLED),
        coding_policy=None,
        patch_validation_policy=None,
        verification_plan=VerificationPlan(),
    )
    assert unavailable.outcome == CodingPipelineOutcome.CODING_PROVIDER_UNAVAILABLE
    invalid = execute_coding_pipeline(
        repository_root=tmp_path,
        routing_evaluation=_evaluation(Route.GEMINI),
        coding_context=context,
        coding_provider=FakeProvider(
            _proposal("other.py", "--- a/other.py\n+++ b/other.py\n@@ -1 +1 @@\n-x\n+y\n")
        ),
        coding_policy=None,
        patch_validation_policy=None,
        verification_plan=VerificationPlan(),
    )
    assert invalid.outcome == CodingPipelineOutcome.PATCH_VALIDATION_FAILED
    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_provider_failure_is_preserved_without_apply_or_verification(tmp_path: Path):
    provider = FakeProvider(error=ProviderErrorKind.TIMEOUT)
    result = execute_coding_pipeline(
        repository_root=tmp_path,
        routing_evaluation=_evaluation(Route.GEMINI),
        coding_context=_context(Route.GEMINI, []),
        coding_provider=provider,
        coding_policy=None,
        patch_validation_policy=None,
        verification_plan=VerificationPlan(),
    )

    assert result.outcome == CodingPipelineOutcome.CODING_PROVIDER_FAILED
    assert result.coding_attempt is not None
    assert result.coding_attempt.error_kind == ProviderErrorKind.TIMEOUT
    assert result.patch_validation is None
    assert result.patch_apply is None
    assert result.verification is None
    assert provider.calls == 1


def test_apply_failure_is_reported_without_verification(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_text("value = 1\n", encoding="utf-8")
    patch = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"
    result = execute_coding_pipeline(
        repository_root=tmp_path,
        routing_evaluation=_evaluation(Route.GEMINI),
        coding_context=_context(
            Route.GEMINI, [CodingFileContext(path="a.py", content="value = 1\n")]
        ),
        coding_provider=FakeProvider(_proposal("a.py", patch)),
        coding_policy=None,
        patch_validation_policy=None,
        verification_plan=VerificationPlan(),
        patch_applier=SafePatchApplier(
            write_bytes=lambda path, content: (_ for _ in ()).throw(OSError())
        ),
    )

    assert result.outcome == CodingPipelineOutcome.PATCH_APPLY_FAILED
    assert result.patch_apply is not None and result.patch_apply.failure_kind is not None
    assert result.verification is None
    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_timeout_and_empty_plan_roll_back_dirty_state_without_touching_unrelated_file(
    tmp_path: Path,
):
    target = tmp_path / "a.py"
    unrelated = tmp_path / "notes.txt"
    dirty = "value = 1  # user change\n"
    target.write_text(dirty, encoding="utf-8")
    unrelated.write_text("keep\n", encoding="utf-8")
    patch = (
        "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n"
        "-value = 1  # user change\n+value = 2  # user change\n"
    )
    context = _context(
        Route.GEMINI,
        [CodingFileContext(path="a.py", content=dirty)],
    )
    timeout = execute_coding_pipeline(
        repository_root=tmp_path,
        routing_evaluation=_evaluation(Route.GEMINI),
        coding_context=context,
        coding_provider=FakeProvider(_proposal("a.py", patch)),
        coding_policy=None,
        patch_validation_policy=None,
        verification_plan=_plan(tmp_path),
        verification_coordinator=CodingVerificationCoordinator(TimeoutEngine()),
    )

    assert timeout.outcome == CodingPipelineOutcome.VERIFICATION_FAILED
    assert timeout.verification is not None
    assert timeout.verification.failure_kind == CodingVerificationFailureKind.CHECK_TIMEOUT
    assert timeout.verification.rolled_back
    assert target.read_text(encoding="utf-8") == dirty
    assert unrelated.read_text(encoding="utf-8") == "keep\n"

    empty_plan = execute_coding_pipeline(
        repository_root=tmp_path,
        routing_evaluation=_evaluation(Route.GEMINI_TO_CODEX),
        coding_context=context,
        coding_provider=FakeProvider(_proposal("a.py", patch)),
        coding_policy=None,
        patch_validation_policy=None,
        verification_plan=VerificationPlan(),
    )
    assert empty_plan.outcome == CodingPipelineOutcome.VERIFICATION_FAILED
    assert empty_plan.verification is not None
    assert empty_plan.verification.failure_kind == CodingVerificationFailureKind.EMPTY_PLAN
    assert target.read_text(encoding="utf-8") == dirty
