from pathlib import Path

import pytest

from car.providers.models import (
    ProviderCapabilities,
    ProviderClassification,
    ProviderHealth,
    ProviderStatus,
)
from car.repository.scanner import scan_repository
from car.router.consultation import (
    ConsultationSkipReason,
    DecisionSource,
    evaluate_routing,
)
from car.router.models import Complexity, Route, ScopeSize, TaskCategory, TaskRequest, UserMode


class FakeProvider:
    def __init__(
        self,
        status=ProviderStatus.CONFIGURED,
        route=Route.CODEX,
        confidence=0.99,
        risk=0.95,
        error: RuntimeError | None = None,
    ):
        self.status, self.route, self.confidence, self.risk = status, route, confidence, risk
        self.error, self.calls, self.context = error, 0, None

    def capabilities(self):
        return ProviderCapabilities(supports_classification=True)

    def health(self):
        return ProviderHealth(
            status=self.status, configured=self.status == ProviderStatus.CONFIGURED
        )

    def classify(self, context):
        self.calls += 1
        self.context = context
        if self.error:
            raise self.error
        return ProviderClassification(
            categories=[TaskCategory.FRONTEND],
            complexity=Complexity.LOW,
            risk=self.risk,
            scope=ScopeSize.SMALL,
            suggested_route=self.route,
            confidence=self.confidence,
        )


def test_l0_and_hard_rules_skip(git_repository: Path):
    repository = scan_repository(git_repository)
    for task, expected in [
        ("Format README.md", ConsultationSkipReason.DETERMINISTIC_L0),
        ("Fix authentication bypass", ConsultationSkipReason.HARD_CODEX_RULE),
    ]:
        provider = FakeProvider()
        result = evaluate_routing(
            TaskRequest(description=task), repository, UserMode.AUTO, provider
        )
        assert provider.calls == 0 and result.provider_consultation.skip_reason == expected
        assert result.final_decision.route == result.deterministic_decision.route


@pytest.mark.parametrize(
    "task",
    [
        "Fix authentication bypass",
        "Fix race condition in worker pool",
        "Implement protocol state machine",
        "Redesign application architecture",
    ],
)
def test_hard_codex_rules_never_consult_provider(git_repository: Path, task: str):
    provider = FakeProvider()
    result = evaluate_routing(
        TaskRequest(description=task), scan_repository(git_repository), UserMode.AUTO, provider
    )
    assert provider.calls == 0
    assert result.final_decision.route == Route.CODEX
    assert result.decision_sources == [DecisionSource.DETERMINISTIC, DecisionSource.HARD_RULE]


@pytest.mark.parametrize(
    "mode", [UserMode.GEMINI, UserMode.GEMINI_TO_CODEX, UserMode.CODEX, UserMode.PLAN]
)
def test_overrides_skip(git_repository: Path, mode: UserMode):
    provider = FakeProvider()
    result = evaluate_routing(
        TaskRequest(description="Fix CSS spacing"), scan_repository(git_repository), mode, provider
    )
    assert (
        provider.calls == 0
        and result.provider_consultation.skip_reason == ConsultationSkipReason.EXPLICIT_OVERRIDE
    )
    assert result.final_decision.route == Route(mode.value)
    assert result.decision_sources == [
        DecisionSource.DETERMINISTIC,
        DecisionSource.EXPLICIT_OVERRIDE,
    ]


def test_high_confidence_provider_can_escalate_css_route(git_repository: Path):
    provider = FakeProvider(route=Route.CODEX)
    result = evaluate_routing(
        TaskRequest(description="Fix CSS spacing"),
        scan_repository(git_repository),
        UserMode.AUTO,
        provider,
    )
    assert provider.calls == 1 and result.deterministic_decision.route == Route.GEMINI
    assert result.final_decision.route == Route.CODEX
    assert result.provider_influenced_decision
    assert result.final_risk == 0.95


def test_low_confidence_provider_cannot_escalate_but_risk_is_preserved(git_repository: Path):
    provider = FakeProvider(route=Route.CODEX, confidence=0.699, risk=0.95)
    result = evaluate_routing(
        TaskRequest(description="Fix CSS spacing"),
        scan_repository(git_repository),
        UserMode.AUTO,
        provider,
    )
    assert result.final_decision.route == Route.GEMINI
    assert not result.provider_influenced_decision
    assert result.final_risk == 0.95
    assert result.fusion_reasons == ["provider_confidence_below_threshold"]


def test_provider_agreement_and_parser_escalation(git_repository: Path):
    repository = scan_repository(git_repository)
    agreement = evaluate_routing(
        TaskRequest(description="Fix CSS spacing"),
        repository,
        UserMode.AUTO,
        FakeProvider(route=Route.GEMINI),
    )
    escalation = evaluate_routing(
        TaskRequest(description="Fix parser regression"),
        repository,
        UserMode.AUTO,
        FakeProvider(route=Route.CODEX),
    )
    assert agreement.final_decision.route == Route.GEMINI
    assert not agreement.provider_influenced_decision
    assert escalation.deterministic_decision.route == Route.GEMINI_TO_CODEX
    assert escalation.final_decision.route == Route.CODEX
    assert escalation.provider_influenced_decision


def test_unavailable_and_failing_provider_do_not_change_route(git_repository: Path):
    repository = scan_repository(git_repository)
    unavailable = evaluate_routing(
        TaskRequest(description="Fix CSS spacing"),
        repository,
        UserMode.AUTO,
        FakeProvider(status=ProviderStatus.MISSING_CREDENTIALS),
    )
    failed = evaluate_routing(
        TaskRequest(description="Fix CSS spacing"),
        repository,
        UserMode.AUTO,
        FakeProvider(error=RuntimeError("timeout")),
    )
    assert unavailable.final_decision.route == Route.GEMINI
    assert unavailable.provider_risk is None
    assert failed.final_decision.route == Route.GEMINI
    assert not failed.provider_influenced_decision
