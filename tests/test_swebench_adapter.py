"""Offline tests for the isolated SWE-bench Verified adapter."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from car.benchmark.models import BenchmarkStrategy
from car.benchmark.results import BenchmarkFailureKind
from car.benchmark.swebench.evaluator import (
    SWEbenchEvaluationRequest,
    SWEbenchEvaluationResult,
    SWEbenchEvaluationStatus,
    check_preflight,
    map_evaluation_result,
)
from car.benchmark.swebench.manifest import load_sample_spec
from car.benchmark.swebench.models import (
    SAMPLE_ALGORITHM_VERSION,
    SAMPLE_PREFIX,
    SWEbenchInstance,
    SWEbenchSampleSpec,
    sample_sha256,
)
from car.benchmark.swebench.projection import (
    EVALUATOR_ONLY_FIELDS,
    explicit_repository_scope,
    project_provider_visible,
    validate_base_checkout,
)
from car.benchmark.swebench.selection import select_verified_sample
from car.repository.git import run_git


def _instances() -> list[SWEbenchInstance]:
    records = []
    for difficulty_index, difficulty in enumerate(("<15 min fix", "15 min - 1 hour", "1-4 hours")):
        for repository in range(8):
            for number in range(8):
                records.append(
                    SWEbenchInstance(
                        instance_id=f"repo{repository}__task-{difficulty_index}-{number}",
                        repo=f"org/repo{repository}",
                        base_commit=f"{number:040x}",
                        problem_statement=f"Fix {difficulty} task {number}",
                        difficulty=difficulty,
                    )
                )
    return records


def test_selection_is_deterministic_stratified_and_repository_bounded() -> None:
    selected = select_verified_sample(_instances())
    reversed_selected = select_verified_sample(reversed(_instances()))

    assert selected == reversed_selected
    assert len(selected) == 24
    assert max(Counter(item.repo for item in selected).values()) <= 3
    distribution = Counter(item.difficulty for item in selected)
    assert set(distribution) == {"<15 min fix", "15 min - 1 hour", "1-4 hours"}
    assert sum(distribution.values()) == 24


def test_provider_projection_excludes_all_gold_fields_and_unknown_input() -> None:
    raw_record = {
        "instance_id": "org__task-1",
        "repo": "org/repo",
        "base_commit": "a" * 40,
        "problem_statement": "Fix an externally authored issue",
        "difficulty": "<15 min fix",
        "patch": "SECRET_GOLD_PATCH",
        "test_patch": "SECRET_TEST_PATCH",
        "FAIL_TO_PASS": "SECRET_FAILING_TEST",
        "PASS_TO_PASS": "SECRET_PASSING_TEST",
    }

    projection = project_provider_visible(SWEbenchInstance.model_validate(raw_record))
    serialized = projection.model_dump_json()

    assert EVALUATOR_ONLY_FIELDS == {"patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS"}
    assert "SECRET" not in serialized
    assert all(field not in projection.model_dump() for field in EVALUATOR_ONLY_FIELDS)


def test_provider_projection_is_identical_for_all_benchmark_strategies() -> None:
    instance = SWEbenchInstance(
        instance_id="org__task-1",
        repo="org/repo",
        base_commit="a" * 40,
        problem_statement="Fix an externally authored issue",
        difficulty="<15 min fix",
    )

    projections = {strategy: project_provider_visible(instance) for strategy in BenchmarkStrategy}

    assert len({projection.model_dump_json() for projection in projections.values()}) == 1


def test_repository_scope_is_independent_of_gold_metadata(tmp_path: Path) -> None:
    _git_repository(tmp_path)
    (tmp_path / "module.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "tests.py").write_text("assert True\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "baseline")

    raw_records = (
        {
            "instance_id": "org__task-1",
            "repo": "org/repo",
            "base_commit": "a" * 40,
            "problem_statement": "Fix the issue",
            "difficulty": "<15 min fix",
            "patch": "--- module.py\n+++ module.py\n",
        },
        {
            "instance_id": "org__task-1",
            "repo": "org/repo",
            "base_commit": "a" * 40,
            "problem_statement": "Fix the issue",
            "difficulty": "<15 min fix",
            "patch": "--- tests.py\n+++ tests.py\n",
        },
    )
    scope = explicit_repository_scope(tmp_path)

    assert scope == ("module.py", "tests.py")
    assert "gold_solution.py" not in scope
    assert [
        project_provider_visible(SWEbenchInstance.model_validate(record)).model_dump()
        for record in raw_records
    ] == [
        project_provider_visible(SWEbenchInstance.model_validate(raw_records[0])).model_dump()
    ] * 2


def test_base_checkout_must_match_the_public_pinned_commit(tmp_path: Path) -> None:
    _git_repository(tmp_path)
    (tmp_path / "module.py").write_text("value = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "baseline")
    head = _git_output(tmp_path, "rev-parse", "HEAD")
    instance = SWEbenchInstance(
        instance_id="org__task-1",
        repo="org/repo",
        base_commit=head,
        problem_statement="Fix the issue",
        difficulty="<15 min fix",
    )

    validate_base_checkout(instance, tmp_path)
    (tmp_path / "module.py").write_text("value = 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="clean"):
        validate_base_checkout(instance, tmp_path)


def test_malformed_external_instance_fails_closed() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        SWEbenchInstance(
            instance_id=" ",
            repo="org/repo",
            base_commit="a" * 40,
            problem_statement="Fix the issue",
            difficulty="<15 min fix",
        )


def test_sample_hash_is_canonical_and_rejects_tampering() -> None:
    selected = select_verified_sample(_instances())
    payload = {
        "schema_version": 1,
        "benchmark": "swebench_verified",
        "dataset": "SWE-bench/SWE-bench_Verified",
        "dataset_revision": "a" * 40,
        "split": "test",
        "sampling_prefix": SAMPLE_PREFIX,
        "sampling_algorithm_version": SAMPLE_ALGORITHM_VERSION,
        "instances": [item.model_dump(mode="json") for item in selected],
        "evaluator": {
            "harness_revision": "b" * 40,
            "docker_image_source": "official-harness-per-instance",
            "minimum_free_disk_gib": 120,
        },
        "gemini_model": "gemini-3.5-flash-lite",
        "codex_model": "gpt-5.6-terra",
        "codex_reasoning_effort": "medium",
    }
    spec = SWEbenchSampleSpec.model_validate({**payload, "sample_sha256": sample_sha256(payload)})

    assert spec.sample_sha256 == spec.canonical_sha256()
    with pytest.raises(ValueError, match="sample_sha256"):
        SWEbenchSampleSpec.model_validate({**payload, "sample_sha256": "0" * 64})


def test_tracked_sample_spec_has_a_valid_canonical_identity() -> None:
    specification = load_sample_spec(
        Path(__file__).parents[1] / "benchmark_specs" / "swebench-verified-v1.json"
    )

    assert len(specification.instances) == 24
    assert specification.sample_sha256 == specification.canonical_sha256()
    assert max(Counter(item.repo for item in specification.instances).values()) <= 3
    assert Counter(item.difficulty for item in specification.instances) == {
        "<15 min fix": 9,
        "15 min - 1 hour": 12,
        "1-4 hours": 2,
        ">4 hours": 1,
    }


def test_evaluator_outcomes_preserve_infrastructure_failure_boundary() -> None:
    assert map_evaluation_result(
        SWEbenchEvaluationResult(status=SWEbenchEvaluationStatus.RESOLVED, diagnostic="resolved")
    ).verified_success
    unresolved = map_evaluation_result(
        SWEbenchEvaluationResult(
            status=SWEbenchEvaluationStatus.UNRESOLVED, diagnostic="unresolved"
        )
    )
    infrastructure = map_evaluation_result(
        SWEbenchEvaluationResult(
            status=SWEbenchEvaluationStatus.INFRASTRUCTURE_FAILURE,
            diagnostic="container unavailable",
        )
    )

    assert unresolved.failure_kind == BenchmarkFailureKind.TASK_FAILED
    assert not unresolved.infrastructure_failure
    assert infrastructure.failure_kind == BenchmarkFailureKind.EXECUTION_FAILED
    assert infrastructure.infrastructure_failure


def test_official_evaluator_command_is_pinned_and_contains_no_gold_data(tmp_path: Path) -> None:
    request = SWEbenchEvaluationRequest(
        harness_directory=tmp_path / "official-harness",
        predictions_path=tmp_path / "evaluator-owned-predictions.jsonl",
        run_id="car-swebench-dry",
        instance_ids=("org__task-1",),
    )

    command = request.command()

    assert command[:3] == ("python", "-m", "swebench.harness.run_evaluation")
    assert "SWE-bench/SWE-bench_Verified" in command
    assert "--split" in command and "test" in command
    assert "patch" not in command
    assert "test_patch" not in command


def test_preflight_is_injected_and_does_not_change_the_host(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("car.benchmark.swebench.evaluator.shutil.which", lambda _: "docker")
    monkeypatch.setattr(
        "car.benchmark.swebench.evaluator.shutil.disk_usage",
        lambda _: type("Usage", (), {"free": 200 * 1024**3})(),
    )

    result = check_preflight(
        tmp_path,
        command_runner=lambda args: (0, "linux\n", "") if args[0] == "docker" else (1, "", ""),
    )

    assert result.docker_available
    assert result.linux_containers
    assert result.ready == result.architecture_compatible


def _git_repository(path: Path) -> None:
    _git(path, "init")
    _git(path, "config", "user.email", "tests@example.invalid")
    _git(path, "config", "user.name", "CAR tests")


def _git(path: Path, *args: str) -> None:
    result = run_git(path, *args)
    assert result is not None and result.returncode == 0, (
        result.stderr if result else "git unavailable"
    )


def _git_output(path: Path, *args: str) -> str:
    result = run_git(path, *args)
    assert result is not None and result.returncode == 0, (
        result.stderr if result else "git unavailable"
    )
    return result.stdout.strip()
