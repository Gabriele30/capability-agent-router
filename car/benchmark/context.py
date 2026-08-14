"""Build real CAR application inputs for an owned benchmark workspace."""

import re
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

# These limits intentionally bound provider-visible source, not the exact
# authorization set or CAR's independent proposal/delta limits.
MAX_PROVIDER_CONTEXT_FILES = 20
MAX_PROVIDER_CONTEXT_BYTES = 120_000
BENCHMARK_TRACKED_FILE_SCOPE = (
    "WRITE SCOPE\n"
    "CAR authorizes final task changes only to existing tracked regular files in this "
    "isolated repository. CAR retains the exact membership set and independently validates "
    "every proposed path. Do not modify tests or verification files unless they are existing "
    "tracked regular files needed for the task. Optional safe auxiliary paths remain subject "
    "to CAR's fixed policy; everything else is read-only."
)


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
    candidates: list[tuple[str, str]] = []
    for relative in case.authorized_paths:
        target = (root / relative).resolve(strict=True)
        target.relative_to(root)
        if target.is_symlink() or not target.is_file():
            raise ValueError(f"benchmark path is not a regular file: {relative}")
        text = _read_provider_text(target)
        if text is not None:
            candidates.append((relative, text))
    files = _select_provider_context(case.task, candidates)
    verification = _verification_plan(root, case)
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
        authorized_paths=case.authorized_paths,
        authorization_summary=BENCHMARK_TRACKED_FILE_SCOPE,
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


def _read_provider_text(path: Path) -> str | None:
    """Return strict UTF-8 text only; authorization remains independent."""
    content = path.read_bytes()
    if b"\x00" in content:
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _select_provider_context(
    task: str, candidates: list[tuple[str, str]]
) -> list[CodingFileContext]:
    """Choose a small, deterministic public-text subset independent of providers."""
    terms = set(re.findall(r"[a-z0-9]{3,}", task.casefold()))

    def rank(candidate: tuple[str, str]) -> tuple[int, int, str]:
        path, _ = candidate
        path_terms = set(re.findall(r"[a-z0-9]{3,}", path.casefold()))
        matches = len(terms & path_terms)
        return (-matches, len(path), path)

    selected: list[CodingFileContext] = []
    total_bytes = 0
    for path, content in sorted(candidates, key=rank):
        content_bytes = len(content.encode("utf-8"))
        if content_bytes > MAX_PROVIDER_CONTEXT_BYTES:
            continue
        if len(selected) >= MAX_PROVIDER_CONTEXT_FILES:
            break
        if total_bytes + content_bytes > MAX_PROVIDER_CONTEXT_BYTES:
            continue
        selected.append(CodingFileContext(path=path, content=content))
        total_bytes += content_bytes
    return selected


def _verification_plan(root: Path, case: BenchmarkCase) -> VerificationPlan:
    if case.hidden_verification is not None:
        return VerificationPlan(
            commands=[
                CommandSpec(
                    args=[
                        "python",
                        "-B",
                        "-m",
                        "car.benchmark.hidden_oracle",
                        case.hidden_verification,
                    ],
                    cwd=str(root),
                    timeout_seconds=60,
                )
            ]
        )
    commands: list[CommandSpec] = []
    for check in case.verification:
        if check == "ruff":
            args = ["ruff", "check", *case.authorized_paths]
        elif check == "pytest":
            args = ["python", "-m", "pytest"]
        else:
            raise ValueError(f"unsupported benchmark verification: {check}")
        commands.append(CommandSpec(args=args, cwd=str(root), timeout_seconds=60))
    if not commands:
        raise ValueError("benchmark verification plan must not be empty")
    return VerificationPlan(commands=commands)
