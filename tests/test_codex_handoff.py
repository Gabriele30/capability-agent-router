"""Bounding and privacy regressions for the future Codex handoff artifact."""

from __future__ import annotations

import inspect
from pathlib import Path

from car.coding.models import (
    CodingAttemptResult,
    CodingFileContext,
    CodingProposal,
    CodingTaskContext,
    FileChangeOperation,
    ProposedFileChange,
)
from car.coding.verification import CodingVerificationFailureKind, CodingVerificationResult
from car.escalation.handoff import build_codex_handoff, render_codex_handoff_markdown
from car.escalation.models import HandoffPolicy
from car.execution.models import CommandResult, CommandSpec
from car.patching.apply import PatchApplyTransaction
from car.patching.models import (
    PatchApplyResult,
    PatchValidationResult,
    PatchViolation,
    PatchViolationKind,
)
from car.providers.models import RepositoryClassificationContext
from car.repository.models import GitState, LanguageStats, ProjectSignals, RepositoryState
from car.rollback.snapshot import TargetFileSnapshot, TargetSnapshot
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


def _proposal(
    *,
    patch: str = "@@ -1 +1 @@\n-old\n+new",
    reasons: list[str] | None = None,
) -> CodingProposal:
    return CodingProposal(
        summary="Fix parser",
        changes=[
            ProposedFileChange(
                path="car/parser.py",
                operation=FileChangeOperation.MODIFY,
                patch=patch,
            )
        ],
        reasons=reasons or ["localized"],
    )


def _build_handoff(
    root: Path,
    *,
    files: list[CodingFileContext] | None = None,
    proposal: CodingProposal | None = None,
    validation: PatchValidationResult | None = None,
    verification: CodingVerificationResult | None = None,
    policy: HandoffPolicy | None = None,
):
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
        root=root,
        name="private-repository",
        git=GitState(available=True, is_repository=True, branch="main"),
        languages=LanguageStats(counts={"Python": 4}),
        project_signals=ProjectSignals(systems=["Python"]),
    )
    context = CodingTaskContext(
        task="Fix parser regression",
        route=Route.GEMINI_TO_CODEX,
        repository=RepositoryClassificationContext(
            name="private-repository",
            branch="main",
            dirty=False,
            languages={"Python": 4},
            systems=["Python"],
        ),
        files=files or [CodingFileContext(path="car/parser.py", content="old")],
    )
    return build_codex_handoff(
        "Fix parser regression",
        evaluation,
        repository,
        context,
        CodingAttemptResult(
            provider="gemini",
            attempted=True,
            succeeded=True,
            proposal=proposal or _proposal(),
        ),
        validation or PatchValidationResult(valid=True),
        PatchApplyResult(attempted=True, succeeded=True, message="applied"),
        verification,
        policy=policy,
    )


def _failed_verification(*, stdout: str = "", stderr: str = "") -> CodingVerificationResult:
    command = CommandSpec(args=["ruff", "check"], cwd=".", timeout_seconds=10)
    return CodingVerificationResult(
        attempted=True,
        passed=False,
        checks=[CommandResult(command=command, exit_code=1, stdout=stdout, stderr=stderr)],
        rolled_back=True,
        failure_kind=CodingVerificationFailureKind.CHECK_FAILED,
        message="verification failed",
    )


def test_handoff_bounds_patch_evidence_and_selected_files(tmp_path: Path):
    patch = "a" * 150 + "PATCH_END_SHOULD_NOT_APPEAR"
    value = _build_handoff(
        tmp_path,
        files=[
            CodingFileContext(path="a.py", content="a"),
            CodingFileContext(path="b.py", content="b"),
            CodingFileContext(path="c.py", content="c"),
            CodingFileContext(path="d.py", content="d"),
        ],
        proposal=_proposal(patch=patch),
        policy=HandoffPolicy(max_patch_chars=100, max_selected_files=2),
    )
    markdown = render_codex_handoff_markdown(value)
    assert value.selected_files == ["a.py", "b.py"]
    assert value.patch_attempt.diffs[0].endswith("[truncated by CAR]")
    assert patch not in markdown
    assert "PATCH_END_SHOULD_NOT_APPEAR" not in markdown


