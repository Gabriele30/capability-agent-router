"""Cross-platform structured command runner; it never uses a shell."""

from __future__ import annotations

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
