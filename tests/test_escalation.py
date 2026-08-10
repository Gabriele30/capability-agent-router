from pathlib import Path

import pytest

from car.coding.models import (
    CodingAttemptResult,
    CodingFileContext,
    CodingProposal,
    CodingTaskContext,
    FileChangeOperation,
    ProposedFileChange,
)
from car.coding.verification import CodingVerificationFailureKind, CodingVerificationResult
from car.escalation.handoff import (
    build_codex_handoff,
    decide_escalation,
    render_codex_handoff_markdown,
    write_codex_handoff,
)
from car.escalation.models import (
    CodexHandoff,
    CodingAttemptSummary,
    EscalationReason,
    PatchAttemptSummary,
    RepositoryHandoffSummary,
    RoutingHandoffSummary,
    VerificationHandoffSummary,
)
from car.patching.models import (
    PatchApplyFailureKind,
    PatchApplyResult,
    PatchValidationResult,
    PatchViolation,
    PatchViolationKind,
)
from car.providers.models import ProviderErrorKind, RepositoryClassificationContext
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


def handoff(route=Route.GEMINI_TO_CODEX, rollback_failure=None):
    return CodexHandoff(
        task="Fix parser regression",
        routing=RoutingHandoffSummary(
            deterministic_route=route,
            final_route=route,
            decision_sources=["deterministic"],
            fusion_reasons=["no_provider_evidence"],
            provider_influenced_decision=False,
            deterministic_risk=0.3,
            final_risk=0.3,
        ),
        repository=RepositoryHandoffSummary(
            name="repo", branch="main", dirty=True, languages={"Python": 2}, systems=["Python"]
        ),
        selected_files=["car/parser.py"],
        coding_attempt=CodingAttemptSummary(
            provider="gemini",
            attempted=True,
            succeeded=True,
            proposal_summary="Fix parser",
            reasons=["localized"],
            uncertainties=[],
        ),
        patch_attempt=PatchAttemptSummary(
            paths=["car/parser.py"],
            operations=["modify"],
            diffs=["-old\n+new\n[truncated by CAR]"],
            validation_valid=True,
            apply_succeeded=True,
        ),
        verification=VerificationHandoffSummary(
            planned_checks=[["ruff", "check"]],
            executed_checks=[
                {
                    "command": ["ruff", "check"],
                    "exit_code": 1,
                    "timeout": False,
                    "stdout": "bad",
                    "stderr": "failed",
                }
            ],
            failure_kind="check_failed",
            rollback_attempted=True,
            rollback_succeeded=not bool(rollback_failure),
            rollback_failure=rollback_failure,
        ),
        escalation_reason=EscalationReason.WORKSPACE_STATE_UNCERTAIN
        if rollback_failure
        else EscalationReason.VERIFICATION_FAILED,
        recommended_next_step=(
            "Inspect the failed verification evidence and produce a corrected patch. "
            "The previous coding attempt was rolled back."
        ),
    )


def handoff_builder_inputs(tmp_path: Path):
    decision = RoutingDecision(
        route=Route.GEMINI_TO_CODEX,
        risk=RiskAssessment(score=0.4, level=RiskLevel.MEDIUM),
        complexity=Complexity.MEDIUM,
        scope=ScopeEstimate(size=ScopeSize.MEDIUM),
        confidence=0.7,
        reasons=["parser regression"],
        matched_rules=["medium-uncertainty"],
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
        root=tmp_path,
        name="repo",
        git=GitState(available=True, is_repository=True, branch="main"),
        languages=LanguageStats(counts={"Python": 1}),
        project_signals=ProjectSignals(systems=["Python"]),
    )
    context = CodingTaskContext(
        task="Fix parser regression",
        route=Route.GEMINI_TO_CODEX,
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
        reasons=["localized"],
    )
    return evaluation, repository, context, proposal


def build_failure_handoff(
    tmp_path: Path,
    coding_attempt: CodingAttemptResult,
    patch_validation: PatchValidationResult | None = None,
    patch_apply: PatchApplyResult | None = None,
    verification: CodingVerificationResult | None = None,
):
    evaluation, repository, context, _ = handoff_builder_inputs(tmp_path)
    return build_codex_handoff(
        "Fix parser regression",
        evaluation,
        repository,
        context,
        coding_attempt,
        patch_validation,
        patch_apply,
        verification,
    )


