"""Benchmark-only adapters over CAR's existing application boundaries."""

from dataclasses import dataclass, field

from car.application.codex import CodexExecutionPolicy
from car.application.coding_execution import (
    CodingPipelineExecutionPolicy,
    execute_authorized_coding_pipeline,
)
from car.application.coding_flow import execute_coding_flow
from car.benchmark.context import build_execution_context
from car.benchmark.models import BenchmarkStrategy
from car.benchmark.results import (
    BenchmarkExecutionOutcome,
    BenchmarkInvariantError,
    BenchmarkPatchViolation,
)
from car.codex_write.models import CodexWriteAuthorization, CodexWritePolicy
from car.codex_write.pipeline import ControlledCodexWritePipeline
from car.codex_write.runtime_models import CodexReasoningEffort
from car.coding.base import CodingProvider
from car.escalation.models import HandoffPolicy
from car.telemetry import (
    AttemptCapability,
    ExecutionTelemetryCollector,
    FinalOutcome,
    TokenUsage,
    VerificationTelemetry,
)
from car.telemetry.models import UsageSource


@dataclass(frozen=True)
class BenchmarkExecutionDependencies:
    coding_provider: CodingProvider
    codex_runtime: object
    controlled_pipeline: ControlledCodexWritePipeline | None = None
    codex_write_policy: CodexWritePolicy = field(
        default_factory=lambda: CodexWritePolicy(enabled=True)
    )
    codex_model: str | None = None
    codex_reasoning_effort: CodexReasoningEffort | None = None


class CARBenchmarkExecutor:
    """Dispatch benchmark strategies without exposing them to production routing."""

    def __init__(self, dependencies: BenchmarkExecutionDependencies) -> None:
        self._dependencies = dependencies

    def execute(self, case, workspace, strategy):
        """Preserve the telemetry-only executor contract used by existing callers."""
        return self.execute_outcome(case, workspace, strategy).telemetry

    def execute_outcome(self, case, workspace, strategy) -> BenchmarkExecutionOutcome:
        """Return benchmark-only bounded failure metadata alongside telemetry."""
        try:
            context = build_execution_context(case, workspace, strategy)
        except Exception as error:
            raise BenchmarkInvariantError("invalid benchmark execution context") from error
        if strategy == BenchmarkStrategy.GEMINI_ONLY:
            return self._gemini_only(context)
        if strategy == BenchmarkStrategy.CODEX_ONLY:
            return self._codex_only(context)
        return self._car(context)

    def _gemini_only(self, context) -> BenchmarkExecutionOutcome:
        collector = ExecutionTelemetryCollector()
        route = context.routing.final_decision.route
        collector.start_execution(initial_route=route, task_category=context.case.category)
        sequence = collector.start_attempt(
            AttemptCapability.GEMINI,
            provider="gemini",
            model=_provider_model(self._dependencies.coding_provider),
        )
        result = execute_authorized_coding_pipeline(
            repository_root=context.workspace,
            routing_evaluation=context.routing,
            coding_context=context.coding,
            coding_provider=self._dependencies.coding_provider,
            coding_policy=None,
            patch_validation_policy=None,
            verification_plan=context.verification,
            execution_policy=CodingPipelineExecutionPolicy(enabled=True),
        )
        pipeline = result.pipeline_result
        verification = _coding_verification(pipeline.verification if pipeline else None)
        usage = pipeline.coding_attempt.usage if pipeline and pipeline.coding_attempt else None
        collector.finish_attempt(
            sequence,
            succeeded=result.succeeded,
            failure_kind=result.failure_kind.value if result.failure_kind else None,
            usage=usage,
            verification=verification,
        )
        if verification:
            collector.record_verification(verification)
        return BenchmarkExecutionOutcome(
            telemetry=collector.finish_execution(
                final_route=route,
                final_outcome=(
                    FinalOutcome.VERIFIED_SUCCESS if result.succeeded else FinalOutcome.RESTORED
                ),
                verified_success=result.succeeded,
            ),
            task_changed_paths=_task_changed_paths(pipeline.patch_validation if pipeline else None),
            auxiliary_changed_paths=_auxiliary_changed_paths(
                pipeline.patch_validation if pipeline else None
            ),
            patch_violations=_patch_violations(pipeline.patch_validation if pipeline else None),
            pipeline_outcome=pipeline.outcome if pipeline else None,
            provider_error_kind=(
                pipeline.coding_attempt.error_kind
                if pipeline and pipeline.coding_attempt is not None
                else None
            ),
        )

    def _codex_only(self, context) -> BenchmarkExecutionOutcome:
        collector = ExecutionTelemetryCollector()
        collector.start_execution(
            initial_route=context.routing.final_decision.route, task_category=context.case.category
        )
        sequence = collector.start_attempt(
            AttemptCapability.CODEX_CONTROLLED_WRITE,
            provider="codex",
            model=self._dependencies.codex_model,
        )
        pipeline = self._dependencies.controlled_pipeline or ControlledCodexWritePipeline()
        result = pipeline.execute(
            context.workspace,
            context.case.task,
            context.case.authorized_paths,
            context.verification,
            self._dependencies.codex_write_policy,
            CodexWriteAuthorization(authorized=True),
            codex_model=self._dependencies.codex_model,
            codex_reasoning_effort=self._dependencies.codex_reasoning_effort,
            authorization_summary=context.coding.authorization_summary,
        )
        verification = _codex_verification(result.verification_result)
        collector.finish_attempt(
            sequence,
            succeeded=result.accepted,
            failure_kind=result.failure_kind.value if result.failure_kind else None,
            usage=_codex_usage(result),
            verification=verification,
        )
        if verification:
            collector.record_verification(verification)
        return BenchmarkExecutionOutcome(
            telemetry=collector.finish_execution(
                final_route=context.routing.final_decision.route,
                final_outcome=(
                    FinalOutcome.VERIFIED_SUCCESS
                    if result.accepted
                    else FinalOutcome.UNCERTAIN
                    if result.source_state.value == "uncertain"
                    else FinalOutcome.RESTORED
                ),
                verified_success=result.accepted,
                source_state=result.source_state,
            ),
            rejected_paths=_rejected_paths(result),
            patch_violations=_patch_violations(getattr(result, "delta_result", None)),
            task_changed_paths=_task_changed_paths(getattr(result, "delta_result", None)),
            auxiliary_changed_paths=_auxiliary_changed_paths(getattr(result, "delta_result", None)),
        )

    def _car(self, context) -> BenchmarkExecutionOutcome:
        result = execute_coding_flow(
            repository_root=context.workspace,
            routing_evaluation=context.routing,
            repository_state=context.repository,
            coding_context=context.coding,
            coding_provider=self._dependencies.coding_provider,
            coding_policy=None,
            patch_validation_policy=None,
            verification_plan=context.verification,
            coding_execution_policy=CodingPipelineExecutionPolicy(enabled=True),
            handoff_policy=HandoffPolicy(),
            codex_runtime=self._dependencies.codex_runtime,
            codex_execution_policy=CodexExecutionPolicy(enabled=False),
            codex_write_policy=self._dependencies.codex_write_policy,
            codex_write_authorization=CodexWriteAuthorization(authorized=True),
            codex_write_paths=context.case.authorized_paths,
            codex_write_scope_summary=context.coding.authorization_summary,
            codex_model=self._dependencies.codex_model,
            codex_reasoning_effort=self._dependencies.codex_reasoning_effort,
            controlled_write_pipeline=self._dependencies.controlled_pipeline,
        )
        if result.telemetry is None:
            raise RuntimeError("CAR application flow returned no telemetry")
        pipeline = result.coding.pipeline_result
        controlled = result.controlled_write or (
            result.post_failure.controlled_write if result.post_failure else None
        )
        delta = getattr(controlled, "delta_result", None)
        return BenchmarkExecutionOutcome(
            telemetry=result.telemetry,
            rejected_paths=_rejected_paths(controlled) if controlled is not None else (),
            patch_violations=_patch_violations(delta),
            task_changed_paths=(
                _task_changed_paths(delta)
                if delta is not None
                else _task_changed_paths(pipeline.patch_validation if pipeline else None)
            ),
            auxiliary_changed_paths=(
                _auxiliary_changed_paths(delta)
                if delta is not None
                else _auxiliary_changed_paths(pipeline.patch_validation if pipeline else None)
            ),
            pipeline_outcome=pipeline.outcome if pipeline else None,
            provider_error_kind=(
                pipeline.coding_attempt.error_kind
                if pipeline and pipeline.coding_attempt is not None
                else None
            ),
        )


