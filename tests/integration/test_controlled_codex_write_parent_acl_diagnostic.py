"""Opt-in causal A/B diagnostic for ACL inheritance on a disposable B2 parent."""

import getpass
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

if os.environ.get("CAR_RUN_LIVE_CODEX_PARENT_ACL_DIAGNOSTIC") != "1":
    pytest.skip(
        "set CAR_RUN_LIVE_CODEX_PARENT_ACL_DIAGNOSTIC=1 to run the controlled Codex "
        "parent ACL diagnostic",
        allow_module_level=True,
    )

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


@dataclass(frozen=True)
class _Outcome:
    name: str
    exit_code: int | None
    timed_out: bool
    calculator_contains_plus: bool
    modified: set[str]
    created: set[str]
    deleted: set[str]
    git_status: tuple[str, ...]


class _PrivateParentDiagnosticManager(IsolatedWorkspaceManager):
    """Recreate the historical private-parent condition only for causal validation."""

    def _prepare_windows_acl(self, parent: Path, source_root: Path) -> bool:
        del parent, source_root
        return True


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=check,
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


def _outcome(name: str, before: dict[str, bytes], workspace: Path, process) -> _Outcome:
    after = _snapshot(workspace)
    before_paths, after_paths = set(before), set(after)
    return _Outcome(
        name=name,
        exit_code=process.exit_code,
        timed_out=process.timed_out,
        calculator_contains_plus="return a + b"
        in (workspace / "calculator.py").read_text(encoding="utf-8"),
        modified={path for path in before_paths & after_paths if before[path] != after[path]},
        created=after_paths - before_paths,
        deleted=before_paths - after_paths,
        git_status=tuple(_git(workspace, "status", "--porcelain").stdout.splitlines()),
    )


def _summary(outcome: _Outcome) -> str:
    """Safe diagnostic output: no paths, task text, model output, or environment values."""
    return (
        f"{outcome.name}: exit={outcome.exit_code} timed_out={outcome.timed_out} "
        f"write_succeeded={outcome.calculator_contains_plus} "
        f"modified={sorted(outcome.modified)} created={sorted(outcome.created)} "
        f"deleted={sorted(outcome.deleted)} git_status={list(outcome.git_status)}"
    )


def _reset_workspace(workspace: Path, before: dict[str, bytes], baseline_head_oid: str) -> None:
    """Restore only the synthetic calculator without recreating the B2 workspace."""
    current = _snapshot(workspace)
    changed = {path for path in set(before) & set(current) if before[path] != current[path]}
    assert changed <= {"calculator.py"}
    assert set(current) == set(before)
    if changed:
        (workspace / "calculator.py").write_bytes(before["calculator.py"])
    assert _snapshot(workspace) == before
    assert _git(workspace, "rev-parse", "HEAD").stdout.strip() == baseline_head_oid
    assert _git(workspace, "diff", "--cached", "--quiet", check=False).returncode == 0
    assert _git(workspace, "status", "--porcelain").stdout == ""


def _assert_disposable_parent(workspace, manager: IsolatedWorkspaceManager, source: Path) -> Path:
    parent = workspace.parent
    assert manager.owns(workspace)
    assert parent == workspace.path.parent
    assert parent.name.startswith("car-codex-worktree-")
    assert parent.parent == Path(tempfile.gettempdir()).resolve()
    try:
        source.resolve().relative_to(parent)
    except ValueError:
        pass
    else:
        pytest.fail("the ACL diagnostic target must be outside the source repository")
    return parent


