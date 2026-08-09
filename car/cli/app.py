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
from car.config.models import CarConfig
from car.repository.scanner import RepositoryScanError, scan_repository
from car.router.models import TaskRequest

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


def _scan_or_exit() -> object:
    try:
        return scan_repository()
    except RepositoryScanError as error:
        console.print(f"[red]Error:[/] {error}")
        raise typer.Exit(code=1) from error


def _title() -> None:
    console.print("[bold]CAR — Capability Agent Router[/]\n")


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


def _print_status(repository: object) -> None:
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


@app.command()
def task(
    description: Annotated[str, typer.Argument(help="Task to acquire for future routing.")],
) -> None:
    """Validate and acquire a task without invoking an AI provider."""
    try:
        request = TaskRequest(description=description)
    except ValidationError as error:
        console.print(f"[red]Error:[/] Invalid task: {error.errors()[0]['msg']}")
        raise typer.Exit(code=2) from error

    repository = _scan_or_exit()
    _title()
    console.print("[bold]Task[/]")
    console.print(request.description)
    console.print("\n[bold]Repository[/]")
    console.print(repository.name)
    console.print("\n[bold]Repository analysis[/]")
    console.print("[green]OK[/] Git state collected")
    console.print("[green]OK[/] Languages detected")
    console.print("[green]OK[/] Project signals detected")
    console.print("\n[bold]Routing[/]")
    console.print("[yellow]NOT IMPLEMENTED — milestone 2+[/]")
    console.print("\n[bold green]Task accepted successfully.[/]")


def main() -> None:
    """Run the CLI entry point."""
    app()
