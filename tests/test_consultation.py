from pathlib import Path

import pytest

from car.providers.models import (
    ProviderCapabilities,
    ProviderClassification,
    ProviderHealth,
    ProviderStatus,
)
from car.repository.scanner import scan_repository
from car.router.consultation import ConsultationSkipReason, evaluate_routing
from car.router.models import Complexity, Route, ScopeSize, TaskCategory, TaskRequest, UserMode


class FakeProvider:
    def __init__(self, status=ProviderStatus.CONFIGURED, route=Route.CODEX):
        self.status, self.route, self.calls, self.context = status, route, 0, None

    def capabilities(self):
        return ProviderCapabilities(supports_classification=True)

    def health(self):
        return ProviderHealth(
            status=self.status, configured=self.status == ProviderStatus.CONFIGURED
        )

    def classify(self, context):
        self.calls += 1
        self.context = context
        return ProviderClassification(
            categories=[TaskCategory.FRONTEND],
            complexity=Complexity.LOW,
            risk=0.95,
            scope=ScopeSize.SMALL,
            suggested_route=self.route,
            confidence=0.99,
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


def test_provider_evidence_cannot_change_route(git_repository: Path):
    provider = FakeProvider(route=Route.CODEX)
    result = evaluate_routing(
        TaskRequest(description="Fix CSS spacing"),
        scan_repository(git_repository),
        UserMode.AUTO,
        provider,
    )
    assert provider.calls == 1 and result.deterministic_decision.route == Route.GEMINI
    assert result.final_decision.route == Route.GEMINI
