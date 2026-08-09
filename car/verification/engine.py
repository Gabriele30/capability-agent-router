"""Execute structured verification commands after a write operation."""

from car.execution.runner import CommandRunner
from car.verification.models import VerificationPlan, VerificationResult, VerificationStatus


class VerificationEngine:
    def __init__(self, runner: CommandRunner | None = None) -> None:
        self.runner = runner or CommandRunner()

    def verify(self, plan: VerificationPlan) -> VerificationResult:
        checks = [self.runner.run(command) for command in plan.commands]
        if all(check.exit_code == 0 for check in checks):
            return VerificationResult(
                status=VerificationStatus.PASSED, checks=checks, message="verified"
            )
        return VerificationResult(
            status=VerificationStatus.FAILED, checks=checks, message="verification failed"
        )
