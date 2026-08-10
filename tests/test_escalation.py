from pathlib import Path

import pytest

from car.escalation.handoff import (
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
from car.router.models import Route


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
