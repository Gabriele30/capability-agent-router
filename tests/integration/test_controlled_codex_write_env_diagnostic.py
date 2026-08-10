"""Explicit opt-in A/B diagnostic for the controlled-write child environment.

This is intentionally not a production policy test. It runs the exact fixed Codex
command twice against one disposable projected workspace, varying only the child
environment. It does not print environment values, prompt contents, or local paths.
"""

import os

import pytest

if os.environ.get("CAR_RUN_LIVE_CODEX_ENV_DIAGNOSTIC") != "1":
    pytest.skip(
        "set CAR_RUN_LIVE_CODEX_ENV_DIAGNOSTIC=1 to run the controlled Codex "
        "environment A/B diagnostic",
        allow_module_level=True,
    )

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from car.codex_write.baseline import SourceBaselineService
from car.codex_write.models import CodexWriteAuthorization, CodexWritePolicy
from car.codex_write.projection import BaselineProjectionService
from car.codex_write.runtime import (
    SubprocessControlledCodexRunner,
    _execution_argv,
    _stdin,
    controlled_child_environment,
)
from car.codex_write.runtime_models import ControlledCodexWriteRequest
from car.codex_write.workspace import IsolatedWorkspaceManager

_WINDOWS_CORE_EXPANSION_KEYS = (
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMW6432",
)


@dataclass(frozen=True)
class _VariantOutcome:
    name: str
    exit_code: int | None
    timed_out: bool
    executable_not_found: bool
    calculator_present_before: bool
    modified: set[str]
    created: set[str]
    deleted: set[str]


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def _windows_core_environment(parent: dict[str, str]) -> dict[str, str]:
    """Return current CAR env plus a bounded, non-secret Windows core candidate set."""
    environment = controlled_child_environment(parent)
    source = {name.casefold(): value for name, value in parent.items()}
    present = {name.casefold() for name in environment}
    for name in _WINDOWS_CORE_EXPANSION_KEYS:
        if name.casefold() not in present and (value := source.get(name.casefold())) is not None:
            environment[name] = value
            present.add(name.casefold())
    assert len(present) == len(environment)
    return environment


def _run_variant(
    *,
    name: str,
    runner: SubprocessControlledCodexRunner,
    argv: list[str],
    request: ControlledCodexWriteRequest,
    environment: dict[str, str],
    timeout_seconds: float,
) -> _VariantOutcome:
    workspace = request.workspace.workspace.path
    before = _snapshot(workspace)
    result = runner.run(
        argv,
        cwd=workspace,
        stdin=_stdin(request),
        environment=environment,
        timeout_seconds=timeout_seconds,
    )
    after = _snapshot(workspace)
    before_paths, after_paths = set(before), set(after)
    return _VariantOutcome(
        name=name,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        executable_not_found=result.executable_not_found,
        calculator_present_before="calculator.py" in before,
        modified={path for path in before_paths & after_paths if before[path] != after[path]},
        created=after_paths - before_paths,
        deleted=before_paths - after_paths,
    )


def _summary(outcome: _VariantOutcome, environment: dict[str, str]) -> str:
    """Diagnostics intentionally contain only names and boolean/status metadata."""
    names = ",".join(sorted(environment, key=str.casefold))
    return (
        f"{outcome.name}: env_names=[{names}] exit={outcome.exit_code} "
        f"timed_out={outcome.timed_out} executable_not_found={outcome.executable_not_found} "
        f"calculator_present_before={outcome.calculator_present_before} "
        f"modified={sorted(outcome.modified)} created={sorted(outcome.created)} "
        f"deleted={sorted(outcome.deleted)}"
    )


def test_controlled_codex_environment_ab_diagnostic(tmp_path: Path):
    """Live-only; no tool-only equivalent exists for the Codex exec/model path."""
    if os.name != "nt":
        pytest.skip("the controlled-write environment A/B diagnostic is Windows-specific")

    executable = shutil.which("codex")
    if executable is None:
        pytest.skip("local Codex CLI is unavailable")

    source = tmp_path / "synthetic-source"
    source.mkdir()
    _git(source, "init", "-b", "main")
    calculator = source / "calculator.py"
    calculator.write_text("def add(a: int, b: int) -> int:\n    return a - b\n", encoding="utf-8")
    (source / "unrelated.txt").write_text("do not change\n", encoding="utf-8")
    _git(source, "add", "calculator.py", "unrelated.txt")
    _git(
        source,
        "-c",
        "user.name=CAR Diagnostic",
        "-c",
        "user.email=diagnostic@example.invalid",
        "commit",
        "-m",
        "synthetic baseline",
    )
    source_before = _snapshot(source)

    policy = CodexWritePolicy(enabled=True)
    authorization = CodexWriteAuthorization(authorized=True)
    assert authorization.authorized
    manager = IsolatedWorkspaceManager()
    projection_service = BaselineProjectionService(workspace_manager=manager)
    captured = SourceBaselineService().capture(source, policy)
    assert captured.baseline is not None
    projected_result = projection_service.project(source, captured.baseline, policy)
    assert projected_result.projected_workspace is not None
    projected = projected_result.projected_workspace
    request = ControlledCodexWriteRequest(
        workspace=projected,
        task=(
            "Modify only calculator.py. Change add so it returns a + b instead of a - b. "
            "Make no other changes. Do not install dependencies, access the network, stage, "
            "or commit."
        ),
        authorized_paths=("calculator.py",),
    )
    argv = _execution_argv(executable, workspace_path=projected.workspace.path, is_windows=True)
    assert argv[argv.index("--cd") + 1] == str(projected.workspace.path)
    assert str(source) not in argv

    parent = dict(os.environ)
    current_car_environment = controlled_child_environment(parent)
    windows_core_environment = _windows_core_environment(parent)
    assert current_car_environment == controlled_child_environment(parent)
    assert len({name.casefold() for name in current_car_environment}) == len(
        current_car_environment
    )
    assert len({name.casefold() for name in windows_core_environment}) == len(
        windows_core_environment
    )

    runner = SubprocessControlledCodexRunner()
    workspace_before = _snapshot(projected.workspace.path)
    try:
        current = _run_variant(
            name="CURRENT_CAR_ENV",
            runner=runner,
            argv=argv,
            request=request,
            environment=current_car_environment,
            timeout_seconds=policy.codex_write_timeout_seconds,
        )
        print(_summary(current, current_car_environment))
        assert current.calculator_present_before
        assert not current.created and not current.deleted, _summary(
            current, current_car_environment
        )
        if current.modified:
            assert current.modified == {"calculator.py"}, _summary(current, current_car_environment)
            (projected.workspace.path / "calculator.py").write_bytes(
                workspace_before["calculator.py"]
            )
        assert _snapshot(projected.workspace.path) == workspace_before

        expanded = _run_variant(
            name="WINDOWS_CORE_ENV",
            runner=runner,
            argv=argv,
            request=request,
            environment=windows_core_environment,
            timeout_seconds=policy.codex_write_timeout_seconds,
        )
        print(_summary(expanded, windows_core_environment))
        assert expanded.calculator_present_before
        assert not expanded.created and not expanded.deleted, _summary(
            expanded, windows_core_environment
        )
        assert expanded.modified <= {"calculator.py"}, _summary(expanded, windows_core_environment)
    finally:
        cleanup = projection_service.cleanup(projected)
        assert cleanup.removed, cleanup.message

    assert _snapshot(source) == source_before
