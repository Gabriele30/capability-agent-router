"""Verification-gated finalization for pending Codex source transactions."""

from pathlib import Path

from car.coding.verification import CodingVerificationCoordinator
from car.execution.models import CommandResult
from car.verification.engine import VerificationEngine
from car.verification.models import VerificationPlan, VerificationResult, VerificationStatus

from .application import AppliedCodexSourceTransaction
from .models import (
    CodexSourceState,
    CodexSourceTransactionState,
    CodexSourceVerificationResult,
    CodexWriteFailureKind,
    CodexWritePolicy,
)


class CodexSourceVerificationCoordinator:
    """Verify a B1 transaction on source, then finalize or safely roll it back."""

    def __init__(self, engine: VerificationEngine | None = None) -> None:
        self._engine = engine or VerificationEngine()

    def verify_and_finalize(
        self,
        transaction: AppliedCodexSourceTransaction,
        verification_plan: VerificationPlan,
        source_repository: Path,
        policy: CodexWritePolicy,
    ) -> CodexSourceVerificationResult:
        root = source_repository.resolve()
        if not isinstance(transaction, AppliedCodexSourceTransaction):
            raise TypeError("CodexSourceVerificationCoordinator requires a B1 transaction")
        if (
            transaction.root != root
            or transaction.state != CodexSourceTransactionState.APPLIED_PENDING_VERIFICATION
        ):
            return self._blocked(CodexWriteFailureKind.PRE_VERIFICATION_INTEGRITY_FAILED)
        if not verification_plan.commands:
            return self._rollback(transaction, CodexWriteFailureKind.VERIFICATION_REQUIRED, None)
        frozen_plan = verification_plan.model_copy(deep=True)
        if not all(
            CodingVerificationCoordinator._is_safe_command(item, root)
            for item in frozen_plan.commands
        ):
            return self._rollback(transaction, CodexWriteFailureKind.VERIFICATION_REQUIRED, None)
        if not transaction.applied_identities_match() or not transaction.source_integrity_matches(
            policy
        ):
            return self._rollback(
                transaction, CodexWriteFailureKind.PRE_VERIFICATION_INTEGRITY_FAILED, None
            )
        try:
            verification = self._engine.verify(frozen_plan, stop_on_failure=True)
        except Exception:
            return self._rollback(transaction, CodexWriteFailureKind.VERIFICATION_FAILED, None)
        if verification.status != VerificationStatus.PASSED:
            return self._rollback(
                transaction, self._failure_kind(verification.checks), verification
            )
        if not transaction.applied_identities_match() or not transaction.source_integrity_matches(
            policy
        ):
            return self._rollback(
                transaction, CodexWriteFailureKind.POST_VERIFICATION_INTEGRITY_FAILED, verification
            )
        changed_paths = transaction.changed_paths
        try:
            transaction.finalize()
        except Exception:
            return self._rollback(
                transaction, CodexWriteFailureKind.FINALIZATION_FAILED, verification
            )
        if transaction.state != CodexSourceTransactionState.FINALIZED:
            return self._rollback(
                transaction, CodexWriteFailureKind.FINALIZATION_FAILED, verification
            )
        return CodexSourceVerificationResult(
            attempted=True,
            verification_passed=True,
            post_verification_integrity_valid=True,
            finalized=True,
            accepted=True,
            source_state=CodexSourceState.UPDATED_AND_ACCEPTED,
            verification_result=verification,
            changed_paths=changed_paths,
            message="verification passed; source transaction finalized",
        )

    @staticmethod
    def _blocked(kind: CodexWriteFailureKind) -> CodexSourceVerificationResult:
        return CodexSourceVerificationResult(
            attempted=False,
            source_state=CodexSourceState.UNCHANGED,
            failure_kind=kind,
            message="verification requires the matching pending source transaction",
        )

    @staticmethod
    def _failure_kind(checks: list[CommandResult]) -> CodexWriteFailureKind:
        if checks and checks[-1].timed_out:
            return CodexWriteFailureKind.VERIFICATION_TIMEOUT
        return CodexWriteFailureKind.VERIFICATION_FAILED

    @staticmethod
    def _rollback(
        transaction: AppliedCodexSourceTransaction,
        kind: CodexWriteFailureKind,
        verification: VerificationResult | None,
    ) -> CodexSourceVerificationResult:
        rolled_back = transaction.rollback()
        return CodexSourceVerificationResult(
            attempted=verification is not None,
            verification_passed=verification is not None
            and verification.status == VerificationStatus.PASSED,
            rollback_attempted=True,
            rollback_succeeded=rolled_back,
            source_state=CodexSourceState.RESTORED if rolled_back else CodexSourceState.UNCERTAIN,
            failure_kind=kind if rolled_back else CodexWriteFailureKind.ROLLBACK_FAILED,
            verification_result=verification,
            changed_paths=transaction.changed_paths,
            message="verification did not permit source acceptance",
        )
