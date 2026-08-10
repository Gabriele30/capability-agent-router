"""Verification-gated finalization for CAR-applied coding patch transactions."""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from car.execution.models import CommandResult, CommandSpec
from car.patching.apply import PatchApplyTransaction
from car.patching.models import PatchTransactionState
from car.verification.engine import VerificationEngine
from car.verification.models import VerificationPlan, VerificationStatus


class CodingVerificationFailureKind(StrEnum):
    INVALID_TRANSACTION_STATE = "invalid_transaction_state"
    EMPTY_PLAN = "empty_plan"
    UNSAFE_COMMAND = "unsafe_command"
    CHECK_FAILED = "check_failed"
    CHECK_TIMEOUT = "check_timeout"
    CHECK_EXECUTION_ERROR = "check_execution_error"
    VERIFICATION_INTERNAL_ERROR = "verification_internal_error"
    ROLLBACK_FAILED = "rollback_failed"
    FINALIZE_FAILED = "finalize_failed"


class CodingVerificationResult(BaseModel):
    attempted: bool
    passed: bool
    checks_passed: bool = False
    checks: list[CommandResult] = Field(default_factory=list)
    finalized: bool = False
    rolled_back: bool = False
    failure_kind: CodingVerificationFailureKind | None = None
    rollback_failure: CodingVerificationFailureKind | None = None
    message: str


class CodingVerificationCoordinator:
    """Run only CAR-selected checks, then finalize or rollback an applied transaction."""

    def __init__(self, engine: VerificationEngine | None = None) -> None:
        self._engine = engine or VerificationEngine()

    def verify(
        self,
        repository_root: Path,
        transaction: PatchApplyTransaction,
        plan: VerificationPlan,
    ) -> CodingVerificationResult:
        if not isinstance(transaction, PatchApplyTransaction):
            raise TypeError("CodingVerificationCoordinator requires a PatchApplyTransaction")
        root = repository_root.resolve()
        if transaction.state != PatchTransactionState.APPLIED or transaction.root != root:
            return CodingVerificationResult(
                attempted=False,
                passed=False,
                failure_kind=CodingVerificationFailureKind.INVALID_TRANSACTION_STATE,
                message="verification requires an applied transaction for this repository",
            )
        frozen_plan = plan.model_copy(deep=True)
        if not frozen_plan.commands:
            return self._rollback(
                transaction,
                CodingVerificationFailureKind.EMPTY_PLAN,
                [],
                "verification plan is empty",
            )
        if not all(self._is_safe_command(command, root) for command in frozen_plan.commands):
            return self._rollback(
                transaction,
                CodingVerificationFailureKind.UNSAFE_COMMAND,
                [],
                "verification command is not CAR-allowed",
            )
        try:
            verification = self._engine.verify(frozen_plan, stop_on_failure=True)
        except Exception:
            return self._rollback(
                transaction,
                CodingVerificationFailureKind.VERIFICATION_INTERNAL_ERROR,
                [],
                "verification engine failed",
            )
        if verification.status != VerificationStatus.PASSED:
            return self._rollback(
                transaction,
                self._failure_kind(verification.checks),
                verification.checks,
                "verification failed",
            )
        try:
            transaction.finalize()
        except Exception:
            return CodingVerificationResult(
                attempted=True,
                passed=False,
                checks_passed=True,
                checks=verification.checks,
                failure_kind=CodingVerificationFailureKind.FINALIZE_FAILED,
                message="checks passed but transaction finalization failed",
            )
        return CodingVerificationResult(
            attempted=True,
            passed=True,
            checks_passed=True,
            checks=verification.checks,
            finalized=True,
            message="verification passed; transaction finalized",
        )

    @staticmethod
    def _is_safe_command(command: CommandSpec, root: Path) -> bool:
        try:
            if Path(command.cwd).resolve() != root:
                return False
        except OSError:
            return False
        executable, *arguments = command.args
        if executable == "ruff":
            return (
                arguments[:1] == ["check"]
                and "--fix" not in arguments
                or arguments[:2]
                == [
                    "format",
                    "--check",
                ]
            )
        if executable == "pytest":
            return True
        return command.args[:3] == ["python", "-m", "pytest"]

    @staticmethod
    def _failure_kind(checks: list[CommandResult]) -> CodingVerificationFailureKind:
        check = checks[-1]
        if check.timed_out:
            return CodingVerificationFailureKind.CHECK_TIMEOUT
        if check.executable_not_found:
            return CodingVerificationFailureKind.CHECK_EXECUTION_ERROR
        return CodingVerificationFailureKind.CHECK_FAILED

    @staticmethod
    def _rollback(
        transaction: PatchApplyTransaction,
        kind: CodingVerificationFailureKind,
        checks: list[CommandResult],
        message: str,
    ) -> CodingVerificationResult:
        rolled_back = transaction.rollback()
        return CodingVerificationResult(
            attempted=bool(checks),
            passed=False,
            checks=checks,
            rolled_back=rolled_back,
            failure_kind=kind,
            rollback_failure=(
                None if rolled_back else CodingVerificationFailureKind.ROLLBACK_FAILED
            ),
            message=message,
        )