def test_handoff_renderer_is_bounded_private_and_no_double_work(tmp_path: Path):
    value = handoff()
    markdown = render_codex_handoff_markdown(value)
    assert markdown == render_codex_handoff_markdown(value)
    for expected in (
        "Fix parser regression",
        "Fix parser",
        "car/parser.py",
        "-old",
        "failed",
        "Rollback",
    ):
        assert expected in markdown
    assert str(tmp_path) not in markdown
    assert (
        "super-secret-test-key" not in markdown and "VERY_PRIVATE_SOURCE_SENTINEL" not in markdown
    )


def test_escalation_semantics():
    assert decide_escalation(handoff(), verification_passed=True).should_escalate is False
    assert decide_escalation(handoff(Route.GEMINI)).should_escalate is False
    decision = decide_escalation(handoff())
    assert decision.should_escalate and decision.target == Route.CODEX
    assert (
        decide_escalation(handoff(rollback_failure="rollback_failed")).reason
        == EscalationReason.WORKSPACE_STATE_UNCERTAIN
    )


def test_explicit_writer_overwrites_only_fixed_context_path(tmp_path: Path):
    first = write_codex_handoff(tmp_path, handoff())
    second = handoff()
    second.task = "Second task"
    assert write_codex_handoff(tmp_path, second) == first
    assert first == tmp_path / ".car-context" / "current-task.md"
    assert "Second task" in first.read_text(encoding="utf-8")


def test_writer_rejects_context_symlink(tmp_path: Path):
    outside = tmp_path.parent / "escalation-outside"
    outside.mkdir(exist_ok=True)
    try:
        (tmp_path / ".car-context").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(str(error))
    with pytest.raises(ValueError):
        write_codex_handoff(tmp_path, handoff())


def test_builder_represents_coding_provider_timeout(tmp_path: Path):
    value = build_failure_handoff(
        tmp_path,
        CodingAttemptResult(
            provider="gemini",
            attempted=True,
            succeeded=False,
            error_kind=ProviderErrorKind.TIMEOUT,
        ),
    )
    assert value.coding_attempt.error_kind == ProviderErrorKind.TIMEOUT.value
    assert value.coding_attempt.proposal_summary is None
    assert value.patch_attempt.validation_valid is None
    assert value.patch_attempt.apply_succeeded is None
    assert value.verification.executed_checks == []
    assert value.verification.failure_kind is None


def test_builder_represents_patch_validation_failure(tmp_path: Path):
    _, _, _, proposal = handoff_builder_inputs(tmp_path)
    value = build_failure_handoff(
        tmp_path,
        CodingAttemptResult(provider="gemini", attempted=True, succeeded=True, proposal=proposal),
        PatchValidationResult.rejected(
            PatchViolation(
                kind=PatchViolationKind.INVALID_DIFF,
                path="car/parser.py",
                summary="invalid unified diff",
            )
        ),
    )
    assert value.coding_attempt.proposal_summary == "Fix parser"
    assert value.patch_attempt.validation_valid is False
    assert value.patch_attempt.validation_violations == ["invalid_diff: invalid unified diff"]
    assert value.patch_attempt.apply_succeeded is None
    assert value.verification.executed_checks == []


def test_builder_represents_patch_apply_failure(tmp_path: Path):
    _, _, _, proposal = handoff_builder_inputs(tmp_path)
    value = build_failure_handoff(
        tmp_path,
        CodingAttemptResult(provider="gemini", attempted=True, succeeded=True, proposal=proposal),
        PatchValidationResult(valid=True),
        PatchApplyResult(
            attempted=True,
            succeeded=False,
            rolled_back=True,
            failure_kind=PatchApplyFailureKind.WRITE_FAILED,
            message="safe write failure",
        ),
    )
    assert value.patch_attempt.apply_succeeded is False
    assert value.patch_attempt.apply_failure == PatchApplyFailureKind.WRITE_FAILED.value
    assert value.verification.rollback_succeeded is True
    assert value.verification.executed_checks == []


def test_builder_preserves_verification_timeout_for_escalation(tmp_path: Path):
    _, _, _, proposal = handoff_builder_inputs(tmp_path)
    value = build_failure_handoff(
        tmp_path,
        CodingAttemptResult(provider="gemini", attempted=True, succeeded=True, proposal=proposal),
        PatchValidationResult(valid=True),
        PatchApplyResult(attempted=True, succeeded=True, message="applied"),
        CodingVerificationResult(
            attempted=True,
            passed=False,
            rolled_back=True,
            failure_kind=CodingVerificationFailureKind.CHECK_TIMEOUT,
            message="verification timed out",
        ),
    )
    assert value.verification.failure_kind == CodingVerificationFailureKind.CHECK_TIMEOUT.value
    escalation = decide_escalation(value)
    assert escalation.should_escalate is True
    assert escalation.target == Route.CODEX
