"""Pure handoff construction plus explicit, repository-local persistence."""

from pathlib import Path

from car.coding.models import CodingAttemptResult, CodingTaskContext
from car.coding.verification import CodingVerificationResult
from car.escalation.models import (
    CodexHandoff,
    CodingAttemptSummary,
    EscalationDecision,
    EscalationReason,
    HandoffPolicy,
    PatchAttemptSummary,
    RepositoryHandoffSummary,
    RoutingHandoffSummary,
    VerificationHandoffSummary,
)
from car.patching.models import PatchApplyResult, PatchValidationResult
from car.repository.models import RepositoryState
from car.router.consultation import RoutingEvaluation
from car.router.models import Route
from car.verification.models import VerificationPlan


def _truncate(value: str, maximum: int) -> str:
    return value if len(value) <= maximum else value[:maximum] + "\n[truncated by CAR]"


def build_codex_handoff(
    task: str,
    routing_evaluation: RoutingEvaluation,
    repository: RepositoryState,
    coding_context: CodingTaskContext,
    coding_attempt: CodingAttemptResult,
    patch_validation: PatchValidationResult | None,
    patch_apply: PatchApplyResult | None,
    verification: CodingVerificationResult | None,
    verification_plan: VerificationPlan | None = None,
    policy: HandoffPolicy | None = None,
) -> CodexHandoff:
    active = policy or HandoffPolicy()
    proposal = coding_attempt.proposal
    paths = [change.path for change in proposal.changes] if proposal else []
    diffs = (
        [_truncate(change.patch, active.max_patch_chars) for change in proposal.changes]
        if proposal
        else []
    )
    rollback_ok = (
        verification.rolled_back
        if verification
        else (patch_apply.rolled_back if patch_apply else None)
    )
    rollback_failure = (
        verification.rollback_failure.value
        if verification and verification.rollback_failure
        else (
            patch_apply.rollback_failure_kind.value
            if patch_apply and patch_apply.rollback_failure_kind
            else None
        )
    )
    failure = (
        verification.failure_kind.value if verification and verification.failure_kind else None
    )
    reason = (
        EscalationReason.WORKSPACE_STATE_UNCERTAIN
        if rollback_failure
        else (
            EscalationReason.VERIFICATION_TIMEOUT
            if failure == "check_timeout"
            else EscalationReason.VERIFICATION_FAILED
        )
    )
    recommendation = (
        "Inspect workspace state before making further changes because rollback did not "
        "complete successfully."
        if rollback_failure
        else "Inspect the failed verification evidence and produce a corrected patch. "
        "The previous Gemini patch was rolled back."
    )
    return CodexHandoff(
        task=task,
        routing=RoutingHandoffSummary(
            deterministic_route=routing_evaluation.deterministic_decision.route,
            final_route=routing_evaluation.final_decision.route,
            decision_sources=[item.value for item in routing_evaluation.decision_sources],
            fusion_reasons=routing_evaluation.fusion_reasons[: active.max_reasons],
            provider_influenced_decision=routing_evaluation.provider_influenced_decision,
            deterministic_risk=routing_evaluation.deterministic_risk,
            provider_risk=routing_evaluation.provider_risk,
            final_risk=routing_evaluation.final_risk,
        ),
        repository=RepositoryHandoffSummary(
            name=repository.name,
            branch=repository.git.branch,
            dirty=repository.git.dirty,
            languages=repository.languages.counts,
            systems=repository.project_signals.systems,
        ),
        selected_files=[file.path for file in coding_context.files][: active.max_selected_files],
        coding_attempt=CodingAttemptSummary(
            provider=coding_attempt.provider,
            attempted=coding_attempt.attempted,
            succeeded=coding_attempt.succeeded,
            proposal_summary=proposal.summary if proposal else None,
            reasons=(proposal.reasons if proposal else [])[: active.max_reasons],
            uncertainties=(proposal.uncertainties if proposal else [])[: active.max_reasons],
            error_kind=coding_attempt.error_kind.value if coding_attempt.error_kind else None,
        ),
        patch_attempt=PatchAttemptSummary(
            paths=paths,
            operations=[change.operation.value for change in proposal.changes] if proposal else [],
            diffs=diffs,
            validation_valid=patch_validation.valid if patch_validation else None,
            validation_violations=[item.kind.value for item in patch_validation.violations][
                : active.max_reasons
            ]
            if patch_validation
            else [],
            apply_succeeded=patch_apply.succeeded if patch_apply else None,
            apply_failure=patch_apply.failure_kind.value
            if patch_apply and patch_apply.failure_kind
            else None,
        ),
        verification=VerificationHandoffSummary(
            planned_checks=[command.args for command in verification_plan.commands]
            if verification_plan
            else [],
            executed_checks=[
                {
                    "command": check.command.args,
                    "exit_code": check.exit_code,
                    "timeout": check.timed_out,
                    "stdout": _truncate(check.stdout, active.max_check_output_chars),
                    "stderr": _truncate(check.stderr, active.max_check_output_chars),
                }
                for check in verification.checks
            ]
            if verification
            else [],
            failure_kind=failure,
            rollback_attempted=verification is not None,
            rollback_succeeded=rollback_ok,
            rollback_failure=rollback_failure,
        ),
        escalation_reason=reason,
        recommended_next_step=recommendation,
    )


