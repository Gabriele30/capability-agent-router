"""Typer commands and Rich presentation for CAR."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from car import __version__
from car.application.codex import CodexExecutionPolicy
from car.application.coding_execution import CodingPipelineExecutionPolicy
from car.application.execution_gateway import (
    CodingFlowAuthorization,
    CodingFlowExecutionRequest,
    CodingFlowGateway,
)
from car.application.routing import build_gemini_provider, evaluate_analysis
from car.benchmark.executors import BenchmarkExecutionDependencies, CARBenchmarkExecutor
from car.benchmark.manifest import load_manifest
from car.benchmark.models import BenchmarkStrategy
from car.benchmark.presentation import render_benchmark_report
from car.benchmark.runner import BenchmarkRunner
from car.benchmark.service import run_manifest_benchmark
from car.cli.presentation import present_execution_result
from car.codex.runtime import LocalCodexRuntime
from car.codex_write.models import CodexWriteAuthorization, CodexWritePolicy
from car.coding.gemini import GeminiCodingProvider
from car.coding.models import (
    CodingFileContext,
    CodingTaskContext,
    normalize_repository_relative_path,
)
from car.config.models import CarConfig
from car.escalation.models import HandoffPolicy
from car.execution.models import CommandSpec, ExecutionResult, ExecutionStatus
from car.l0.executor import L0Executor
from car.l0.resolver import L0ResolutionError, resolve_l0_plan
from car.patching.models import PatchValidationPolicy
from car.providers.models import RepositoryClassificationContext
from car.repository.models import RepositoryState
from car.repository.scanner import RepositoryScanError, scan_repository
from car.router.consultation import RoutingEvaluation, evaluate_routing
from car.router.engine import DecisionEngine
from car.router.models import Route, RoutingDecision, TaskRequest, UserMode
from car.verification.models import VerificationPlan

app = typer.Typer(add_completion=False, help="Capability-aware software engineering task routing.")
console = Console()
LOGGER = logging.getLogger(__name__)


def _build_coding_provider(config: CarConfig) -> GeminiCodingProvider:
    """Construct the coding adapter only after an authorized CLI invocation."""
    return GeminiCodingProvider(config.providers.gemini)


def _build_codex_runtime() -> LocalCodexRuntime:
    """Construct the existing read-only runtime only for opted-in analysis."""
    return LocalCodexRuntime()


def _build_benchmark_executor(
    config: CarConfig, *, codex_model: str | None = None
) -> CARBenchmarkExecutor:
    """Construct live-capable adapters only after the benchmark command is invoked."""
    return CARBenchmarkExecutor(
        BenchmarkExecutionDependencies(
            coding_provider=_build_coding_provider(config),
            codex_runtime=_build_codex_runtime(),
            codex_write_policy=CodexWritePolicy(enabled=True),
            codex_model=codex_model,
        )
    )


def _context_paths(root: Path) -> tuple[Path, Path, Path]:
    context_directory = root / ".car-context"
    return context_directory, context_directory / "config.json", context_directory / "state.json"


def _load_config(config_path: Path) -> CarConfig:
    try:
        return CarConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, json.JSONDecodeError) as error:
        raise typer.BadParameter(f"CAR configuration is invalid: {error}") from error


def _scan_or_exit() -> RepositoryState:
    try:
        return scan_repository()
    except RepositoryScanError as error:
        console.print(f"[red]Error:[/] {error}")
        raise typer.Exit(code=1) from error


def _title() -> None:
    console.print("[bold]CAR - Capability Agent Router[/]\n")


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO, format="%(levelname)s %(message)s"
    )
    if verbose:
        LOGGER.debug("Verbose logging enabled")


@app.callback()
def callback(
    verbose: Annotated[bool, typer.Option("--verbose", help="Show diagnostic logging.")] = False,
) -> None:
    """Capability-aware orchestration for software engineering agents."""
    _configure_logging(verbose)


@app.command()
def version() -> None:
    """Show CAR's installed version."""
    _title()
    console.print(f"version {__version__}")


