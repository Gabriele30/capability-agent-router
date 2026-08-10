"""Cross-platform structured command runner; it never uses a shell."""

from __future__ import annotations

import os
import subprocess
import time

from car.execution.models import CommandResult, CommandSpec


class CommandRunner:
    def run(self, command: CommandSpec) -> CommandResult:
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command.args,
                cwd=command.cwd,
                capture_output=True,
                check=False,
                shell=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=command.timeout_seconds,
                env=_child_environment(command),
            )
            return CommandResult(
                command=command,
                exit_code=completed.returncode,
                stdout=completed.stdout[-10_000:],
                stderr=completed.stderr[-10_000:],
                duration_seconds=round(time.monotonic() - started, 3),
            )
        except FileNotFoundError:
            return CommandResult(
                command=command,
                executable_not_found=True,
                duration_seconds=round(time.monotonic() - started, 3),
            )
        except subprocess.TimeoutExpired as error:
            return CommandResult(
                command=command,
                stdout=(error.stdout or "")[-10_000:],
                stderr=(error.stderr or "")[-10_000:],
                timed_out=True,
                duration_seconds=round(time.monotonic() - started, 3),
            )


def _child_environment(command: CommandSpec) -> dict[str, str] | None:
    """Prevent pytest verification artifacts without mutating the CAR process environment."""
    if command.args not in (["python", "-m", "pytest"], ["pytest"]):
        return None
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    options = environment.get("PYTEST_ADDOPTS", "")
    if "no:cacheprovider" not in options:
        environment["PYTEST_ADDOPTS"] = " ".join(
            item for item in (options, "-p no:cacheprovider") if item
        )
    return environment