def decide_escalation(
    handoff: CodexHandoff, verification_passed: bool = False
) -> EscalationDecision:
    if verification_passed:
        return EscalationDecision(
            should_escalate=False, reason=EscalationReason.NO_ESCALATION_SUCCESS
        )
    if handoff.verification.rollback_failure:
        return EscalationDecision(
            should_escalate=False, reason=EscalationReason.WORKSPACE_STATE_UNCERTAIN
        )
    if handoff.routing.final_route == Route.GEMINI_TO_CODEX:
        return EscalationDecision(
            should_escalate=True, target=Route.CODEX, reason=handoff.escalation_reason
        )
    return EscalationDecision(
        should_escalate=False, reason=EscalationReason.ROUTE_DOES_NOT_ALLOW_ESCALATION
    )


def render_codex_handoff_markdown(handoff: CodexHandoff) -> str:
    checks = (
        "\n".join(
            f"- {' '.join(item['command'])}: exit={item['exit_code']} timeout={item['timeout']}\n"
            f"  stdout: {item['stdout']}\n  stderr: {item['stderr']}"
            for item in handoff.verification.executed_checks
        )
        or "- Not attempted"
    )
    patches = (
        "\n".join(
            f"### {path} ({operation})\n```diff\n{diff}\n```"
            for path, operation, diff in zip(
                handoff.patch_attempt.paths,
                handoff.patch_attempt.operations,
                handoff.patch_attempt.diffs,
                strict=True,
            )
        )
        or "Not available"
    )
    sections = [
        "# CAR Codex Handoff",
        "## Original Task",
        handoff.task,
        "## Routing Decision",
        f"Final route: {handoff.routing.final_route.value}",
        "## Repository State",
        f"Name: {handoff.repository.name}",
        f"Branch: {handoff.repository.branch}",
        f"Dirty: {handoff.repository.dirty}",
        "## Selected Files",
        "\n".join(f"- {path}" for path in handoff.selected_files),
        "## Gemini Coding Attempt",
        handoff.coding_attempt.proposal_summary or "No proposal",
        "## Attempted Patch",
        patches,
        "## Verification",
        checks,
        "## Rollback",
        f"Succeeded: {handoff.verification.rollback_succeeded}",
        f"Failure: {handoff.verification.rollback_failure}",
        "## Recommended Next Step",
        handoff.recommended_next_step,
    ]
    return "\n\n".join(sections) + "\n"


def write_codex_handoff(repository_root: Path, handoff: CodexHandoff) -> Path:
    root = repository_root.resolve()
    context = root / ".car-context"
    if context.is_symlink() or (context.exists() and not context.is_dir()):
        raise ValueError("unsafe CAR context directory")
    context.mkdir(exist_ok=True)
    target = context / "current-task.md"
    if target.is_symlink() or not target.resolve(strict=False).is_relative_to(root):
        raise ValueError("unsafe handoff target")
    temporary = context / "current-task.tmp"
    temporary.write_text(render_codex_handoff_markdown(handoff), encoding="utf-8")
    temporary.replace(target)
    return target
