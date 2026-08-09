"""Optional live Gemini validation. Standard pytest never reaches the network here."""

import os
from pathlib import Path

import pytest

from car.config.models import CarConfig
from car.providers.gemini import GeminiProvider
from car.providers.models import (
    ClassificationContext,
    DeterministicClassificationContext,
    ProviderClassification,
    RepositoryClassificationContext,
)
from car.router.models import Complexity, Route, ScopeSize, TaskCategory

pytestmark = pytest.mark.live


def _live_config() -> CarConfig:
    path = Path.cwd() / ".car-context" / "config.json"
    return (
        CarConfig.model_validate_json(path.read_text(encoding="utf-8"))
        if path.exists()
        else CarConfig()
    )


def test_gemini_structured_classification_live() -> None:
    if os.environ.get("CAR_RUN_LIVE_GEMINI_TESTS") != "1":
        pytest.skip("Gemini live test skipped: opt-in unavailable.")
    config = _live_config().providers.gemini
    if not config.enabled:
        pytest.skip("Gemini live test skipped: provider disabled.")
    if not config.model:
        pytest.skip("Gemini live test skipped: model not configured.")
    if not os.environ.get(config.api_key_env):
        pytest.skip("Gemini live test skipped: credentials unavailable.")
    context = ClassificationContext(
        task="Fix CSS spacing in dashboard cards",
        repository=RepositoryClassificationContext(
            name="capability-agent-router",
            branch="main",
            dirty=False,
            languages={"Python": 1},
            systems=["Python"],
        ),
        deterministic=DeterministicClassificationContext(
            categories=[TaskCategory.FRONTEND],
            complexity=Complexity.LOW,
            scope=ScopeSize.SMALL,
            risk=0.1,
        ),
    )
    result = GeminiProvider(config).classify(context)
    assert isinstance(result, ProviderClassification)
    assert 0 <= result.risk <= 1 and 0 <= result.confidence <= 1
    assert result.suggested_route in {Route.GEMINI, Route.GEMINI_TO_CODEX, Route.CODEX, Route.PLAN}
