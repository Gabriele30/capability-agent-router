"""Opt-in live validation for the Gemini coding transport.

The test uses only synthetic context and stops at a structured proposal.
"""

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.live


def _live_config():
    from car.config.models import CarConfig

    path = Path.cwd() / ".car-context" / "config.json"
    return (
        CarConfig.model_validate_json(path.read_text(encoding="utf-8"))
        if path.exists()
        else CarConfig()
    )


def test_gemini_coding_live_transport() -> None:
    if os.environ.get("CAR_RUN_LIVE_GEMINI_CODING_TESTS") != "1":
        pytest.skip("Gemini coding live test skipped: dedicated opt-in unavailable.")

    from car.coding.gemini import GeminiCodingProvider
    from car.coding.models import CodingFileContext, CodingProposal, CodingTaskContext
    from car.providers.models import RepositoryClassificationContext
    from car.router.models import Route

    config = _live_config().providers.gemini
    if not config.enabled:
        pytest.skip("Gemini coding live test skipped: provider disabled.")
    if not config.model:
        pytest.skip("Gemini coding live test skipped: model not configured.")
    if not os.environ.get(config.api_key_env):
        pytest.skip("Gemini coding live test skipped: credentials unavailable.")

    context = CodingTaskContext(
        task="Update greet() so it returns 'Hello, <name>!'.",
        route=Route.GEMINI,
        repository=RepositoryClassificationContext(
            name="synthetic-example",
            branch="main",
            dirty=False,
            languages={"Python": 1},
            systems=["Python"],
        ),
        files=[
            CodingFileContext(
                path="example.py",
                content='def greet(name: str) -> str:\n    return "Hello " + name\n',
            )
        ],
        constraints=["Modify only example.py.", "Preserve the function signature."],
    )

    result = GeminiCodingProvider(config).propose(context)

    assert isinstance(result, CodingProposal)
    assert result.summary.strip()
    assert result.changes
    assert len({change.path for change in result.changes}) == len(result.changes)
    assert all(not change.path.startswith(("/", "\\")) for change in result.changes)


def test_coding_live_gate_is_independent_from_classification_gate(monkeypatch) -> None:
    monkeypatch.setenv("CAR_RUN_LIVE_GEMINI_TESTS", "1")
    monkeypatch.delenv("CAR_RUN_LIVE_GEMINI_CODING_TESTS", raising=False)

    if os.environ.get("CAR_RUN_LIVE_GEMINI_CODING_TESTS") != "1":
        pytest.skip("Gemini coding live test remains disabled without its dedicated opt-in.")