def test_handoff_bounds_stdout_and_stderr_independently(tmp_path: Path):
    stdout = "o" * 150 + "STDOUT_END_SHOULD_NOT_APPEAR"
    stderr = "e" * 150 + "STDERR_END_SHOULD_NOT_APPEAR"
    value = _build_handoff(
        tmp_path,
        verification=_failed_verification(stdout=stdout, stderr=stderr),
        policy=HandoffPolicy(max_check_output_chars=100),
    )
    check = value.verification.executed_checks[0]
    markdown = render_codex_handoff_markdown(value)
    assert str(check["stdout"]).endswith("[truncated by CAR]")
    assert str(check["stderr"]).endswith("[truncated by CAR]")
    assert "STDOUT_END_SHOULD_NOT_APPEAR" not in markdown
    assert "STDERR_END_SHOULD_NOT_APPEAR" not in markdown


def test_handoff_bounds_reasons_and_validation_violations(tmp_path: Path):
    violations = [
        PatchViolation(
            kind=PatchViolationKind.INVALID_DIFF,
            path="car/parser.py",
            summary=f"violation-{number}",
        )
        for number in range(5)
    ]
    value = _build_handoff(
        tmp_path,
        proposal=_proposal(reasons=[f"reason-{number}" for number in range(5)]),
        validation=PatchValidationResult(valid=False, violations=violations),
        policy=HandoffPolicy(max_reasons=2),
    )
    assert value.coding_attempt.reasons == ["reason-0", "reason-1"]
    assert value.patch_attempt.validation_violations == [
        "invalid_diff: violation-0",
        "invalid_diff: violation-1",
    ]


def test_handoff_excludes_absolute_root_and_retains_relative_paths(tmp_path: Path):
    root = tmp_path / "private-car-root"
    root.mkdir()
    markdown = render_codex_handoff_markdown(
        _build_handoff(
            root,
            files=[CodingFileContext(path="car/parser.py", content="ordinary source")],
        )
    )
    assert str(root) not in markdown
    assert "car/parser.py" in markdown


def test_handoff_excludes_source_contents_and_environment_secrets(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-secret")
    monkeypatch.setenv("CAR_PRIVATE_ENV_SENTINEL", "DO_NOT_SERIALIZE_ME")
    markdown = render_codex_handoff_markdown(
        _build_handoff(
            tmp_path,
            files=[
                CodingFileContext(path="car/private.py", content="VERY_PRIVATE_SOURCE_SENTINEL")
            ],
        )
    )
    assert "car/private.py" in markdown
    for private_value in (
        "VERY_PRIVATE_SOURCE_SENTINEL",
        "super-secret-test-key",
        "fake-openai-secret",
        "DO_NOT_SERIALIZE_ME",
    ):
        assert private_value not in markdown


def test_handoff_excludes_runtime_snapshot_contents(tmp_path: Path):
    snapshot = TargetSnapshot(
        root=tmp_path,
        files={
            Path("car/private.py"): TargetFileSnapshot(
                path=Path("car/private.py"),
                existed=True,
                content=b"SNAPSHOT_PRIVATE_SENTINEL",
            )
        },
    )
    transaction = PatchApplyTransaction(
        tmp_path,
        snapshot,
        lambda path, content: None,
        PatchApplyResult(attempted=True, succeeded=True, message="applied"),
    )
    markdown = render_codex_handoff_markdown(_build_handoff(tmp_path))
    assert transaction._snapshot is snapshot
    assert "transaction" not in inspect.signature(build_codex_handoff).parameters
    assert "SNAPSHOT_PRIVATE_SENTINEL" not in markdown


def test_handoff_keeps_bounded_useful_evidence_without_private_data(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-test-key")
    markdown = render_codex_handoff_markdown(
        _build_handoff(
            tmp_path,
            files=[CodingFileContext(path="car/parser.py", content="VERY_PRIVATE_SOURCE_SENTINEL")],
            proposal=_proposal(patch="x" * 150 + "PATCH_END_SHOULD_NOT_APPEAR"),
            verification=_failed_verification(stdout="o" * 150 + "STDOUT_END_SHOULD_NOT_APPEAR"),
            policy=HandoffPolicy(max_patch_chars=100, max_check_output_chars=100),
        )
    )
    for expected in (
        "Fix parser regression",
        "Final route: gemini_to_codex",
        "Fix parser",
        "car/parser.py",
        "[truncated by CAR]",
        CodingVerificationFailureKind.CHECK_FAILED.value,
        "Succeeded: True",
    ):
        assert expected in markdown
    for private_value in (
        str(tmp_path),
        "VERY_PRIVATE_SOURCE_SENTINEL",
        "super-secret-test-key",
        "PATCH_END_SHOULD_NOT_APPEAR",
        "STDOUT_END_SHOULD_NOT_APPEAR",
    ):
        assert private_value not in markdown
