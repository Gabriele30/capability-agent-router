"""Offline CLI presentation tests for the opt-in SWE-bench bridge."""

import json
from importlib import import_module

from typer.testing import CliRunner

from car.benchmark.models import BenchmarkStrategy
from car.benchmark.results import BenchmarkFailureKind, BenchmarkTaskResult
from car.benchmark.swebench.evaluator import SWEbenchEvaluationResult, SWEbenchEvaluationStatus
from car.benchmark.swebench.runtime import SWEbenchLiveRun
from car.cli.app import app
from car.providers.models import ProviderErrorKind
from car.telemetry.models import FinalOutcome

runner = CliRunner()


def _run(*, diagnostics: bool, evaluator_status: SWEbenchEvaluationStatus) -> SWEbenchLiveRun:
    result = BenchmarkTaskResult(
        case_id="sympy__sympy-20590",
        strategy=BenchmarkStrategy.GEMINI_ONLY,
        verified_success=False,
        duration_ms=1,
        attempt_count=1,
        final_outcome=FinalOutcome.RESTORED,
        failure_kind=BenchmarkFailureKind.TASK_FAILED,
        failure_reason="strategy did not achieve verified success",
        pipeline_outcome="coding_provider_failed" if diagnostics else None,
        provider_error_kind=ProviderErrorKind.INVALID_REQUEST if diagnostics else None,
        provider_http_status=400 if diagnostics else None,
        provider_error_status="INVALID_ARGUMENT" if diagnostics else None,
        provider_error_message="response_format schema is invalid" if diagnostics else None,
    )
    return SWEbenchLiveRun(
        result=result,
        evaluator=SWEbenchEvaluationResult(
            status=evaluator_status,
            diagnostic=(
                "no accepted candidate delta"
                if evaluator_status == SWEbenchEvaluationStatus.EMPTY_PATCH
                else "official evaluator resolved the candidate"
            ),
        ),
    )


def _invoke(monkeypatch, git_repository, run: SWEbenchLiveRun, *args: str):
    module = import_module("car.cli.app")
    monkeypatch.setattr(module, "run_swebench_instance", lambda *_, **__: run)
    monkeypatch.chdir(git_repository)
    return runner.invoke(
        app,
        [
            "swebench-run",
            "--instance",
            "sympy__sympy-20590",
            "--strategy",
            "gemini-only",
            "--live",
            *args,
        ],
    )


def test_swebench_cli_prints_safe_provider_failure_and_evaluator_diagnostics(
    git_repository, monkeypatch
):
    result = _invoke(
        monkeypatch,
        git_repository,
        _run(diagnostics=True, evaluator_status=SWEbenchEvaluationStatus.EMPTY_PATCH),
    )

    assert result.exit_code == 0
    for expected in (
        "Pipeline outcome: coding_provider_failed",
        "Provider error kind: invalid_request",
        "Provider HTTP status: 400",
        "Provider error status: INVALID_ARGUMENT",
        "Provider error message: response_format schema is invalid",
        "Evaluator: empty_patch",
        "Evaluator diagnostic: no accepted candidate delta",
    ):
        assert expected in result.stdout
    for forbidden in ("GEMINI_API_KEY", "headers", "prompt", "details", "gold", "test_patch"):
        assert forbidden not in result.stdout


def test_swebench_cli_omits_missing_provider_diagnostics_and_prints_success_evaluator(
    git_repository, monkeypatch
):
    result = _invoke(
        monkeypatch,
        git_repository,
        _run(diagnostics=False, evaluator_status=SWEbenchEvaluationStatus.RESOLVED),
    )

    assert result.exit_code == 0
    assert "Evaluator: resolved" in result.stdout
    assert "Pipeline outcome:" not in result.stdout
    assert "Provider error" not in result.stdout


def test_swebench_cli_json_uses_existing_structured_result(git_repository, monkeypatch):
    result = _invoke(
        monkeypatch,
        git_repository,
        _run(diagnostics=True, evaluator_status=SWEbenchEvaluationStatus.EMPTY_PATCH),
        "--json",
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["result"]["provider_error_kind"] == "invalid_request"
    assert payload["result"]["provider_http_status"] == 400
    assert payload["evaluator"]["status"] == "empty_patch"
    assert payload["evaluator"]["diagnostic"] == "no accepted candidate delta"
    assert "prompt" not in result.stdout and "headers" not in result.stdout
