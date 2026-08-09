from pathlib import Path

import pytest

from car.application.routing import evaluate_analysis
from car.config.models import CarConfig
from car.providers.models import (
    ProviderCapabilities,
    ProviderClassification,
    ProviderHealth,
    ProviderStatus,
)
from car.repository.scanner import scan_repository
from car.router.models import Complexity, Route, ScopeSize, TaskCategory, TaskRequest, UserMode


class FakeProvider:
    def __init__(self, status=ProviderStatus.CONFIGURED, route=Route.CODEX, confidence=0.95):
        self.status = status
        self.route = route
        self.confidence = confidence
        self.calls = 0
        self.failure: RuntimeError | None = None

    def capabilities(self):
        return ProviderCapabilities(supports_classification=True)

    def health(self):
        return ProviderHealth(
            status=self.status, configured=self.status == ProviderStatus.CONFIGURED
        )

    def classify(self, context):
        self.calls += 1
        if self.failure:
            raise self.failure
        return ProviderClassification(
            categories=[TaskCategory.FRONTEND],
            complexity=Complexity.LOW,
            risk=0.8,
            scope=ScopeSize.SMALL,
            suggested_route=self.route,
            confidence=self.confidence,
        )


def _configured_config() -> CarConfig:
    return CarConfig(providers={"gemini": {"enabled": True, "model": "configured-model"}})


@pytest.mark.parametrize(
    ("task", "mode", "expected"),
    [
        ("Format README.md", UserMode.AUTO, Route.L0),
        ("Fix authentication bypass", UserMode.AUTO, Route.CODEX),
        ("Fix CSS spacing", UserMode.CODEX, Route.CODEX),
        ("Fix CSS spacing", UserMode.GEMINI, Route.GEMINI),
        ("Fix CSS spacing", UserMode.GEMINI_TO_CODEX, Route.GEMINI_TO_CODEX),
        ("Fix CSS spacing", UserMode.PLAN, Route.PLAN),
    ],
)
def test_composition_keeps_lazy_and_explicit_gates(
    git_repository: Path, task: str, mode: UserMode, expected: Route
):
    provider = FakeProvider()
    _, result = evaluate_analysis(
        TaskRequest(description=task),
        scan_repository(git_repository),
        mode,
        _configured_config(),
        provider_factory=lambda _: provider,
    )
    assert provider.calls == 0
    assert result.final_decision.route == expected


def test_configured_provider_is_consulted_and_can_escalate(git_repository: Path):
    provider = FakeProvider(route=Route.CODEX)
    _, result = evaluate_analysis(
        TaskRequest(description="Fix CSS spacing in dashboard"),
        scan_repository(git_repository),
        UserMode.AUTO,
        _configured_config(),
        provider_factory=lambda _: provider,
    )
    assert provider.calls == 1
    assert result.deterministic_decision.route == Route.GEMINI
    assert result.final_decision.route == Route.CODEX
    assert result.provider_influenced_decision


def test_parser_and_low_confidence_provider_fusion(git_repository: Path):
    parser = FakeProvider(route=Route.CODEX)
    low_confidence = FakeProvider(route=Route.CODEX, confidence=0.20)
    repository = scan_repository(git_repository)
    _, parser_result = evaluate_analysis(
        TaskRequest(description="Fix parser regression"),
        repository,
        UserMode.AUTO,
        _configured_config(),
        provider_factory=lambda _: parser,
    )
    _, css_result = evaluate_analysis(
        TaskRequest(description="Fix CSS spacing"),
        repository,
        UserMode.AUTO,
        _configured_config(),
        provider_factory=lambda _: low_confidence,
    )
    assert parser_result.final_decision.route == Route.CODEX
    assert css_result.final_decision.route == Route.GEMINI
    assert not css_result.provider_influenced_decision


@pytest.mark.parametrize(
    "status",
    [ProviderStatus.DISABLED, ProviderStatus.NOT_CONFIGURED, ProviderStatus.MISSING_CREDENTIALS],
)
def test_unavailable_provider_falls_back_without_classification(
    git_repository: Path, status: ProviderStatus
):
    provider = FakeProvider(status=status)
    _, result = evaluate_analysis(
        TaskRequest(description="Fix CSS spacing"),
        scan_repository(git_repository),
        UserMode.AUTO,
        _configured_config(),
        provider_factory=lambda _: provider,
    )
    assert provider.calls == 0
    assert result.final_decision.route == Route.GEMINI
    assert result.provider_consultation.skip_reason.value == "provider_unavailable"


def test_provider_failure_falls_back_with_normalized_error(git_repository: Path):
    provider = FakeProvider()
    provider.failure = RuntimeError("timeout")
    _, result = evaluate_analysis(
        TaskRequest(description="Fix CSS spacing"),
        scan_repository(git_repository),
        UserMode.AUTO,
        _configured_config(),
        provider_factory=lambda _: provider,
    )
    assert result.provider_consultation.attempted
    assert result.provider_consultation.error_kind == "timeout"
    assert result.final_decision.route == Route.GEMINI
