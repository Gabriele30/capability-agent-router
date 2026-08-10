"""Execute structured verification commands after a write operation."""

from car.execution.runner import CommandRunner
from car.verification.models import VerificationPlan, VerificationResult, VerificationStatus


class VerificationEngine:
    def __init__(self, runner: CommandRunner | None = None) -> None:
        self.runner = runner or CommandRunner()

    def verify(
        self, plan: VerificationPlan, *, stop_on_failure: bool = False
    ) -> VerificationResult:
        checks = []
        for command in plan.commands:
            check = self.runner.run(command)
            checks.append(check)
            if stop_on_failure and check.exit_code != 0:
                break
        if checks and all(check.exit_code == 0 for check in checks):
            return VerificationResult(
                status=VerificationStatus.PASSED, checks=checks, message="verified"
            )
        return VerificationResult(
            status=VerificationStatus.FAILED, checks=checks, message="verification failed"
        )
