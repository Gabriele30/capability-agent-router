import pytest
from pydantic import ValidationError

from car.providers.models import ProviderClassification
from car.router.consultation import ProviderConsultationResult
from car.router.fusion import FusionPolicy, FusionReason, fuse_routing_decision
from car.router.models import (
    Complexity,
    RiskAssessment,
    RiskLevel,
    Route,
    RoutingDecision,
    ScopeEstimate,
    ScopeSize,
    TaskCategory,
)


def _decision(route: Route = Route.GEMINI, risk: float = 0.2) -> RoutingDecision:
    return RoutingDecision(
        route=route,
        risk=RiskAssessment(score=risk, level=RiskLevel.LOW),
        complexity=Complexity.LOW,
        scope=ScopeEstimate(size=ScopeSize.SMALL),
        confidence=0.9,
        reasons=["deterministic"],
        matched_rules=["test"],
        categories=[TaskCategory.FRONTEND],
    )


def _consultation(
    route: Route, confidence: float = 0.9, risk: float = 0.4
) -> ProviderConsultationResult:
    return ProviderConsultationResult(
        attempted=True,
        succeeded=True,
        classification=ProviderClassification(
            categories=[TaskCategory.FRONTEND],
            complexity=Complexity.LOW,
            risk=risk,
            scope=ScopeSize.SMALL,
            suggested_route=route,
            confidence=confidence,
        ),
    )


@pytest.mark.parametrize(
    ("deterministic", "provider", "expected"),
    [
        (Route.GEMINI, Route.GEMINI_TO_CODEX, Route.GEMINI_TO_CODEX),
        (Route.GEMINI, Route.CODEX, Route.CODEX),
        (Route.GEMINI_TO_CODEX, Route.CODEX, Route.CODEX),
    ],
)
def test_high_confidence_escalation(deterministic: Route, provider: Route, expected: Route):
    outcome = fuse_routing_decision(_decision(deterministic), _consultation(provider))
    assert outcome.final_decision.route == expected
    assert outcome.provider_influenced_decision
    assert outcome.reasons == [FusionReason.PROVIDER_ESCALATION]


def test_agreement_downgrade_plan_and_l0_are_not_changed():
    agreement = fuse_routing_decision(_decision(), _consultation(Route.GEMINI, confidence=0.95))
    downgrade = fuse_routing_decision(
        _decision(Route.GEMINI_TO_CODEX), _consultation(Route.GEMINI, 0.99)
    )
    plan = fuse_routing_decision(_decision(), _consultation(Route.PLAN, 0.99))
    l0 = fuse_routing_decision(_decision(Route.L0), _consultation(Route.CODEX, 0.99))
    assert agreement.reasons == [FusionReason.PROVIDER_AGREEMENT]
    assert downgrade.reasons == [FusionReason.PROVIDER_DOWNGRADE_BLOCKED]
    assert plan.reasons == [FusionReason.PROVIDER_PLAN_IGNORED]
    assert l0.final_decision.route == Route.L0
    assert l0.reasons == [FusionReason.PROVIDER_PLAN_IGNORED]
    assert not any(item.provider_influenced_decision for item in [agreement, downgrade, plan, l0])


def test_confidence_threshold_is_inclusive_and_policy_can_disable_escalation():
    allowed = fuse_routing_decision(_decision(), _consultation(Route.CODEX, 0.70))
    blocked = fuse_routing_decision(_decision(), _consultation(Route.CODEX, 0.699))
    disabled = fuse_routing_decision(
        _decision(), _consultation(Route.CODEX, 0.99), FusionPolicy(allow_provider_escalation=False)
    )
    assert allowed.final_decision.route == Route.CODEX
    assert blocked.reasons == [FusionReason.PROVIDER_CONFIDENCE_BELOW_THRESHOLD]
    assert disabled.reasons == [FusionReason.PROVIDER_ESCALATION_DISABLED]


def test_risk_is_maximum_even_when_route_is_not_escalated():
    outcome = fuse_routing_decision(_decision(risk=0.2), _consultation(Route.CODEX, 0.3, 0.95))
    assert outcome.final_decision.route == Route.GEMINI
    assert outcome.provider_risk == outcome.final_risk == outcome.final_decision.risk.score == 0.95


def test_no_provider_evidence_preserves_deterministic_risk():
    outcome = fuse_routing_decision(
        _decision(risk=0.2),
        ProviderConsultationResult(attempted=True, succeeded=False, error_kind="timeout"),
    )
    assert outcome.final_risk == 0.2
    assert outcome.provider_risk is None
    assert outcome.reasons == [FusionReason.NO_PROVIDER_EVIDENCE]


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_fusion_policy_rejects_invalid_confidence(confidence: float):
    with pytest.raises(ValidationError):
        FusionPolicy(min_provider_confidence_for_escalation=confidence)
