"""Offline tests for child-scoped verification-process hygiene."""

import os
import subprocess
from pathlib import Path

from car.execution.models import CommandSpec
from car.execution.runner import CommandRunner


def _command(root: Path, args: list[str]) -> CommandSpec:
    return CommandSpec(args=args, cwd=str(root), timeout_seconds=30)


def test_car_pytest_verification_leaves_no_cache_or_bytecode_artifacts(tmp_path: Path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text(
        "def test_example():\n    assert True\n", encoding="utf-8"
    )

    result = CommandRunner().run(_command(tmp_path, ["python", "-m", "pytest"]))

    assert result.exit_code == 0
    assert not (tmp_path / ".pytest_cache").exists()
    assert not list(tmp_path.rglob("__pycache__"))
    assert not list(tmp_path.rglob("*.pyc"))


def test_pytest_environment_is_child_scoped_and_preserves_user_options(monkeypatch, tmp_path: Path):
    captured = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args[0], 0, "", "")

    monkeypatch.setenv("PYTEST_ADDOPTS", "--maxfail=1")
    monkeypatch.setenv("CAR_TEST_USER_VALUE", "preserved")
    parent_environment = os.environ.copy()
    monkeypatch.setattr("car.execution.runner.subprocess.run", fake_run)

    CommandRunner().run(_command(tmp_path, ["python", "-m", "pytest"]))

    child = captured["env"]
    assert child is not os.environ
    assert child["PYTHONDONTWRITEBYTECODE"] == "1"
    assert child["CAR_TEST_USER_VALUE"] == "preserved"
    assert child["PYTEST_ADDOPTS"] == "--maxfail=1 -p no:cacheprovider"
    assert os.environ == parent_environment


def test_pytest_cacheprovider_option_is_not_duplicated(monkeypatch, tmp_path: Path):
    captured = {}
    monkeypatch.setenv("PYTEST_ADDOPTS", "-p no:cacheprovider --quiet")
    monkeypatch.setattr(
        "car.execution.runner.subprocess.run",
        lambda *args, **kwargs: (
            captured.update(kwargs) or subprocess.CompletedProcess(args[0], 0, "", "")
        ),
    )

    CommandRunner().run(_command(tmp_path, ["python", "-m", "pytest"]))

    assert captured["env"]["PYTEST_ADDOPTS"] == "-p no:cacheprovider --quiet"


def test_non_pytest_command_receives_no_pytest_specific_environment(monkeypatch, tmp_path: Path):
    captured = {}
    monkeypatch.setattr(
        "car.execution.runner.subprocess.run",
        lambda *args, **kwargs: (
            captured.update(kwargs) or subprocess.CompletedProcess(args[0], 0, "", "")
        ),
    )

    CommandRunner().run(_command(tmp_path, ["ruff", "check", "sample.py"]))

    assert captured["env"] is None


def test_runner_preserves_shell_false_for_pytest_verification(monkeypatch, tmp_path: Path):
    captured = {}
    monkeypatch.setattr(
        "car.execution.runner.subprocess.run",
        lambda *args, **kwargs: (
            captured.update(kwargs) or subprocess.CompletedProcess(args[0], 1, "", "failed")
        ),
    )

    result = CommandRunner().run(_command(tmp_path, ["python", "-m", "pytest"]))

    assert result.exit_code == 1
    assert captured["shell"] is False
