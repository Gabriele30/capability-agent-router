from pathlib import Path

import pytest

from car.config.models import L0Config
from car.execution.models import CommandResult, CommandSpec, ExecutionPlan, ExecutionStatus
from car.execution.safety import SafetyLevel, classify_plan
from car.l0.executor import L0Executor
from car.l0.resolver import L0ResolutionError, resolve_l0_plan
from car.repository.scanner import scan_repository
from car.router.models import TaskRequest
from car.verification.engine import VerificationEngine


def make_plan(root: Path, target: str = "sample.py") -> ExecutionPlan:
    command = CommandSpec(args=["ruff", "format", target], cwd=str(root), timeout_seconds=10)
    verify = CommandSpec(
        args=["ruff", "format", "--check", target], cwd=str(root), timeout_seconds=10
    )
    return ExecutionPlan(
        operation="format",
        tool="ruff",
        targets=[target],
        commands=[command],
        verification_commands=[verify],
        expected_write_scope=[target],
        timeout_seconds=10,
    )


class WritingRunner:
    def __init__(
        self,
        root: Path,
        verification_exit_code: int = 0,
        command_exit_code: int = 0,
        extra: bool = False,
    ):
        self.root = root
        self.verification_exit_code = verification_exit_code
        self.command_exit_code = command_exit_code
        self.extra = extra
        self.calls = 0

    def run(self, command: CommandSpec) -> CommandResult:
        self.calls += 1
        if self.calls == 1:
            (self.root / "sample.py").write_bytes(b"formatted\n")
            if self.extra:
                (self.root / "unexpected.py").write_text("changed\n", encoding="utf-8")
            exit_code = self.command_exit_code
        else:
            exit_code = self.verification_exit_code
        return CommandResult(command=command, exit_code=exit_code)


def test_format_python_file_resolves_to_ruff_plan(git_repository: Path) -> None:
    (git_repository / "sample.py").write_text("x=1\n", encoding="utf-8")
    plan = resolve_l0_plan(
        TaskRequest(description="Format sample.py"),
        scan_repository(git_repository),
        L0Config(),
        tool_lookup=lambda _: "ruff",
    )
    assert plan.commands[0].args == ["ruff", "format", "sample.py"]
    assert plan.verification_commands[0].args == ["ruff", "format", "--check", "sample.py"]


def test_ruff_lint_fix_resolves_to_ruff_plan(git_repository: Path) -> None:
    (git_repository / "sample.py").write_text("x=1\n", encoding="utf-8")
    plan = resolve_l0_plan(
        TaskRequest(description="Run Ruff lint fix on sample.py"),
        scan_repository(git_repository),
        L0Config(),
        tool_lookup=lambda _: "ruff",
    )
    assert plan.commands[0].args == ["ruff", "check", "--fix", "sample.py"]
    assert plan.verification_commands[0].args == ["ruff", "check", "sample.py"]


def test_target_outside_repository_is_blocked(git_repository: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(L0ResolutionError, match="escapes"):
        resolve_l0_plan(
            TaskRequest(description="Format ../outside.py"),
            scan_repository(git_repository),
            L0Config(),
            tool_lookup=lambda _: "ruff",
        )


def test_symlink_outside_repository_is_blocked(git_repository: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    link = git_repository / "link.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable in this environment")
    with pytest.raises(L0ResolutionError, match="escapes|symlink"):
        resolve_l0_plan(
            TaskRequest(description="Format link.py"),
            scan_repository(git_repository),
            L0Config(),
            tool_lookup=lambda _: "ruff",
        )


def test_missing_ruff_never_modifies_file(git_repository: Path) -> None:
    target = git_repository / "sample.py"
    target.write_bytes(b"original\n")
    with pytest.raises(L0ResolutionError, match="ruff is not available"):
        resolve_l0_plan(
            TaskRequest(description="Format sample.py"),
            scan_repository(git_repository),
            L0Config(),
            tool_lookup=lambda _: None,
        )
    assert target.read_bytes() == b"original\n"


def test_success_requires_verification(git_repository: Path) -> None:
    (git_repository / "sample.py").write_bytes(b"original\n")
    runner = WritingRunner(git_repository)
    result = L0Executor(runner=runner, verifier=VerificationEngine(runner)).execute(
        make_plan(git_repository)
    )
    assert result.status == ExecutionStatus.SUCCEEDED
    assert (git_repository / "sample.py").read_bytes() == b"formatted\n"


def test_verification_failure_restores_exact_user_content(git_repository: Path) -> None:
    target = git_repository / "sample.py"
    original = b"user change before CAR\r\n"
    target.write_bytes(original)
    runner = WritingRunner(git_repository, verification_exit_code=1)
    result = L0Executor(runner=runner, verifier=VerificationEngine(runner)).execute(
        make_plan(git_repository)
    )
    assert result.status == ExecutionStatus.ROLLED_BACK
    assert result.rollback_succeeded is True
    assert target.read_bytes() == original


def test_command_failure_rolls_back(git_repository: Path) -> None:
    target = git_repository / "sample.py"
    target.write_bytes(b"original\n")
    runner = WritingRunner(git_repository, command_exit_code=1)
    result = L0Executor(runner=runner, verifier=VerificationEngine(runner)).execute(
        make_plan(git_repository)
    )
    assert result.status == ExecutionStatus.ROLLED_BACK
    assert target.read_bytes() == b"original\n"


def test_scope_violation_is_rolled_back(git_repository: Path) -> None:
    target = git_repository / "sample.py"
    target.write_bytes(b"original\n")
    runner = WritingRunner(git_repository, extra=True)
    result = L0Executor(runner=runner, verifier=VerificationEngine(runner)).execute(
        make_plan(git_repository)
    )
    assert result.status == ExecutionStatus.ROLLED_BACK
    assert not (git_repository / "unexpected.py").exists()
    assert target.read_bytes() == b"original\n"


def test_arbitrary_command_plan_is_blocked(git_repository: Path) -> None:
    plan = make_plan(git_repository)
    plan.commands[0].args = ["python", "anything.py"]
    assert classify_plan(plan) == SafetyLevel.FORBIDDEN
    assert L0Executor().execute(plan).status == ExecutionStatus.BLOCKED
