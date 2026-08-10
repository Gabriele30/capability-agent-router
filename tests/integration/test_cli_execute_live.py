"""Opt-in real Gemini success validation through the public ``car execute`` command."""

import json
import os
import subprocess
import sys
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

if os.getenv("CAR_RUN_LIVE_CODING_FLOW_TESTS") != "1":
    pytest.skip(
        "live coding flow requires CAR_RUN_LIVE_CODING_FLOW_TESTS=1",
        allow_module_level=True,
    )


from car.cli.app import app  # noqa: E402
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


def _bounded(value: str, limit: int = 500) -> str:
    """Keep failure diagnostics useful without dumping provider or repository data."""
    return value if len(value) <= limit else f"{value[:limit]}… [truncated]"


def _live_failure_diagnostic(captured: list[Any]) -> str:
    """Render only structured gateway evidence retained by the real CLI flow."""
    if not captured:
        return "No CodingFlowGatewayResult was captured."
    gateway = captured[-1]
    flow = gateway.flow_result
    application = flow.coding if flow else None
    pipeline = application.pipeline_result if application else None
    attempt = pipeline.coding_attempt if pipeline else None
    proposal = attempt.proposal if attempt else None
    validation = pipeline.patch_validation if pipeline else None
    apply = pipeline.patch_apply if pipeline else None
    verification = pipeline.verification if pipeline else None
    post_failure = flow.post_failure if flow else None

    diagnostic = {
        "gateway": {
            "authorized": gateway.authorized,
            "attempted": gateway.attempted,
            "succeeded": gateway.succeeded,
            "failure_kind": _enum_value(gateway.failure_kind),
        },
        "coding_flow": {
            "outcome": _enum_value(flow.outcome) if flow else None,
            "attempted": flow.attempted if flow else None,
            "succeeded": flow.succeeded if flow else None,
        },
        "pipeline_application": {
            "attempted": application.attempted if application else None,
            "succeeded": application.succeeded if application else None,
            "failure_kind": _enum_value(application.failure_kind) if application else None,
        },
        "pipeline": {"outcome": _enum_value(pipeline.outcome) if pipeline else None},
        "coding_attempt": {
            "attempted": attempt.attempted if attempt else None,
            "succeeded": attempt.succeeded if attempt else None,
            "provider": attempt.provider if attempt else None,
            "error_kind": _enum_value(attempt.error_kind) if attempt else None,
            "provider_message": "not retained by CodingAttemptResult",
            "proposal_present": proposal is not None,
        },
        "proposal": {
            "summary": _bounded(proposal.summary) if proposal else None,
            "change_count": len(proposal.changes) if proposal else 0,
            "changes": [
                {
                    "operation": change.operation.value,
                    "path": change.path,
                    "diff_present": bool(change.patch),
                    "diff_length": len(change.patch),
                }
                for change in proposal.changes
            ]
            if proposal
            else [],
        },
        "patch_validation": {
            "executed": validation is not None,
            "valid": validation.valid if validation else None,
            "violations": [
                {"kind": violation.kind.value, "path": violation.path, "message": violation.summary}
                for violation in validation.violations
            ]
            if validation
            else [],
        },
        "patch_apply": {
            "executed": apply is not None,
            "succeeded": apply.succeeded if apply else None,
            "state": "not retained by PatchApplyResult" if apply else None,
            "failure_kind": _enum_value(apply.failure_kind) if apply else None,
            "rollback_failure_kind": _enum_value(apply.rollback_failure_kind) if apply else None,
        },
        "verification": {
            "executed": verification is not None,
            "passed": verification.passed if verification else None,
            "rolled_back": verification.rolled_back if verification else None,
            "failure_kind": _enum_value(verification.failure_kind) if verification else None,
            "rollback_failure": _enum_value(verification.rollback_failure)
            if verification
            else None,
            "checks": [
                {
                    "argv": check.command.args,
                    "exit_code": check.exit_code,
                    "timed_out": check.timed_out,
                    "executable_not_found": check.executable_not_found,
                    "stdout": _bounded(check.stdout),
                    "stderr": _bounded(check.stderr),
                }
                for check in verification.checks
            ]
            if verification
            else [],
        },
        "post_failure": {
            "outcome": _enum_value(post_failure.outcome) if post_failure else None,
            "should_escalate": post_failure.escalation.should_escalate if post_failure else None,
            "codex_execution_attempted": post_failure.attempted_codex if post_failure else None,
        },
    }
    return json.dumps(diagnostic, indent=2, sort_keys=True)


def _enum_value(value: Any) -> str | None:
    return value.value if value is not None else None


def _run_pytest(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest"],
        cwd=root,
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _live_config(source_root: Path) -> CarConfig:
    config_path = source_root / ".car-context" / "config.json"
    if not config_path.is_file():
        pytest.skip("live coding flow requires existing local CAR Gemini configuration")
    config = CarConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
    health = GeminiCodingProvider(config.providers.gemini).health()
    if health.status != ProviderStatus.CONFIGURED:
        pytest.skip(f"live coding flow Gemini is locally unavailable: {health.status.value}")
    return config


def test_cli_execute_real_gemini_coding_success(tmp_path: Path, monkeypatch):
    """Exercise CLI preview, consent, real provider, apply, and real pytest verification."""
    source_root = Path.cwd()
    config = _live_config(source_root)
    calculator = tmp_path / "calculator.py"
    tests = tmp_path / "tests"
    tests.mkdir()
    calculator.write_text(
        "def add(a: int, b: int) -> int:\n    return a - b\n",
        encoding="utf-8",
    )
    test_file = tests / "test_calculator.py"
    test_file.write_text(
        "from calculator import add\n\n\ndef test_add() -> None:\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    before_calculator = calculator.read_bytes()
    before_test = test_file.read_bytes()
    assert _run_pytest(tmp_path).returncode != 0

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
    monkeypatch.chdir(tmp_path)
    captured_gateway_results = []
    real_execute = cli.CodingFlowGateway.execute

    def spy_execute(gateway, request, authorization=None):
        gateway_result = real_execute(gateway, request, authorization)
        captured_gateway_results.append(gateway_result)
        return gateway_result

    monkeypatch.setattr(cli.CodingFlowGateway, "execute", spy_execute)
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
        ],
    )

    assert result.exit_code == 0, _live_failure_diagnostic(captured_gateway_results)
    assert "Coding execution preview" in result.stdout
    assert "calculator.py" in result.stdout
    assert "pytest" in result.stdout
    assert "coding task verified" in result.stdout.lower()
    assert calculator.read_bytes() != before_calculator
    assert test_file.read_bytes() == before_test
    assert _run_pytest(tmp_path).returncode == 0
    assert not (tmp_path / ".car-context").exists()
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*.py")} == {
        Path("calculator.py"),
        Path("tests/test_calculator.py"),
    }

    request = cli.TaskRequest(description=TASK)
    repository = cli.scan_repository()
    route = cli.evaluate_routing(
        request, repository, config.default_mode, provider=None
    ).final_decision.route
    assert route in {Route.GEMINI, Route.GEMINI_TO_CODEX}