@app.command()
def init() -> None:
    """Initialize CAR's local context in the current Git repository."""
    repository = _scan_or_exit()
    context_directory, config_path, state_path = _context_paths(repository.root)
    _title()
    console.print("[bold]Repository[/]")
    console.print(repository.root)
    console.print("\nInitializing CAR...\n")
    console.print("[green]OK[/] Git repository detected")
    try:
        created_context = not context_directory.exists()
        context_directory.mkdir(parents=True, exist_ok=True)
        if config_path.exists():
            _load_config(config_path)
            config_message = "configuration already valid"
        else:
            config_path.write_text(CarConfig().model_dump_json(indent=2) + "\n", encoding="utf-8")
            config_message = "configuration created"
        if not state_path.exists():
            state_path.write_text(
                json.dumps(
                    {"schema_version": 1, "initialized_at": datetime.now(UTC).isoformat()}, indent=2
                )
                + "\n",
                encoding="utf-8",
            )
            state_message = "state created"
        else:
            state_message = "state already present"
    except OSError as error:
        console.print(f"[red]Error:[/] Unable to initialize CAR: {error}")
        raise typer.Exit(code=1) from error
    console.print(
        f"[green]OK[/] .car-context {'created' if created_context else 'already present'}"
    )
    console.print(f"[green]OK[/] {config_message}")
    console.print(f"[green]OK[/] {state_message}")
    console.print("\n[bold green]CAR initialized successfully.[/]")


def _print_status(repository: RepositoryState) -> None:
    table = Table(title="Repository", show_header=False, box=None)
    table.add_row("Name:", repository.name)
    table.add_row("Root:", str(repository.root))
    table.add_row("Branch:", repository.git.branch or "detached HEAD")
    table.add_row("Git state:", "dirty" if repository.git.dirty else "clean")
    console.print(table)
    console.print()
    console.print("[bold]Repository intelligence[/]")
    console.print(f"Tracked files: {repository.tracked_file_count}")
    if repository.languages.counts:
        languages = Table(title="Languages", box=None, show_header=False)
        for language, count in repository.languages.counts.items():
            languages.add_row(language, str(count))
        console.print(languages)
    if repository.project_signals.systems:
        console.print("[bold]Detected systems:[/]")
        for system in repository.project_signals.systems:
            console.print(f"[green]OK[/] {system}")
    if repository.git.dirty:
        console.print("\n[bold]Working tree[/]")
        console.print(f"Modified:   {len(repository.git.modified_files)}")
        console.print(f"Staged:     {len(repository.git.staged_files)}")
        console.print(f"Untracked:  {len(repository.git.untracked_files)}")


@app.command()
def status() -> None:
    """Show deterministic repository intelligence and local CAR state."""
    repository = _scan_or_exit()
    _, config_path, _ = _context_paths(repository.root)
    initialized = config_path.exists()
    if initialized:
        _load_config(config_path)
    _title()
    _print_status(repository)
    console.print("\n[bold]CAR[/]")
    console.print(f"Initialized: {'yes' if initialized else 'no'}")
    console.print(f"Version:     {__version__}")


def _routing_inputs(
    description: str, mode: UserMode
) -> tuple[TaskRequest, RepositoryState, RoutingDecision]:
    try:
        request = TaskRequest(description=description)
    except ValidationError as error:
        console.print(f"[red]Error:[/] Invalid task: {error.errors()[0]['msg']}")
        raise typer.Exit(code=2) from error
    repository = _scan_or_exit()
    _, config_path, _ = _context_paths(repository.root)
    config = _load_config(config_path) if config_path.exists() else CarConfig()
    selected_mode = mode if mode != UserMode.AUTO else config.default_mode
    decision = DecisionEngine().decide(request, repository, selected_mode, config.routing_policy)
    return request, repository, decision


