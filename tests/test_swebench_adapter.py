"""Offline tests for the isolated SWE-bench Verified adapter."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from car.benchmark.models import BenchmarkStrategy
from car.benchmark.results import BenchmarkFailureKind
from car.benchmark.swebench.evaluator import (
    QUALIFIED_SWEBENCH_VERSION,
    QUALIFIED_WSL_DISTRIBUTION,
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
    assert specification.sample_sha256 == (
        "dc51478dd492d91dc6b117e89c0b8adf61710d751de5b3bfee9b2a263d5f7d5f"
    )
    assert tuple(item.instance_id for item in specification.instances) == (
        "astropy__astropy-13398",
        "astropy__astropy-14369",
        "sympy__sympy-13647",
        "django__django-14311",
        "matplotlib__matplotlib-14623",
        "django__django-15382",
        "scikit-learn__scikit-learn-26194",
        "sympy__sympy-22914",
        "django__django-15732",
        "pydata__xarray-6744",
        "astropy__astropy-13977",
        "scikit-learn__scikit-learn-25747",
        "matplotlib__matplotlib-20826",
        "sympy__sympy-24443",
        "sphinx-doc__sphinx-9281",
        "sphinx-doc__sphinx-9591",
        "matplotlib__matplotlib-24149",
        "scikit-learn__scikit-learn-12585",
        "sphinx-doc__sphinx-9367",
        "pytest-dev__pytest-7432",
        "pytest-dev__pytest-6202",
        "pydata__xarray-4094",
        "pytest-dev__pytest-5262",
        "pydata__xarray-6992",
    )
    assert "psf__requests-2317" not in {item.instance_id for item in specification.instances}
    assert specification.instances[19].instance_id == "pytest-dev__pytest-7432"
    assert specification.instances[19].repo == "pytest-dev/pytest"


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
    empty_patch = map_evaluation_result(
        SWEbenchEvaluationResult(
            status=SWEbenchEvaluationStatus.EMPTY_PATCH, diagnostic="empty patch"
        )
    )
    assert not empty_patch.verified_success
    assert empty_patch.failure_kind == BenchmarkFailureKind.TASK_FAILED
    assert infrastructure.failure_kind == BenchmarkFailureKind.EXECUTION_FAILED
    assert infrastructure.infrastructure_failure


def test_qualified_wsl_evaluator_command_contains_no_gold_data(tmp_path: Path) -> None:
    request = SWEbenchEvaluationRequest(
        evaluator_directory=tmp_path / "official-evaluator",
        predictions_path=tmp_path / "evaluator-owned-predictions.jsonl",
        run_id="car-swebench-dry",
        instance_ids=("org__task-1",),
    )

    command = request.command()

    assert command[:7] == (
        "wsl.exe",
        "-d",
        QUALIFIED_WSL_DISTRIBUTION,
        "--",
        "python3",
        "-m",
        "swebench.harness.run_evaluation",
    )
    assert "princeton-nlp/SWE-bench_Verified" in command
    assert "--split" in command and "test" in command
    assert "patch" not in command
    assert "test_patch" not in command


def test_qualified_preflight_is_injected_and_does_not_change_the_host(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("car.benchmark.swebench.evaluator.shutil.which", lambda _: "wsl.exe")
    monkeypatch.setattr(
        "car.benchmark.swebench.evaluator.shutil.disk_usage",
        lambda _: type("Usage", (), {"free": 200 * 1024**3})(),
    )

    def runner(args: list[str]) -> tuple[int, str, str]:
        if args[-2:] == ["uname", "-s"]:
            return 0, "Linux\n", ""
        if args[-2:] == ["uname", "-m"]:
            return 0, "x86_64\n", ""
        if args[-2:] == ["--format", "{{.OSType}} {{.Architecture}}"]:
            return 0, "linux amd64\n", ""
        if any("importlib.metadata" in argument for argument in args):
            return 0, f"{QUALIFIED_SWEBENCH_VERSION}\n", ""
        return 1, "", "unexpected command"

    result = check_preflight(tmp_path, command_runner=runner)

    assert result.docker_available
    assert result.linux_execution
    assert result.linux_containers
    assert result.swebench_version == QUALIFIED_SWEBENCH_VERSION
    assert result.runtime_contract_valid
    assert result.dataset_contract_valid
    assert result.max_workers_policy_valid
    assert result.ready


def test_preflight_fails_closed_for_windows_or_old_contract(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("car.benchmark.swebench.evaluator.shutil.which", lambda _: None)
    monkeypatch.setattr(
        "car.benchmark.swebench.evaluator.shutil.disk_usage",
        lambda _: type("Usage", (), {"free": 200 * 1024**3})(),
    )
    request = SWEbenchEvaluationRequest(
        evaluator_directory=tmp_path,
        predictions_path=tmp_path / "prediction.jsonl",
        run_id="preflight-old-contract",
        instance_ids=("org__task-1",),
        dataset="SWE-bench/SWE-bench_Verified",
        dataset_revision="03e151cf5560b1af6a4363c6a9d766deaaea6b56",
    )

    result = check_preflight(tmp_path, request=request, command_runner=lambda _: (1, "", ""))

    assert not result.ready
    assert not result.linux_execution
    assert not result.dataset_contract_valid
    assert result.runtime_contract_valid
    assert not result.swebench_version_compatible
    assert any("Windows Python" in message for message in result.messages)


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