def _enable_parent_inheritance(parent: Path) -> None:
    """Enable inherited ACL entries only on the exact disposable CAR parent."""
    completed = subprocess.run(
        ["icacls", str(parent), "/inheritance:e"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    assert completed.returncode == 0, "failed to enable inheritance on disposable workspace parent"


def _filtered_acl_evidence(paths: dict[str, Path]) -> tuple[str, ...]:
    """Read-only ACL categories, filtered to relevant principals without paths."""
    current_user = getpass.getuser().casefold()
    principals = (
        (current_user, "current_user"),
        ("system", "SYSTEM"),
        ("administrators", "Administrators"),
        ("codex", "CodexSandbox"),
    )
    evidence: list[str] = []
    for label, path in paths.items():
        completed = subprocess.run(
            ["icacls", str(path)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        for line in completed.stdout.splitlines():
            lowered = line.casefold()
            permission = line.rpartition(":")[2].strip()
            for marker, principal in principals:
                if marker in lowered:
                    evidence.append(f"{label}: {principal}:{permission}")
                    break
    return tuple(evidence)


def test_parent_acl_inheritance_causal_ab_diagnostic(tmp_path: Path):
    """Live-only: the result matrix is evidence, not an assertion that either writes."""
    if os.name != "nt":
        pytest.skip("the parent ACL causal diagnostic is Windows-specific")

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
        "user.name=CAR Parent ACL Diagnostic",
        "-c",
        "user.email=parent-acl-diagnostic@example.invalid",
        "commit",
        "-m",
        "synthetic baseline",
    )
    source_before = _snapshot(source)
    source_head = _git(source, "rev-parse", "HEAD").stdout.strip()
    source_branch = _git(source, "branch", "--show-current").stdout.strip()
    source_index = _git(source, "diff", "--cached", "--binary").stdout

    policy = CodexWritePolicy(enabled=True)
    authorization = CodexWriteAuthorization(authorized=True)
    assert authorization.authorized
    manager = _PrivateParentDiagnosticManager()
    projection_service = BaselineProjectionService(workspace_manager=manager)
    captured = SourceBaselineService().capture(source, policy)
    assert captured.baseline is not None
    projected_result = projection_service.project(source, captured.baseline, policy)
    assert projected_result.projected_workspace is not None
    projected = projected_result.projected_workspace
    workspace = projected.workspace.path
    parent = _assert_disposable_parent(projected.workspace, manager, source)
    assert (workspace / "calculator.py").read_text(encoding="utf-8").endswith("return a - b\n")
    assert (workspace / "unrelated.txt").read_text(encoding="utf-8") == "do not change\n"
    assert _git(workspace, "rev-parse", "HEAD").stdout.strip() == captured.baseline.head_oid

    request = ControlledCodexWriteRequest(
        workspace=projected,
        task=(
            "Modify only calculator.py. Change add so it returns a + b instead of a - b. "
            "Make no other changes. Do not install dependencies, access the network, stage, "
            "or commit."
        ),
        authorized_paths=("calculator.py",),
    )
    argv = _execution_argv(executable, workspace_path=workspace, is_windows=True)
    environment = controlled_child_environment(dict(os.environ))
    assert environment == controlled_child_environment()
    assert argv[argv.index("--cd") + 1] == str(workspace)
    assert str(source) not in argv
    workspace_before = _snapshot(workspace)
    runner = SubprocessControlledCodexRunner()

    try:
        private_process = runner.run(
            argv,
            cwd=workspace,
            stdin=_stdin(request),
            environment=environment,
            timeout_seconds=policy.codex_write_timeout_seconds,
        )
        private = _outcome("PRIVATE_PARENT_ACL", workspace_before, workspace, private_process)
        print(_summary(private))
        _reset_workspace(workspace, workspace_before, captured.baseline.head_oid)

        _enable_parent_inheritance(parent)
        acl_evidence = _filtered_acl_evidence(
            {
                "workspace_parent": parent,
                "workspace": workspace,
                "calculator": workspace / "calculator.py",
            }
        )
        print(f"INHERITED_PARENT_ACL_EVIDENCE={list(acl_evidence)}")

        inherited_process = runner.run(
            argv,
            cwd=workspace,
            stdin=_stdin(request),
            environment=environment,
            timeout_seconds=policy.codex_write_timeout_seconds,
        )
        inherited = _outcome("INHERITED_PARENT_ACL", workspace_before, workspace, inherited_process)
        print(_summary(inherited))
        print(
            "RESULT_MATRIX: "
            f"PRIVATE_PARENT_ACL_WRITE={private.calculator_contains_plus} "
            f"INHERITED_PARENT_ACL_WRITE={inherited.calculator_contains_plus}"
        )
    finally:
        cleanup = projection_service.cleanup(projected)
        assert cleanup.removed, cleanup.message

    assert _snapshot(source) == source_before
    assert _git(source, "rev-parse", "HEAD").stdout.strip() == source_head
    assert _git(source, "branch", "--show-current").stdout.strip() == source_branch
    assert _git(source, "diff", "--cached", "--binary").stdout == source_index
    assert _git(source, "status", "--porcelain").stdout == ""