def _parse_analyze_mode(value: str) -> UserMode:
    try:
        return UserMode(value.replace("-", "_"))
    except ValueError as error:
        valid = ", ".join(item.value.replace("_", "-") for item in UserMode)
        raise typer.BadParameter(f"Mode must be one of: {valid}.") from error


def _print_decision(request: TaskRequest, mode: UserMode, decision: RoutingDecision) -> None:
    console.print("[bold]Task[/]")
    console.print(request.description)
    console.print("\n[bold]Mode[/]")
    console.print(mode.value.upper())
    console.print("\n[bold]Analysis[/]")
    console.print("Categories: " + ", ".join(category.value for category in decision.categories))
    console.print(f"Complexity: {decision.complexity.value.upper()}")
    console.print(f"Scope:      {decision.scope.size.value.upper()}")
    console.print("\n[bold]Risk[/]")
    console.print(f"{decision.risk.score:.2f} / {decision.risk.level.value.upper()}")
    console.print("\n[bold]Decision[/]")
    console.print(f"Route:      {decision.route.value.upper()}")
    console.print(f"Confidence: {decision.confidence:.2f}")
    console.print("\n[bold]Reasons[/]")
    for reason in decision.reasons:
        console.print(f"- {reason}")
    console.print("\n[bold]Matched rules[/]")
    for rule in decision.matched_rules:
        console.print(f"- {rule}")


def _print_evaluation(request: TaskRequest, evaluation: RoutingEvaluation) -> None:
    decision = evaluation.deterministic_decision
    console.print("[bold]Task[/]")
    console.print(request.description)
    console.print("\n[bold]Deterministic[/]")
    console.print(f"Route:      {decision.route.value.upper()}")
    console.print(f"Risk:       {decision.risk.score:.2f} / {decision.risk.level.value.upper()}")
    console.print(f"Complexity: {decision.complexity.value.upper()}")
    console.print("Categories: " + ", ".join(category.value for category in decision.categories))
    console.print("Matched rules: " + ", ".join(decision.matched_rules))
    console.print("\n[bold]Provider[/]")
    consultation = evaluation.provider_consultation
    console.print(f"Consulted:  {'yes' if consultation.attempted else 'no'}")
    if consultation.succeeded and consultation.classification:
        classification = consultation.classification
        console.print("Status:     SUCCESS")
        console.print(f"Suggested:  {classification.suggested_route.value.upper()}")
        console.print(f"Risk:       {classification.risk:.2f}")
        console.print(f"Confidence: {classification.confidence:.2f}")
    elif consultation.attempted:
        console.print("Status:     FAILED")
        console.print(f"Error:      {(consultation.error_kind or 'unknown_error').upper()}")
        console.print("Provider classification failed safely.")
    else:
        console.print("Status:     SKIPPED")
        skip_reason = consultation.skip_reason
        reason = skip_reason.value.upper() if skip_reason else "PROVIDER_UNAVAILABLE"
        console.print(f"Reason:     {reason}")
    console.print("\n[bold]Fusion[/]")
    console.print(
        "Reason:     " + ", ".join(reason.replace("_", " ") for reason in evaluation.fusion_reasons)
    )
    console.print(f"Influenced: {'yes' if evaluation.provider_influenced_decision else 'no'}")
    console.print(
        "Sources:    " + ", ".join(source.value.upper() for source in evaluation.decision_sources)
    )
    console.print("\n[bold]Final[/]")
    console.print(f"Route:      {evaluation.final_decision.route.value.upper()}")
    console.print(f"Risk:       {evaluation.final_risk:.2f}")


def _execution_unavailable_message(route: Route) -> str:
    messages = {
        Route.GEMINI: "Gemini coding execution is not implemented yet.",
        Route.GEMINI_TO_CODEX: "Gemini-to-Codex execution is not implemented yet.",
        Route.CODEX: "Codex execution is not implemented yet.",
        Route.PLAN: "Planning execution is not implemented yet.",
    }
    return messages[route]


