"""Opt-in live validation of Gemini rollback followed by read-only Codex analysis."""

import os
import shutil
import subprocess
from importlib import import_module
from pathlib import Path

import pytest
from typer.testing import CliRunner

if os.getenv("CAR_RUN_LIVE_CODING_ESCALATION_TESTS") != "1":
    pytest.skip(
        "set CAR_RUN_LIVE_CODING_ESCALATION_TESTS=1 for live Gemini-to-Codex validation",
        allow_module_level=True,
    )


from car.cli.app import app  # noqa: E402
from car.codex.models import CodexRuntimeHealthStatus  # noqa: E402
from car.codex.runtime import LocalCodexRuntime  # noqa: E402
from car.coding.gemini import GeminiCodingProvider  # noqa: E402
from car.config.models import CarConfig  # noqa: E402
from car.providers.models import ProviderStatus  # noqa: E402
from car.router.models import Route  # noqa: E402

TASK = (
    "Fix the implementation of add in calculator.py so that it correctly adds "
    "the two arguments. Modify only calculator.py. Make the smallest change and "
    "do not create files."
)
runner = CliRunner()


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        check=False,
        shell=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        pytest.skip("Git is unavailable for live coding escalation validation")
    return completed.stdout


def _source_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def _live_config(source_root: Path) -> CarConfig:
    config_path = source_root / ".car-context" / "config.json"
    if not config_path.is_file():
        pytest.skip("live coding escalation requires existing local CAR Gemini configuration")
    config = CarConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
    if GeminiCodingProvider(config.providers.gemini).health().status != ProviderStatus.CONFIGURED:
        pytest.skip("live coding escalation requires locally configured Gemini")
    return config


def _bounded_output(checks) -> str:
    return "\n".join(f"{check.stdout}\n{check.stderr}" for check in checks)


@pytest.mark.live
def test_cli_execute_live_failure_rolls_back_then_runs_read_only_codex(tmp_path: Path, monkeypatch):
    """Keep the real public Gemini-to-Codex path observable without stubbing it."""
    source_root = Path.cwd()
    _live_config(source_root)
    if shutil.which("git") is None:
        pytest.skip("Git is unavailable for live coding escalation validation")
    codex_health = LocalCodexRuntime().health()
    if codex_health.status != CodexRuntimeHealthStatus.READY:
        pytest.skip(f"local Codex runtime is not ready: {codex_health.status.value}")

    calculator = tmp_path / "calculator.py"
    tests = tmp_path / "tests"
    tests.mkdir()
    calculator.write_text(
        "def add(a: int, b: int) -> int:\n    return a - b\n",
        encoding="utf-8",
    )
    test_file = tests / "test_calculator.py"
    test_file.write_text(
        "from calculator import add\n\n\ndef test_add() -> None:\n"
        "    assert add(2, 3) == 5\n\n\ndef test_car_live_failure_sentinel() -> None:\n"
        "    assert False, 'CAR live rollback sentinel'\n",
        encoding="utf-8",
    )
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("do not change\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    before_files = _source_snapshot(tmp_path)
    before_status = _git(tmp_path, "status", "--porcelain")

    cli = import_module("car.cli.app")
    source_context = source_root / ".car-context"
    monkeypatch.setattr(
        cli,
        "_context_paths",
        lambda root: (
            source_context,
            source_context / "config.json",
            source_context / "state.json",
        ),
    )
    captured = []
    real_execute = cli.CodingFlowGateway.execute

    def spy_execute(gateway, request, authorization=None):
        result = real_execute(gateway, request, authorization)
        captured.append(result)
        return result

    monkeypatch.setattr(cli.CodingFlowGateway, "execute", spy_execute)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        [
            "execute",
            TASK,
            "--file",
            "calculator.py",
            "--verify",
            "pytest",
            "--yes",
            "--codex-analysis",
        ],
    )

    assert result.exit_code == 1, result.stdout[-2_000:]
    assert "Task remains unresolved" in result.stdout
    assert len(captured) == 1
    gateway = captured[0]
    flow = gateway.flow_result
    assert gateway.authorized and gateway.attempted and not gateway.succeeded
    assert flow is not None and not flow.succeeded
    assert flow.outcome.value == "codex_analysis_succeeded"
    pipeline = flow.coding.pipeline_result
    assert pipeline is not None and pipeline.route == Route.GEMINI_TO_CODEX
    assert pipeline.coding_attempt is not None and pipeline.coding_attempt.proposal is not None
    proposal = pipeline.coding_attempt.proposal
    assert [(change.operation.value, change.path) for change in proposal.changes] == [
        ("modify", "calculator.py")
    ]
    assert pipeline.patch_validation is not None and pipeline.patch_validation.valid
    assert pipeline.patch_apply is not None and pipeline.patch_apply.succeeded
    assert pipeline.patch_apply.created_files == []
    verification = pipeline.verification
    assert verification is not None and not verification.passed and verification.rolled_back
    assert verification.failure_kind.value == "check_failed"
    assert len(verification.checks) == 1
    check = verification.checks[0]
    assert check.command.args == ["python", "-m", "pytest"]
    assert check.exit_code not in {None, 0}
    assert "CAR live rollback sentinel" in _bounded_output(verification.checks)

    post_failure = flow.post_failure
    assert post_failure is not None and post_failure.escalation.should_escalate
    assert post_failure.attempted_codex and post_failure.succeeded
    handoff = post_failure.handoff
    assert handoff is not None
    assert handoff.task == TASK and handoff.selected_files == ["calculator.py"]
    assert handoff.coding_attempt.proposal_summary == proposal.summary
    assert handoff.patch_attempt.paths == ["calculator.py"]
    assert handoff.patch_attempt.operations == ["modify"]
    assert handoff.patch_attempt.validation_valid and handoff.patch_attempt.apply_succeeded
    assert handoff.verification.rollback_succeeded is True
    assert "CAR live rollback sentinel" in str(handoff.verification.executed_checks)
    codex_execution = post_failure.codex_execution
    assert codex_execution is not None and codex_execution.succeeded
    assert codex_execution.application_result is not None
    runtime_result = codex_execution.application_result.execution
    assert runtime_result is not None and runtime_result.succeeded
    assert runtime_result.final_message and runtime_result.final_message.strip()

    assert calculator.read_bytes() == before_files["calculator.py"]
    assert test_file.read_bytes() == before_files["tests/test_calculator.py"]
    assert unrelated.read_bytes() == before_files["unrelated.txt"]
    assert _source_snapshot(tmp_path) == before_files
    assert _git(tmp_path, "status", "--porcelain") == before_status
    assert not (tmp_path / ".car-context").exists()
