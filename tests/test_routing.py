from pathlib import Path

import pytest

from car.config.models import CarConfig
from car.repository.scanner import scan_repository
from car.router.engine import DecisionEngine
from car.router.models import Route, TaskRequest, UserMode


def decide(task: str, repository: Path, mode: UserMode = UserMode.AUTO):
    return DecisionEngine().decide(TaskRequest(description=task), scan_repository(repository), mode)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (UserMode.AUTO, Route.GEMINI),
        (UserMode.GEMINI, Route.GEMINI),
        (UserMode.GEMINI_TO_CODEX, Route.GEMINI_TO_CODEX),
        (UserMode.CODEX, Route.CODEX),
        (UserMode.PLAN, Route.PLAN),
    ],
)
def test_user_modes(git_repository: Path, mode: UserMode, expected: Route) -> None:
    assert decide("Fix CSS spacing", git_repository, mode).route == expected


@pytest.mark.parametrize("task", ["Format src/app.py", "Run Ruff formatting", "lint --fix"])
def test_l0_candidates(git_repository: Path, task: str) -> None:
    assert decide(task, git_repository).route == Route.L0


def test_response_format_is_not_l0(git_repository: Path) -> None:
    assert decide("Change the API response format", git_repository).route != Route.L0


@pytest.mark.parametrize(
    "task",
    [
        "Fix CSS spacing in dashboard cards",
        "Update README installation example",
        "Fix Docker healthcheck",
    ],
)
def test_low_risk_tasks_route_to_gemini(git_repository: Path, task: str) -> None:
    assert decide(task, git_repository).route == Route.GEMINI


def test_parser_regression_routes_to_escalation(git_repository: Path) -> None:
    assert decide("Fix parser regression", git_repository).route == Route.GEMINI_TO_CODEX


@pytest.mark.parametrize(
    "task",
    [
        "Fix security vulnerability",
        "Fix authentication bypass",
        "Correct authorization permission check",
        "Change AES encryption handling",
        "Fix race condition in worker pool",
        "Fix memory safety issue",
        "Implement protocol state machine",
        "Run database migration",
        "Design new application architecture",
        "Deploy to production",
    ],
)
def test_hard_risk_tasks_route_to_codex(git_repository: Path, task: str) -> None:
    assert decide(task, git_repository).route == Route.CODEX


def test_dirty_repository_does_not_force_codex(git_repository: Path) -> None:
    (git_repository / "README.md").write_text("dirty\n", encoding="utf-8")
    assert decide("Fix CSS spacing", git_repository).route == Route.GEMINI


def test_hard_risk_overrides_l0(git_repository: Path) -> None:
    assert decide("Format this project authentication config", git_repository).route == Route.CODEX


def test_decision_serializes_as_json(git_repository: Path) -> None:
    payload = decide("Fix CSS spacing", git_repository).model_dump(mode="json")
    assert payload["route"] == "gemini"
    assert payload["risk"]["score"] <= 1


def test_v1_configuration_migrates_in_memory() -> None:
    config = CarConfig.model_validate(
        {"schema_version": 1, "default_mode": "code".replace("code", "codex")}
    )
    assert config.schema_version == 3
    assert config.default_mode == UserMode.CODEX
    assert config.routing_policy.max_gemini_risk == 0.35
