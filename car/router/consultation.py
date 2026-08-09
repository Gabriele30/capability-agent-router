"""Optional provider consultation; deterministic routing remains authoritative in 4C1."""

from enum import StrEnum

from pydantic import BaseModel

from car.providers.base import ClassificationProvider
from car.providers.context import build_classification_context
from car.providers.models import (
    ProviderClassification,
    ProviderErrorKind,
    ProviderStatus,
)
from car.repository.models import RepositoryState
from car.router.analysis import analyze_task
from car.router.engine import DecisionEngine, assess_risk
from car.router.models import RoutingDecision, TaskRequest, UserMode


class ConsultationSkipReason(StrEnum):
    EXPLICIT_OVERRIDE = "explicit_override"
    DETERMINISTIC_L0 = "deterministic_l0"
    HARD_CODEX_RULE = "hard_codex_rule"
    PROVIDER_NOT_SUPPLIED = "provider_not_supplied"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


class DecisionSource(StrEnum):
    DETERMINISTIC = "deterministic"
    PROVIDER = "provider"
    FUSION_POLICY = "fusion_policy"
    HARD_RULE = "hard_rule"
    EXPLICIT_OVERRIDE = "explicit_override"


class ProviderConsultationResult(BaseModel):
    attempted: bool
    succeeded: bool
    skip_reason: ConsultationSkipReason | None = None
    classification: ProviderClassification | None = None
    error_kind: str | None = None


class RoutingEvaluation(BaseModel):
    deterministic_decision: RoutingDecision
    provider_consultation: ProviderConsultationResult
    final_decision: RoutingDecision
    deterministic_risk: float
    provider_risk: float | None = None
    final_risk: float
    provider_influenced_decision: bool = False
    fusion_reasons: list[str]
    decision_sources: list[DecisionSource]


def evaluate_routing(
    task: TaskRequest,
    repository: RepositoryState,
    mode: UserMode,
    provider: ClassificationProvider | None = None,
) -> RoutingEvaluation:
    engine = DecisionEngine()
    analysis = analyze_task(task.description, repository)
    decision = engine.decide_from_analysis(analysis, repository, mode)
    if mode != UserMode.AUTO:
        consultation = ProviderConsultationResult(
            attempted=False, succeeded=False, skip_reason=ConsultationSkipReason.EXPLICIT_OVERRIDE
        )
    elif decision.route.value == "l0":
        consultation = ProviderConsultationResult(
            attempted=False, succeeded=False, skip_reason=ConsultationSkipReason.DETERMINISTIC_L0
        )
    elif (
        "hard-risk-category" in decision.matched_rules
        or "production-deployment" in decision.matched_rules
    ):
        consultation = ProviderConsultationResult(
            attempted=False, succeeded=False, skip_reason=ConsultationSkipReason.HARD_CODEX_RULE
        )
    elif provider is None:
        consultation = ProviderConsultationResult(
            attempted=False,
            succeeded=False,
            skip_reason=ConsultationSkipReason.PROVIDER_NOT_SUPPLIED,
        )
    elif provider.health().status != ProviderStatus.CONFIGURED:
        consultation = ProviderConsultationResult(
            attempted=False,
            succeeded=False,
            skip_reason=ConsultationSkipReason.PROVIDER_UNAVAILABLE,
        )
    else:
        try:
            classification = provider.classify(
                build_classification_context(
                    task.description, repository, analysis, assess_risk(analysis, repository)
                )
            )
            consultation = ProviderConsultationResult(
                attempted=True, succeeded=True, classification=classification
            )
        except RuntimeError as error:
            consultation = ProviderConsultationResult(
                attempted=True, succeeded=False, error_kind=_safe_error_kind(str(error))
            )
    from car.router.fusion import fuse_routing_decision

    outcome = fuse_routing_decision(decision, consultation)
    sources = [DecisionSource.DETERMINISTIC]
    if consultation.skip_reason == ConsultationSkipReason.EXPLICIT_OVERRIDE:
        sources.append(DecisionSource.EXPLICIT_OVERRIDE)
    elif consultation.skip_reason == ConsultationSkipReason.HARD_CODEX_RULE:
        sources.append(DecisionSource.HARD_RULE)
    elif consultation.succeeded:
        sources.extend([DecisionSource.PROVIDER, DecisionSource.FUSION_POLICY])
    return RoutingEvaluation(
        deterministic_decision=decision,
        provider_consultation=consultation,
        final_decision=outcome.final_decision,
        deterministic_risk=decision.risk.score,
        provider_risk=outcome.provider_risk,
        final_risk=outcome.final_risk,
        provider_influenced_decision=outcome.provider_influenced_decision,
        fusion_reasons=[reason.value for reason in outcome.reasons],
        decision_sources=sources,
    )


def _safe_error_kind(value: str) -> str:
    """Expose only known provider taxonomy values, never raw exception text."""
    if value in {kind.value for kind in ProviderErrorKind}:
        return value
    if value in {status.value for status in ProviderStatus}:
        return value
    return ProviderErrorKind.UNKNOWN_ERROR.value