def _coding_verification(result) -> VerificationTelemetry | None:
    if result is None:
        return None
    failures = sum(
        check.exit_code != 0 or check.timed_out or check.executable_not_found
        for check in result.checks
    )
    return VerificationTelemetry(
        attempted=result.attempted,
        passed=result.passed,
        check_count=len(result.checks),
        passed_check_count=len(result.checks) - failures,
        failed_check_count=failures,
        timeout_count=sum(check.timed_out for check in result.checks),
    )


def _codex_verification(result) -> VerificationTelemetry | None:
    if result is None:
        return None
    verification = result.verification_result
    checks = getattr(verification, "checks", []) if verification else []
    return VerificationTelemetry(
        attempted=result.attempted,
        passed=result.accepted,
        check_count=len(checks),
        passed_check_count=len(checks) if result.accepted else 0,
        failed_check_count=0 if result.accepted else len(checks),
        timeout_count=sum(check.timed_out for check in checks),
    )


def _rejected_paths(result) -> tuple[str, ...]:
    """Extract CAR-validated repository-relative rejection metadata only."""
    delta_result = getattr(result, "delta_result", None)
    if isinstance(delta_result, dict):
        paths = delta_result.get("rejected_paths", ())
    else:
        paths = getattr(delta_result, "rejected_paths", ())
        if not paths:
            paths = tuple(
                violation.path
                for violation in getattr(delta_result, "violations", ())
                if violation.path is not None
            )
    return tuple(paths) if isinstance(paths, list | tuple) else ()


def _patch_violations(result) -> tuple[BenchmarkPatchViolation, ...]:
    """Export only existing validator taxonomy, relative paths, and bounded summaries."""
    violations = getattr(result, "violations", ())
    return tuple(
        BenchmarkPatchViolation(kind=item.kind, path=item.path, summary=item.summary)
        for item in violations
    )


def _task_changed_paths(result) -> tuple[str, ...]:
    paths = getattr(result, "task_changed_paths", ())
    return tuple(paths) if isinstance(paths, list | tuple) else ()


def _auxiliary_changed_paths(result) -> tuple[str, ...]:
    paths = getattr(result, "auxiliary_changed_paths", ())
    return tuple(paths) if isinstance(paths, list | tuple) else ()


def _codex_usage(result) -> TokenUsage:
    """Forward only runtime-validated structured Codex usage."""
    usage = getattr(getattr(result, "codex_result", None), "usage", None)
    return usage if isinstance(usage, TokenUsage) else TokenUsage(source=UsageSource.UNAVAILABLE)


def _provider_model(provider: CodingProvider) -> str | None:
    """Read an optional provider-neutral configured model identifier safely."""
    model = getattr(provider, "model", None)
    return model if isinstance(model, str) and model else None
