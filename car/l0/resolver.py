"""Resolve only recognized L0 intents into internally constructed Ruff plans."""

from __future__ import annotations

import re
from pathlib import Path

from car.config.models import L0Config
from car.execution.models import CommandSpec, ExecutionPlan
from car.l0.tools import find_tool
from car.repository.models import RepositoryState
from car.router.analysis import analyze_task
from car.router.models import TaskRequest


class L0ResolutionError(RuntimeError):
    pass


def _extract_target(task_text: str, root: Path) -> Path:
    candidates = re.findall(r"(?<!\S)([\w.-]+(?:[\\/][\w.-]+)*\.[A-Za-z0-9]+)", task_text)
    if not candidates:
        raise L0ResolutionError("no explicit target could be resolved")
    candidate = candidates[-1]
    path = (root / candidate).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as error:
        raise L0ResolutionError("target escapes the repository") from error
    if not path.exists() or not path.is_file():
        raise L0ResolutionError("target is not an existing file")
    if path.is_symlink():
        raise L0ResolutionError("symlink targets are not allowed")
    return path


def resolve_l0_plan(
    task: TaskRequest,
    repository: RepositoryState,
    config: L0Config,
    tool_lookup=find_tool,
) -> ExecutionPlan:
    """Construct a safe Ruff-only plan from recognized intent and a valid target."""
    if not config.enabled:
        raise L0ResolutionError("L0 execution is disabled by configuration")
    analysis = analyze_task(task.description, repository)
    if not analysis.possible_l0:
        raise L0ResolutionError("task is not a deterministic L0 candidate")
    target = _extract_target(task.description, repository.root)
    if 1 > config.max_files:
        raise L0ResolutionError("target count exceeds configured L0 file limit")
    if target.suffix.lower() != ".py":
        raise L0ResolutionError("only explicit Python targets are supported in this milestone")
    if find_tool("ruff", tool_lookup) is None:
        raise L0ResolutionError("ruff is not available")
    relative_target = target.relative_to(repository.root).as_posix()
    lowered_task = task.description.lower()
    is_lint_fix = (
        "ruff" in lowered_task
        and ("lint" in lowered_task or "violations" in lowered_task)
        and ("fix" in lowered_task or "violations" in lowered_task)
    )
    operation = "lint_fix" if is_lint_fix else "format"
    command_args = (
        ["ruff", "check", "--fix", relative_target]
        if is_lint_fix
        else ["ruff", "format", relative_target]
    )
    verification_args = (
        ["ruff", "check", relative_target]
        if is_lint_fix
        else ["ruff", "format", "--check", relative_target]
    )
    command = CommandSpec(
        args=command_args, cwd=str(repository.root), timeout_seconds=config.command_timeout_seconds
    )
    verification = CommandSpec(
        args=verification_args,
        cwd=str(repository.root),
        timeout_seconds=config.command_timeout_seconds,
    )
    return ExecutionPlan(
        operation=operation,
        tool="ruff",
        targets=[relative_target],
        commands=[command],
        verification_commands=[verification],
        expected_write_scope=[relative_target],
        timeout_seconds=config.command_timeout_seconds,
    )
