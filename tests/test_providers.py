import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError

from car.config.models import CarConfig
from car.providers.context import build_classification_context
from car.providers.gemini import GeminiProvider, GeminiProviderConfig
from car.providers.models import ProviderClassification, ProviderStatus
from car.repository.scanner import scan_repository
from car.router.analysis import analyze_task
from car.router.engine import assess_risk
from car.router.models import Complexity, Route, ScopeSize, TaskCategory


def test_old_config_loads_with_disabled_gemini() -> None:
    config = CarConfig.model_validate({"schema_version": 1, "default_mode": "auto"})
    assert config.schema_version == 4
    assert config.providers.gemini.enabled is False
    assert config.providers.gemini.model is None


def test_gemini_local_health_states(monkeypatch) -> None:
    disabled = GeminiProvider(GeminiProviderConfig())
    assert disabled.health().status == ProviderStatus.DISABLED
    missing_model = GeminiProvider(GeminiProviderConfig(enabled=True))
    assert missing_model.health().status == ProviderStatus.NOT_CONFIGURED
    missing_key = GeminiProvider(GeminiProviderConfig(enabled=True, model="test"), environment={})
    assert missing_key.health().status == ProviderStatus.MISSING_CREDENTIALS
    monkeypatch.setattr(importlib.util, "find_spec", lambda _: object())
    configured = GeminiProvider(
        GeminiProviderConfig(enabled=True, model="test"),
        environment={"GEMINI_API_KEY": "secret-value"},
    )
    assert configured.health().status == ProviderStatus.CONFIGURED


def test_secret_is_never_represented(monkeypatch) -> None:
    secret = "super-secret-test-key"
    monkeypatch.setattr(importlib.util, "find_spec", lambda _: object())
    provider = GeminiProvider(
        GeminiProviderConfig(enabled=True, model="test"), {"GEMINI_API_KEY": secret}
    )
    rendered = f"{provider!r} {provider.health().model_dump_json()}"
    assert secret not in rendered


def test_classification_context_is_metadata_only(git_repository: Path) -> None:
    repository = scan_repository(git_repository)
    analysis = analyze_task("Fix CSS spacing", repository)
    context = build_classification_context(
        "Fix CSS spacing", repository, analysis, assess_risk(analysis, repository)
    )
    payload = context.model_dump_json()
    assert str(repository.root) not in payload
    assert context.repository.name == git_repository.name


@pytest.mark.parametrize("field,value", [("risk", 1.1), ("confidence", -0.1)])
def test_classification_bounds(field: str, value: float) -> None:
    values = dict(
        categories=[TaskCategory.FRONTEND],
        complexity=Complexity.LOW,
        risk=0.1,
        scope=ScopeSize.SMALL,
        suggested_route=Route.GEMINI,
        confidence=0.5,
    )
    values[field] = value
    with pytest.raises(ValidationError):
        ProviderClassification(**values)


def test_provider_cannot_suggest_l0() -> None:
    with pytest.raises(ValidationError):
        ProviderClassification(
            categories=[TaskCategory.FORMATTING],
            complexity=Complexity.LOW,
            risk=0.1,
            scope=ScopeSize.SMALL,
            suggested_route=Route.L0,
            confidence=0.5,
        )


def test_core_packages_do_not_import_google_sdk() -> None:
    root = Path(__file__).parents[1] / "car"
    for directory in ("router", "execution", "l0", "verification"):
        for source in (root / directory).glob("*.py"):
            assert "google.genai" not in source.read_text(encoding="utf-8")
