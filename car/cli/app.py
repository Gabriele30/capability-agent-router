"""Typer commands and Rich presentation for CAR."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from car import __version__
from car.application.routing import evaluate_analysis
from car.config.models import CarConfig
from car.execution.models import ExecutionResult, ExecutionStatus
from car.l0.executor import L0Executor
from car.l0.resolver import L0ResolutionError, resolve_l0_plan
from car.repository.models import RepositoryState
from car.repository.scanner import RepositoryScanError, scan_repository
from car.router.consultation import RoutingEvaluation
from car.router.engine import DecisionEngine
from car.router.models import RoutingDecision, TaskRequest, UserMode

app = typer.Typer(add_completion=False, help="Capability-aware software engineering task routing.")
console = Console()
LOGGER = logging.getLogger(__name__)


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


@app.command()
def task(
    description: Annotated[str, typer.Argument(help="Task to acquire for future routing.")],
    mode: Annotated[UserMode, typer.Option(help="Routing mode.")] = UserMode.AUTO,
    dry_run: Annotated[bool, typer.Option(help="Build an L0 plan without executing it.")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Acquire a task, decide its route, and stop before provider execution."""
    request, repository, decision = _routing_inputs(description, mode)
    if decision.route.value != "l0":
        if as_json:
            console.print(
                json.dumps({"decision": decision.model_dump(mode="json"), "execution": None})
            )
            return
        _title()
        _print_decision(request, mode, decision)
        console.print("\n[yellow]Execution not implemented for this route.[/]")
        return
    _, config_path, _ = _context_paths(repository.root)
    config = _load_config(config_path) if config_path.exists() else CarConfig()
    try:
        plan = resolve_l0_plan(request, repository, config.l0)
    except L0ResolutionError as error:
        if as_json:
            console.print(
                json.dumps({"decision": decision.model_dump(mode="json"), "error": str(error)})
            )
        else:
            _title()
            _print_decision(request, mode, decision)
            label = (
                "L0 TOOL UNAVAILABLE"
                if "ruff is not available" in str(error)
                else "L0 EXECUTION UNAVAILABLE"
            )
            console.print(f"\n[red]Result: {label} - {error}[/]")
        raise typer.Exit(code=1) from error
    if dry_run:
        if as_json:
            console.print(plan.model_dump_json(indent=2))
            return
        _title()
        _print_plan(plan)
        console.print("\nDry run: Nothing executed.")
        return
    result = L0Executor().execute(plan)
    if as_json:
        console.print(result.model_dump_json(indent=2))
        raise typer.Exit(code=0 if result.status == ExecutionStatus.SUCCEEDED else 1)
    _title()
    _print_decision(request, mode, decision)
    _print_plan(plan)
    _print_execution(result)
    if result.status != ExecutionStatus.SUCCEEDED:
        raise typer.Exit(code=1)


def main() -> None:
    """Run the CLI entry point."""
    app()