def _print_execution_unavailable(route: Route) -> None:
    console.print("\n[bold]Execution[/]")
    console.print(_execution_unavailable_message(route))


def _print_plan(plan) -> None:
    console.print("\n[bold]L0 Execution Plan[/]")
    console.print(f"Operation: {plan.operation.upper()}")
    console.print(f"Tool: {plan.tool}")
    console.print("Targets:")
    for target in plan.targets:
        console.print(f"- {target}")
    console.print("Execute: " + " ".join(plan.commands[0].args))
    console.print("Verify: " + " ".join(plan.verification_commands[0].args))
    console.print("Safety: SAFE")


def _print_execution(result: ExecutionResult) -> None:
    console.print("\n[bold]Execution[/]")
    if result.status == ExecutionStatus.SUCCEEDED:
        console.print("OK " + " ".join(result.plan.commands[0].args))
        console.print("\n[bold]Verification[/]")
        console.print("OK " + " ".join(result.plan.verification_commands[0].args))
        console.print("\n[bold]Changes[/]")
        console.print(f"Files changed: {len(result.changes)}")
        console.print("\n[bold green]Result: L0 VERIFIED SUCCESS[/]")
    else:
        console.print(f"FAIL {result.message}")
        if result.rollback_attempted:
            console.print("\n[bold]Rollback[/]")
            console.print("OK" if result.rollback_succeeded else "FAIL")
        console.print("\n[bold red]Result: L0 FAILED - WORKSPACE RESTORED[/]")


