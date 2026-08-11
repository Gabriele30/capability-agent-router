"""Build real CAR application inputs for an owned benchmark workspace."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from car.benchmark.models import BenchmarkCase, BenchmarkStrategy
from car.coding.models import CodingFileContext, CodingTaskContext
from car.execution.models import CommandSpec
from car.providers.models import RepositoryClassificationContext
from car.repository.models import RepositoryState
from car.repository.scanner import scan_repository
from car.router.consultation import RoutingEvaluation, evaluate_routing
from car.router.models import TaskRequest, UserMode
from car.verification.models import VerificationPlan


class BenchmarkExecutionContext(BaseModel):
    """Internal execution context; absolute paths are never part of benchmark results."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    case: BenchmarkCase
    strategy: BenchmarkStrategy
    workspace: Path
    repository: RepositoryState
    routing: RoutingEvaluation
    coding: CodingTaskContext
    verification: VerificationPlan


def build_execution_context(
    case: BenchmarkCase, workspace: Path, strategy: BenchmarkStrategy
) -> BenchmarkExecutionContext:
    root = workspace.resolve()
    repository = scan_repository(root)
    mode = UserMode.GEMINI if strategy == BenchmarkStrategy.GEMINI_ONLY else UserMode.AUTO
    routing = evaluate_routing(TaskRequest(description=case.task), repository, mode, provider=None)
    files = []
    for relative in case.authorized_paths:
        target = (root / relative).resolve(strict=True)
        target.relative_to(root)
        if target.is_symlink() or not target.is_file():
            raise ValueError(f"benchmark path is not a regular file: {relative}")
        files.append(CodingFileContext(path=relative, content=target.read_text(encoding="utf-8")))
    verification = _verification_plan(root, case.verification, case.authorized_paths)
    coding = CodingTaskContext(
        task=case.task,
        route=routing.final_decision.route,
        repository=RepositoryClassificationContext(
            name=repository.name,
            branch=repository.git.branch,
            dirty=repository.git.dirty,
            languages=repository.languages.counts,
            systems=repository.project_signals.systems,
        ),
        files=files,
    )
    return BenchmarkExecutionContext(
        case=case,
        strategy=strategy,
        workspace=root,
        repository=repository,
        routing=routing,
        coding=coding,
        verification=verification,
    )


def _verification_plan(
    root: Path, checks: tuple[str, ...], paths: tuple[str, ...]
) -> VerificationPlan:
    commands: list[CommandSpec] = []
    for check in checks:
        if check == "ruff":
            args = ["ruff", "check", *paths]
        elif check == "pytest":
            args = ["python", "-m", "pytest"]
        else:
            raise ValueError(f"unsupported benchmark verification: {check}")
        commands.append(CommandSpec(args=args, cwd=str(root), timeout_seconds=60))
    if not commands:
        raise ValueError("benchmark verification plan must not be empty")
    return VerificationPlan(commands=commands)
