"""Offline tests for pilot-only hidden benchmark verification."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from car.authorization import classify_authorized_path
from car.benchmark.context import build_execution_context
from car.benchmark.hidden_oracle import HIDDEN_ORACLE_IDS, run
from car.benchmark.models import BenchmarkCase, BenchmarkStrategy
from car.coding.models import (
    CodingFileContext,
    CodingProposal,
    CodingTaskContext,
    FileChangeOperation,
    ProposedFileChange,
)
from car.coding.verification import CodingVerificationCoordinator
from car.execution.models import CommandSpec
from car.patching.validation import PatchValidator
from car.providers.models import RepositoryClassificationContext
from car.router.models import Route


def _git_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "fixture"
    (root / "tests").mkdir(parents=True)
    (root / "double.py").write_text(
        "def double(value: int) -> int:\n    return value\n", encoding="utf-8"
    )
    (root / "tests" / "test_double.py").write_text(
        "from double import double\n\n\ndef test_visible() -> None:\n    assert double(7) == 14\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=benchmark@example.invalid",
            "-c",
            "user.name=CAR Benchmark",
            "commit",
            "-m",
            "baseline",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return root


def _case(root: Path) -> BenchmarkCase:
    return BenchmarkCase(
        id="double-value",
        category="trivial_localized",
        task="Update double.py so double(value) returns twice the supplied value.",
        fixture=root.name,
        authorized_paths=("double.py", "tests/test_double.py"),
        verification=("pytest",),
        hidden_verification="double-value",
    )


def _run_hidden_oracle(root: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path.cwd())
    return subprocess.run(
        [sys.executable, "-B", "-m", "car.benchmark.hidden_oracle", "double-value"],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_hidden_oracle_is_absent_from_provider_context_and_workspace(tmp_path: Path):
    fixture = _git_fixture(tmp_path)
    case = _case(fixture)

    contexts = [
        build_execution_context(case, fixture, strategy)
        for strategy in (
            BenchmarkStrategy.GEMINI_ONLY,
            BenchmarkStrategy.CODEX_ONLY,
            BenchmarkStrategy.CAR,
        )
    ]

    assert case.hidden_verification in HIDDEN_ORACLE_IDS
    assert all(
        "hidden_oracle" not in {file.path for file in item.coding.files} for item in contexts
    )
    assert all(
        {file.path for file in item.coding.files} == {"double.py", "tests/test_double.py"}
        for item in contexts
    )
    assert not any(path.name == "hidden_oracle.py" for path in fixture.rglob("hidden_oracle.py"))
    assert {tuple(item.verification.commands[0].args) for item in contexts} == {
        ("python", "-B", "-m", "car.benchmark.hidden_oracle", "double-value")
    }


def test_visible_test_is_authorized_but_hidden_oracle_path_is_not(tmp_path: Path):
    fixture = _git_fixture(tmp_path)
    context = CodingTaskContext(
        task="change",
        route=Route.GEMINI,
        repository=RepositoryClassificationContext(
            name="fixture", dirty=False, languages={}, systems=[]
        ),
        files=[
            CodingFileContext(path="double.py", content=""),
            CodingFileContext(path="tests/test_double.py", content=""),
        ],
    )
    visible = CodingProposal(
        summary="visible test change",
        changes=[
            ProposedFileChange(
                path="tests/test_double.py",
                operation=FileChangeOperation.MODIFY,
                patch=(
                    "--- a/tests/test_double.py\n+++ b/tests/test_double.py\n"
                    "@@ -1 +1 @@\n-from double import double\n+from double import double\n"
                ),
            )
        ],
    )
    assert PatchValidator().validate(visible, context, fixture).valid
    assert classify_authorized_path("hidden_oracle.py", _case(fixture).authorized_paths) is None


def test_hidden_oracle_rejects_visible_test_only_change_and_ignores_visible_test_mutation(
    tmp_path: Path,
):
    fixture = _git_fixture(tmp_path)
    visible = fixture / "tests" / "test_double.py"
    visible.write_text("def test_visible() -> None:\n    assert True\n", encoding="utf-8")
    assert _run_hidden_oracle(fixture).returncode != 0

    (fixture / "double.py").write_text(
        "def double(value: int) -> int:\n    return value * 2\n", encoding="utf-8"
    )
    assert _run_hidden_oracle(fixture).returncode == 0


@pytest.mark.parametrize("strategy", tuple(BenchmarkStrategy))
def test_all_strategies_share_the_same_hidden_oracle_and_authorization(tmp_path: Path, strategy):
    fixture = _git_fixture(tmp_path)
    context = build_execution_context(_case(fixture), fixture, strategy)

    assert context.case.authorized_paths == ("double.py", "tests/test_double.py")
    assert context.verification.commands[0].args[-1] == "double-value"
    assert "tests/test_double.py" in {file.path for file in context.coding.files}


def test_hidden_oracle_plan_satisfies_the_existing_safe_verification_contract(tmp_path: Path):
    fixture = _git_fixture(tmp_path)
    plan = build_execution_context(
        _case(fixture), fixture, BenchmarkStrategy.CODEX_ONLY
    ).verification

    assert plan.commands
    assert CodingVerificationCoordinator._is_safe_command(plan.commands[0], fixture.resolve())
    assert not CodingVerificationCoordinator._is_safe_command(
        CommandSpec(
            args=["python", "-B", "-m", "car.benchmark.hidden_oracle", "double-value", "extra"],
            cwd=str(fixture),
            timeout_seconds=60,
        ),
        fixture.resolve(),
    )


def test_hidden_oracle_registry_contains_all_twenty_cases_and_fails_closed():
    assert len(HIDDEN_ORACLE_IDS) == 20
    assert {
        "slug-normalization",
        "retry-delay-cap",
        "bounded-percentage",
        "chunk-boundaries",
        "inclusive-date-overlap",
        "deduplicate-preserve-order",
        "shipping-after-discount",
        "inventory-reservation",
        "permission-inheritance",
        "cache-expiration",
        "configuration-precedence",
        "event-deduplication",
        "atomic-account-transfer",
        "dependency-order",
        "ttl-lru-cache",
    }.issubset(HIDDEN_ORACLE_IDS)
    with pytest.raises(ValueError, match="unsupported hidden benchmark oracle"):
        run("not-an-oracle")
