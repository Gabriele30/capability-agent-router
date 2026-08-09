"""Conservative provider-evidence fusion, separate from deterministic routing."""

from enum import StrEnum

from pydantic import BaseModel, Field

from car.router.consultation import ProviderConsultationResult
from car.router.models import RiskAssessment, RiskLevel, Route, RoutingDecision


class FusionReason(StrEnum):
    NO_PROVIDER_EVIDENCE = "no_provider_evidence"
    PROVIDER_AGREEMENT = "provider_agreement"
    PROVIDER_ESCALATION = "provider_escalation"
    PROVIDER_DOWNGRADE_BLOCKED = "provider_downgrade_blocked"
    PROVIDER_CONFIDENCE_BELOW_THRESHOLD = "provider_confidence_below_threshold"
    PROVIDER_PLAN_IGNORED = "provider_plan_ignored"
    PROVIDER_ESCALATION_DISABLED = "provider_escalation_disabled"


class FusionPolicy(BaseModel):
    allow_provider_escalation: bool = True
    min_provider_confidence_for_escalation: float = Field(default=0.70, ge=0, le=1)


class FusionOutcome(BaseModel):
    final_decision: RoutingDecision
    provider_influenced_decision: bool
    reasons: list[FusionReason]
    provider_risk: float | None = None
    final_risk: float


def fuse_routing_decision(
    decision: RoutingDecision,
    consultation: ProviderConsultationResult,
    policy: FusionPolicy | None = None,
) -> FusionOutcome:
    active = policy or FusionPolicy()
    if not consultation.succeeded or consultation.classification is None:
        return FusionOutcome(
            final_decision=decision,
            provider_influenced_decision=False,
            reasons=[FusionReason.NO_PROVIDER_EVIDENCE],
            final_risk=decision.risk.score,
        )

    provider_risk = consultation.classification.risk
    final_risk = max(decision.risk.score, provider_risk)
    risk = RiskAssessment(
        score=final_risk,
        level=_risk_level(final_risk),
        indicators=decision.risk.indicators,
    )
    risk_aware_decision = decision.model_copy(update={"risk": risk})
    suggested = consultation.classification.suggested_route
    levels = {Route.GEMINI: 1, Route.GEMINI_TO_CODEX: 2, Route.CODEX: 3}
    if suggested == Route.PLAN or decision.route not in levels:
        return _outcome(
            risk_aware_decision,
            False,
            FusionReason.PROVIDER_PLAN_IGNORED,
            provider_risk,
        )
    if suggested == decision.route:
        return _outcome(risk_aware_decision, False, FusionReason.PROVIDER_AGREEMENT, provider_risk)
    if levels[suggested] < levels[decision.route]:
        return _outcome(
            risk_aware_decision, False, FusionReason.PROVIDER_DOWNGRADE_BLOCKED, provider_risk
        )
    if not active.allow_provider_escalation:
        return _outcome(
            risk_aware_decision, False, FusionReason.PROVIDER_ESCALATION_DISABLED, provider_risk
        )
    if consultation.classification.confidence < active.min_provider_confidence_for_escalation:
        return _outcome(
            risk_aware_decision,
            False,
            FusionReason.PROVIDER_CONFIDENCE_BELOW_THRESHOLD,
            provider_risk,
        )
    return _outcome(
        risk_aware_decision.model_copy(update={"route": suggested}),
        True,
        FusionReason.PROVIDER_ESCALATION,
        provider_risk,
    )


def _outcome(
    decision: RoutingDecision,
    influenced: bool,
    reason: FusionReason,
    provider_risk: float,
) -> FusionOutcome:
    return FusionOutcome(
        final_decision=decision,
        provider_influenced_decision=influenced,
        reasons=[reason],
        provider_risk=provider_risk,
        final_risk=decision.risk.score,
    )


def _risk_level(score: float) -> RiskLevel:
    if score >= 0.75:
        return RiskLevel.HIGH
    if score > 0.35:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW
