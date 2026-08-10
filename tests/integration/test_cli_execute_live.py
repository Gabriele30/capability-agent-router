"""Opt-in real Gemini success validation through the public ``car execute`` command."""

import os
import subprocess
import sys
from importlib import import_module
from pathlib import Path

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

    assert result.exit_code == 0, result.stdout[-2_000:]
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
