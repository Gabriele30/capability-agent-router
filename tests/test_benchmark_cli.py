import json
import subprocess
import sys
from importlib import import_module
from pathlib import Path

from typer.testing import CliRunner

from car.benchmark.models import BenchmarkStrategy
from car.cli.app import app
from car.telemetry import AttemptCapability, ExecutionTelemetryCollector, FinalOutcome, TokenUsage
from car.telemetry.models import UsageSource

runner = CliRunner()


class _OfflineExecutor:
    def __init__(self) -> None:
        self.calls: list[BenchmarkStrategy] = []

    def execute(self, case, workspace, strategy):
        self.calls.append(strategy)
        collector = ExecutionTelemetryCollector()
        collector.start_execution(initial_route=_route_for(strategy), task_category=case.category)
        capability = (
            AttemptCapability.GEMINI
            if strategy == BenchmarkStrategy.GEMINI_ONLY
            else AttemptCapability.CODEX_CONTROLLED_WRITE
        )
        sequence = collector.start_attempt(
            capability,
            provider="gemini" if capability == AttemptCapability.GEMINI else "codex",
            model="gemini-3.6-flash" if capability == AttemptCapability.GEMINI else None,
        )
        usage = (
            TokenUsage(
                input_tokens=100,
                output_tokens=20,
                total_tokens=120,
                source=UsageSource.PROVIDER_REPORTED,
            )
            if capability == AttemptCapability.GEMINI
            else TokenUsage(source=UsageSource.UNAVAILABLE)
        )
        collector.finish_attempt(sequence, succeeded=True, usage=usage)
        return collector.finish_execution(
            final_route=_route_for(strategy),
            final_outcome=FinalOutcome.VERIFIED_SUCCESS,
            verified_success=True,
        )


def _route_for(strategy):
    from car.router.models import Route

    return Route.GEMINI if strategy == BenchmarkStrategy.GEMINI_ONLY else Route.GEMINI_TO_CODEX


def _fixture_and_manifest(tmp_path: Path) -> Path:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "target.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=fixture, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=fixture, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "user.name=test",
            "commit",
            "-m",
            "base",
        ],
        cwd=fixture,
        check=True,
        capture_output=True,
    )
    manifest = tmp_path / "benchmark.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "cases": [
                    {
                        "id": "synthetic-one",
                        "category": "testing",
                        "task": "Synthetic fixture task",
                        "fixture": "fixture",
                        "authorized_paths": ["target.py"],
                        "verification": ["ruff"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest


def test_benchmark_cli_all_and_json_export_are_offline(
    git_repository: Path, tmp_path: Path, monkeypatch
):
    manifest = _fixture_and_manifest(tmp_path)
    source_before = (tmp_path / "fixture" / "target.py").read_bytes()
    executor = _OfflineExecutor()
    cli = import_module("car.cli.app")
    monkeypatch.setattr(cli, "_build_benchmark_executor", lambda config: executor)
    monkeypatch.chdir(git_repository)
    output = tmp_path / "result.json"

    result = runner.invoke(app, ["benchmark", str(manifest), "--all", "--json-out", str(output)])

    assert result.exit_code == 0
    assert executor.calls == list(BenchmarkStrategy)
    assert "gemini" in result.stdout
    assert "codex" in result.stdout
    assert "N/A" in result.stdout
    assert (tmp_path / "fixture" / "target.py").read_bytes() == source_before
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["metadata"]["manifest_hash"]
    assert payload["metadata"]["strategies"] == [item.value for item in BenchmarkStrategy]
    assert len(payload["task_results"]) == 3
    assert len(payload["summaries"]) == 3
    assert payload["summaries"][1]["cost_complete"] is False
    serialized = output.read_text(encoding="utf-8")
    assert str(tmp_path.resolve()) not in serialized
    assert "Synthetic fixture task" not in serialized
    assert "target.py\nvalue" not in serialized


def test_benchmark_cli_one_strategy_and_invalid_inputs(
    git_repository: Path, tmp_path: Path, monkeypatch
):
    manifest = _fixture_and_manifest(tmp_path)
    executor = _OfflineExecutor()
    cli = import_module("car.cli.app")
    monkeypatch.setattr(cli, "_build_benchmark_executor", lambda config: executor)
    monkeypatch.chdir(git_repository)

    one = runner.invoke(app, ["benchmark", str(manifest), "--strategy", "gemini-only"])
    invalid = runner.invoke(app, ["benchmark", str(manifest), "--strategy", "bad"])
    conflicting = runner.invoke(app, ["benchmark", str(manifest), "--strategy", "car", "--all"])
    missing = runner.invoke(app, ["benchmark", str(tmp_path / "missing.json")])
    no_selection = runner.invoke(app, ["benchmark", str(manifest)])
    help_result = runner.invoke(app, ["benchmark", "--help"])

    assert one.exit_code == 0
    assert executor.calls == [BenchmarkStrategy.GEMINI_ONLY]
    assert invalid.exit_code == 2
    assert conflicting.exit_code == 2
    assert missing.exit_code == 2
    assert "invalid benchmark manifest" in missing.stdout
    assert no_selection.exit_code == 2
    assert help_result.exit_code == 0
    assert "--strategy" in help_result.stdout


def test_benchmark_help_imports_in_a_fresh_python_process():
    result = subprocess.run(
        [sys.executable, "-m", "car", "benchmark", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Run selected live benchmark strategies" in result.stdout
