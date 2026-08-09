"""Execute a validated L0 plan with snapshots, verification, and rollback."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from car.execution.models import ExecutionPlan, ExecutionResult, ExecutionStatus
from car.execution.runner import CommandRunner
from car.execution.safety import SafetyLevel, classify_plan
from car.rollback.snapshot import WorkspaceSnapshot
from car.verification.engine import VerificationEngine
from car.verification.models import VerificationPlan, VerificationStatus


def _within_scope(path: str, scope: list[str]) -> bool:
    return any(
        path == allowed or PurePosixPath(allowed) in PurePosixPath(path).parents
        for allowed in scope
    )


class L0Executor:
    def __init__(
        self, runner: CommandRunner | None = None, verifier: VerificationEngine | None = None
    ) -> None:
        self.runner = runner or CommandRunner()
        self.verifier = verifier or VerificationEngine(self.runner)

    def execute(self, plan: ExecutionPlan) -> ExecutionResult:
        if classify_plan(plan) != SafetyLevel.SAFE:
            return ExecutionResult(
                status=ExecutionStatus.BLOCKED, plan=plan, message="safety blocked execution"
            )
        snapshot = WorkspaceSnapshot.capture(Path(plan.commands[0].cwd))
        results = [self.runner.run(command) for command in plan.commands]
        if any(result.exit_code != 0 for result in results):
            message = (
                "tool unavailable during execution"
                if any(result.executable_not_found for result in results)
                else "command failed"
            )
            return self._rollback(plan, snapshot, results, None, message)
        changes = snapshot.changes()
        if any(not _within_scope(change.path, plan.expected_write_scope) for change in changes):
            return self._rollback(plan, snapshot, results, None, "scope violation")
        verification = self.verifier.verify(VerificationPlan(commands=plan.verification_commands))
        if verification.status != VerificationStatus.PASSED:
            return self._rollback(plan, snapshot, results, verification, "verification failed")
        return ExecutionResult(
            status=ExecutionStatus.SUCCEEDED,
            plan=plan,
            command_results=results,
            verification=verification,
            changes=changes,
            message="L0 verified success",
        )

    @staticmethod
    def _rollback(
        plan: ExecutionPlan,
        snapshot: WorkspaceSnapshot,
        results: list,
        verification: object,
        message: str,
    ) -> ExecutionResult:
        try:
            changes = snapshot.changes()
            snapshot.restore()
            return ExecutionResult(
                status=ExecutionStatus.ROLLED_BACK,
                plan=plan,
                command_results=results,
                verification=verification,
                changes=changes,
                rollback_attempted=True,
                rollback_succeeded=True,
                message=message,
            )
        except OSError as error:
            return ExecutionResult(
                status=ExecutionStatus.ROLLBACK_FAILED,
                plan=plan,
                command_results=results,
                verification=verification,
                rollback_attempted=True,
                rollback_succeeded=False,
                message=f"{message}; rollback failed: {error}",
            )