@app.command()
def analyze(
    description: Annotated[str, typer.Argument(help="Task to analyze without execution.")],
    mode: Annotated[str, typer.Option(help="Routing mode.")] = UserMode.AUTO.value,
    as_json: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Analyze a task without executing workspace operations or coding agents."""
    selected_mode = _parse_analyze_mode(mode)
    try:
        request = TaskRequest(description=description)
    except ValidationError as error:
        console.print(f"[red]Error:[/] Invalid task: {error.errors()[0]['msg']}")
        raise typer.Exit(code=2) from error
    repository = _scan_or_exit()
    _, config_path, _ = _context_paths(repository.root)
    config = _load_config(config_path) if config_path.exists() else CarConfig()
    _, evaluation = evaluate_analysis(request, repository, selected_mode, config)
    if as_json:
        console.print(evaluation.model_dump_json(indent=2))
        return
    _title()
    _print_evaluation(request, evaluation)
    if evaluation.final_decision.route.value == "l0":
        try:
            plan = resolve_l0_plan(request, repository, config.l0)
            console.print(f"\nPotential L0 operation: {plan.operation}")
            console.print("Candidate target: " + plan.targets[0])
        except L0ResolutionError as error:
            console.print(f"\nL0 execution unavailable: {error}")
    console.print("\n[bold]Execution[/]")
    console.print("Analysis only. Nothing executed.")


def _selected_coding_files(root: Path, values: list[str]) -> list[CodingFileContext]:
    """Read only explicit, existing, regular, repository-scoped text files."""
    policy = PatchValidationPolicy()
    selected: list[CodingFileContext] = []
    for value in values:
        try:
            relative = normalize_repository_relative_path(value)
        except ValueError as error:
            raise typer.BadParameter(f"Unsafe selected file: {value}") from error
        parts = relative.split("/")
        if parts[0] in policy.protected_prefixes or any(
            part == ".env" or part.startswith(".env.") for part in parts
        ):
            raise typer.BadParameter(f"Protected selected file: {relative}")
        target = root / relative
        try:
            target.resolve(strict=True).relative_to(root.resolve())
        except (OSError, ValueError) as error:
            raise typer.BadParameter(f"Selected file leaves repository: {relative}") from error
        if target.is_symlink() or not target.is_file():
            raise typer.BadParameter(f"Selected file must be a regular file: {relative}")
        try:
            selected.append(
                CodingFileContext(path=relative, content=target.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeDecodeError) as error:
            raise typer.BadParameter(
                f"Selected file is not supported UTF-8 text: {relative}"
            ) from error
    return selected


def _verification_plan(
    root: Path, checks: list[str], files: list[CodingFileContext]
) -> VerificationPlan:
    commands = []
    for check in checks:
        if check == "ruff":
            commands.append(
                CommandSpec(
                    args=["ruff", "check", *[item.path for item in files]],
                    cwd=str(root),
                    timeout_seconds=60,
                )
            )
        elif check == "pytest":
            commands.append(
                CommandSpec(args=["python", "-m", "pytest"], cwd=str(root), timeout_seconds=60)
            )
        else:
            raise typer.BadParameter("Verification checks must be one of: ruff, pytest.")
    return VerificationPlan(commands=commands)


def _print_execute_preview(
    request: TaskRequest,
    repository: RepositoryState,
    evaluation: RoutingEvaluation,
    files: list[CodingFileContext],
    checks: list[str],
    codex_analysis: bool,
) -> None:
    _title()
    console.print("[bold]Coding execution preview[/]")
    console.print(f"Task:       {request.description}")
    console.print(f"Repository: {repository.name}")
    console.print(f"Route:      {evaluation.final_decision.route.value.upper()}")
    console.print(f"Risk:       {evaluation.final_risk:.2f}")
    console.print(f"Complexity: {evaluation.final_decision.complexity.value.upper()}")
    console.print("Selected files:")
    for file in files:
        console.print(f"- {file.path}")
    console.print("Verification checks: " + (", ".join(checks) if checks else "none"))
    console.print("Gemini coding: enabled only after explicit authorization")
    console.print(f"Codex fallback analysis: {'enabled' if codex_analysis else 'disabled'}")
    console.print("Codex workspace mode: READ-ONLY")
    console.print("Files may be modified if Gemini produces a patch that passes CAR validation.")


def _print_coding_flow_result(result) -> None:
    presentation = present_execution_result(result)
    console.print("\n[bold]CAR Execution Result[/]")
    console.print(f"Route: {presentation.route}")
    console.print(f"Coding: {presentation.coding}")
    if presentation.resolved_by:
        console.print(f"Resolved by: {presentation.resolved_by}")
    if presentation.temporary_changes:
        console.print(f"Files changed temporarily: {presentation.files_changed}")
    elif presentation.files_changed:
        console.print(f"Files changed: {presentation.files_changed}")
    console.print(f"Verification: {presentation.verification}")
    for check in presentation.verification_checks:
        console.print(f"  {check}")
    console.print(f"Rollback: {presentation.rollback}")
    console.print(f"Codex analysis: {presentation.codex_analysis}")
    console.print(f"Workspace: {presentation.workspace}")
    if presentation.failure_reason:
        console.print(f"Reason: {presentation.failure_reason}")
    style = "green" if presentation.task == "RESOLVED" else "red"
    console.print(f"Task: [{style}]{presentation.task}[/]")


@app.command()
def execute(
    description: Annotated[
        str, typer.Argument(help="Coding task; selected files may be modified.")
    ],
    files: Annotated[
        list[str] | None,
        typer.Option("--file", help="Existing repository-relative file to authorize."),
    ] = None,
    verify: Annotated[
        list[str] | None, typer.Option("--verify", help="CAR-controlled check: ruff or pytest.")
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", help="Authorize this invocation after preview.")
    ] = False,
    codex_analysis: Annotated[
        bool,
        typer.Option("--codex-analysis", help="Enable optional read-only Codex fallback analysis."),
    ] = False,
    allow_codex_write: Annotated[
        bool,
        typer.Option(
            "--allow-codex-write",
            help="Allow verified Codex changes for this invocation only within explicit paths.",
        ),
    ] = False,
    codex_write_paths: Annotated[
        list[str] | None,
        typer.Option(
            "--codex-write-path", help="Repository-relative file Codex may modify; repeat."
        ),
    ] = None,
) -> None:
    """Preview a scoped coding execution; authorization and verification are mandatory."""
    try:
        request = TaskRequest(description=description)
    except ValidationError as error:
        console.print(f"[red]Error:[/] Invalid task: {error.errors()[0]['msg']}")
        raise typer.Exit(code=2) from error
    repository = _scan_or_exit()
    _, config_path, _ = _context_paths(repository.root)
    config = _load_config(config_path) if config_path.exists() else CarConfig()
    mode = config.default_mode
    evaluation = evaluate_routing(request, repository, mode, provider=None)
    route = evaluation.final_decision.route
    if route == Route.L0:
        _title()
        console.print("L0 execution remains available through the existing `car task` path.")
        return
    if route == Route.CODEX:
        _title()
        console.print("Direct Codex coding execution is not implemented yet.")
        return
    if route == Route.PLAN:
        _title()
        console.print("Planning route selected; no repository mutation will be performed.")
        return
    selected_arguments = files or []
    write_arguments = codex_write_paths or []
    if allow_codex_write and not write_arguments:
        console.print("Controlled Codex write requires at least one --codex-write-path.")
        raise typer.Exit(code=2)
    if write_arguments and not allow_codex_write:
        console.print("--codex-write-path requires --allow-codex-write.")
        raise typer.Exit(code=2)
    verification_checks = verify or []
    if not selected_arguments:
        console.print(
            "No files selected for coding execution. Use --file PATH to authorize the coding scope."
        )
        raise typer.Exit(code=2)
    try:
        selected = _selected_coding_files(repository.root, selected_arguments)
        write_selected = (
            _selected_coding_files(repository.root, write_arguments) if write_arguments else []
        )
    except typer.BadParameter as error:
        console.print(f"[red]Error:[/] {error}")
        raise typer.Exit(code=2) from error
    if not verification_checks:
        console.print("At least one CAR-controlled verification check is required.")
        raise typer.Exit(code=2)
    try:
        plan = _verification_plan(repository.root, verification_checks, selected)
    except typer.BadParameter as error:
        console.print(f"[red]Error:[/] {error}")
        raise typer.Exit(code=2) from error
    _print_execute_preview(
        request, repository, evaluation, selected, verification_checks, codex_analysis
    )
    if allow_codex_write:
        console.print("\nControlled Codex write: ENABLED FOR THIS RUN")
        console.print("Authorized paths:")
        for item in write_selected:
            console.print(f"- {item.path}")
        console.print("CAR validates and verifies isolated Codex changes before acceptance.")
    authorized = yes
    if not yes:
        try:
            authorized = typer.confirm("Proceed with repository modifications?", default=False)
        except (EOFError, typer.Abort):
            authorized = False
    if not authorized:
        console.print("\nExecution cancelled. No repository changes were made.")
        return
    context = CodingTaskContext(
        task=request.description,
        route=route,
        repository=RepositoryClassificationContext(
            name=repository.name,
            branch=repository.git.branch,
            dirty=repository.git.dirty,
            languages=repository.languages.counts,
            systems=repository.project_signals.systems,
        ),
        files=selected,
    )
    gateway = _build_coding_flow_gateway(
        _build_coding_provider(config),
        _build_codex_runtime() if codex_analysis else _UnavailableCodexRuntime(),
    )
    result = gateway.execute(
        CodingFlowExecutionRequest(
            repository_root=repository.root,
            routing_evaluation=evaluation,
            repository_state=repository,
            coding_context=context,
            coding_policy=None,
            patch_validation_policy=None,
            verification_plan=plan,
            coding_execution_policy=CodingPipelineExecutionPolicy(enabled=True),
            handoff_policy=HandoffPolicy(),
            codex_execution_policy=CodexExecutionPolicy(enabled=codex_analysis),
            codex_write_policy=config.codex_write,
            codex_write_authorization=CodexWriteAuthorization(authorized=allow_codex_write),
            codex_write_paths=tuple(item.path for item in write_selected),
        ),
        CodingFlowAuthorization(authorized=True),
    )
    _print_coding_flow_result(result)
    if not result.succeeded:
        raise typer.Exit(code=1)


def _build_coding_flow_gateway(coding_provider, codex_runtime) -> CodingFlowGateway:
    """Compose the production gateway; tests may replace this narrow construction seam."""
    return CodingFlowGateway(coding_provider, codex_runtime)


class _UnavailableCodexRuntime:
    """Never touched while Codex analysis is disabled; avoids constructing a runtime."""

    def health(self):
        raise AssertionError("Codex runtime must not be used when analysis is disabled")

    def execute(self, request):
        raise AssertionError("Codex runtime must not be used when analysis is disabled")


@app.command()
def task(
    description: Annotated[
        str, typer.Argument(help="Task to route and execute only if eligible for L0.")
    ],
    mode: Annotated[UserMode, typer.Option(help="Routing mode.")] = UserMode.AUTO,
    dry_run: Annotated[bool, typer.Option(help="Build an L0 plan without executing it.")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Route a task, executing only an eligible deterministic L0 plan."""
    try:
        request = TaskRequest(description=description)
    except ValidationError as error:
        console.print(f"[red]Error:[/] Invalid task: {error.errors()[0]['msg']}")
        raise typer.Exit(code=2) from error
    repository = _scan_or_exit()
    _, config_path, _ = _context_paths(repository.root)
    config = _load_config(config_path) if config_path.exists() else CarConfig()
    _, evaluation = evaluate_analysis(request, repository, mode, config)
    decision = evaluation.final_decision
    if decision.route != Route.L0:
        if as_json:
            console.print(
                json.dumps(
                    {
                        "routing": evaluation.model_dump(mode="json"),
                        "execution": {
                            "implemented": False,
                            "message": _execution_unavailable_message(decision.route),
                        },
                    },
                    indent=2,
                )
            )
            return
        _title()
        _print_evaluation(request, evaluation)
        _print_execution_unavailable(decision.route)
        return
    try:
        plan = resolve_l0_plan(request, repository, config.l0)
    except L0ResolutionError as error:
        if as_json:
            console.print(
                json.dumps(
                    {
                        "routing": evaluation.model_dump(mode="json"),
                        "execution": {"error": str(error)},
                    },
                    indent=2,
                )
            )
        else:
            _title()
            _print_evaluation(request, evaluation)
            label = (
                "L0 TOOL UNAVAILABLE"
                if "ruff is not available" in str(error)
                else "L0 EXECUTION UNAVAILABLE"
            )
            console.print(f"\n[red]Result: {label} - {error}[/]")
        raise typer.Exit(code=1) from error
    if dry_run:
        if as_json:
            console.print(
                json.dumps(
                    {
                        "routing": evaluation.model_dump(mode="json"),
                        "execution": {
                            "implemented": True,
                            "dry_run": True,
                            "plan": plan.model_dump(mode="json"),
                        },
                    },
                    indent=2,
                )
            )
            return
        _title()
        _print_evaluation(request, evaluation)
        _print_plan(plan)
        console.print("\nDry run: Nothing executed.")
        return
    result = L0Executor().execute(plan)
    if as_json:
        console.print(
            json.dumps(
                {
                    "routing": evaluation.model_dump(mode="json"),
                    "execution": {"implemented": True, "result": result.model_dump(mode="json")},
                },
                indent=2,
            )
        )
        raise typer.Exit(code=0 if result.status == ExecutionStatus.SUCCEEDED else 1)
    _title()
    _print_evaluation(request, evaluation)
    _print_plan(plan)
    _print_execution(result)
    if result.status != ExecutionStatus.SUCCEEDED:
        raise typer.Exit(code=1)


@app.command()
def providers(
    as_json: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Show local provider configuration and health without network access."""
    repository = _scan_or_exit()
    _, config_path, _ = _context_paths(repository.root)
    config = _load_config(config_path) if config_path.exists() else CarConfig()
    gemini_config = config.providers.gemini
    health = build_gemini_provider(config).health()
    credentials_present = bool(os.environ.get(gemini_config.api_key_env))
    report = {
        "gemini": {
            "enabled": gemini_config.enabled,
            "model": gemini_config.model,
            "credential_env": gemini_config.api_key_env,
            "credentials_present": credentials_present,
            "local_status": health.status.value,
        },
        "codex": {"execution": "not_implemented", "authentication": "external_runtime"},
    }
    if as_json:
        console.print(json.dumps(report, indent=2))
        return
    _title()
    console.print("[bold]CAR Providers[/]")
    console.print("\n[bold]Gemini[/]")
    console.print(f"Enabled:        {'yes' if gemini_config.enabled else 'no'}")
    console.print(f"Model:          {gemini_config.model or 'not configured'}")
    console.print(f"Credentials:    {'configured' if credentials_present else 'missing'}")
    console.print(f"Local status:   {health.status.value.upper()}")
    console.print("Live checked:   no")
    if health.status.value == "disabled":
        console.print("Gemini provider is disabled.")
    elif health.status.value == "not_configured":
        console.print("Gemini model is not configured.")
    elif health.status.value == "missing_credentials":
        console.print(f"Credential environment variable {gemini_config.api_key_env} is not set.")
    console.print("\n[bold]Codex[/]")
    console.print("Execution:      not implemented")
    console.print("Authentication: external/local Codex runtime")


def _parse_benchmark_strategies(
    strategy: str | None, all_strategies: bool
) -> tuple[BenchmarkStrategy, ...]:
    if strategy and all_strategies:
        raise typer.BadParameter("Use either --strategy or --all, not both.")
    if all_strategies:
        return tuple(BenchmarkStrategy)
    if strategy is None:
        raise typer.BadParameter("Select a strategy with --strategy or explicitly use --all.")
    try:
        return (BenchmarkStrategy(strategy.replace("-", "_")),)
    except ValueError as error:
        valid = ", ".join(value.value.replace("_", "-") for value in BenchmarkStrategy)
        raise typer.BadParameter(f"Strategy must be one of: {valid}.") from error


@app.command(name="benchmark")
def benchmark(
    manifest_path: Annotated[Path, typer.Argument(help="Local benchmark manifest JSON file.")],
    strategy: Annotated[
        str | None, typer.Option("--strategy", help="Run gemini-only, codex-only, or car.")
    ] = None,
    all_strategies: Annotated[
        bool, typer.Option("--all", help="Run all three benchmark strategies.")
    ] = False,
    json_out: Annotated[
        Path | None, typer.Option("--json-out", help="Write privacy-safe benchmark JSON.")
    ] = None,
    codex_model: Annotated[
        str | None, typer.Option("--codex-model", help="Pin the Codex model for this benchmark.")
    ] = None,
) -> None:
    """Run selected live benchmark strategies over isolated local fixtures."""
    try:
        manifest = load_manifest(manifest_path)
        strategies = _parse_benchmark_strategies(strategy, all_strategies)
    except (ValueError, typer.BadParameter) as error:
        console.print(f"[red]Error:[/] {error}")
        raise typer.Exit(code=2) from error
    repository = _scan_or_exit()
    _, config_path, _ = _context_paths(repository.root)
    config = _load_config(config_path) if config_path.exists() else CarConfig()
    _title()
    console.print(
        "Selected benchmark strategies may invoke configured Gemini and local Codex providers."
    )
    try:
        report = run_manifest_benchmark(
            manifest,
            manifest_path.resolve(),
            strategies,
            BenchmarkRunner(_build_benchmark_executor(config, codex_model=codex_model)),
            gemini_model=config.providers.gemini.model,
            codex_model=codex_model,
        )
        if json_out:
            json_out.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError) as error:
        console.print(f"[red]Error:[/] Benchmark failed: {error}")
        raise typer.Exit(code=1) from error
    render_benchmark_report(report, console)


def main() -> None:
    """Run the CLI entry point."""
    app()
