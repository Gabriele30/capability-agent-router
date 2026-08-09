"""Transparent, deterministic task analysis used by the routing engine."""

from __future__ import annotations

import re

from car.repository.models import RepositoryState
from car.router.models import Complexity, ScopeEstimate, ScopeSize, TaskAnalysis, TaskCategory


def _contains(text: str, *phrases: str) -> bool:
    return any(phrase in text for phrase in phrases)


def _is_l0_candidate(text: str) -> bool:
    """Recognize only unambiguous formatter or lint-fix requests."""
    if "response format" in text or "api format" in text:
        return False
    return bool(
        re.search(r"\bformat\s+(?:[\w./\\-]+\.[\w]+|this project|all files)\b", text)
        or re.search(r"\brun\s+(?:ruff\s+)?format(?:ting)?\b", text)
        or "formatting only" in text
        or "lint --fix" in text
        or "run lint fix" in text
        or bool(re.search(r"\brun\s+ruff\s+lint\s+fix\b", text))
        or bool(re.search(r"\bfix\s+ruff\s+violations\s+in\s+[\w./\\-]+", text))
        or "ruff format" in text
    )


def _categories(text: str, possible_l0: bool) -> tuple[list[TaskCategory], list[str]]:
    categories: list[TaskCategory] = []
    signals: list[str] = []
    rules: tuple[tuple[TaskCategory, tuple[str, ...]], ...] = (
        (TaskCategory.DOCUMENTATION, ("readme", "documentation", "docs")),
        (TaskCategory.FRONTEND, ("css", "spacing", "frontend", "ui", "dashboard")),
        (TaskCategory.CONFIGURATION, ("configuration", "config", "settings")),
        (TaskCategory.DOCKER, ("docker", "healthcheck", "container")),
        (TaskCategory.TESTING, ("test", "pytest", "regression")),
        (TaskCategory.BUGFIX, ("fix", "bug", "regression")),
        (TaskCategory.REFACTORING, ("refactor", "restructure", "cleanup")),
        (TaskCategory.ARCHITECTURE, ("architecture", "architectural", "redesign")),
        (TaskCategory.SECURITY, ("security", "vulnerability", "bypass")),
        (TaskCategory.AUTHENTICATION, ("authentication", "authenticate", "login", "auth logic")),
        (TaskCategory.AUTHORIZATION, ("authorization", "permission", "access control")),
        (TaskCategory.CRYPTOGRAPHY, ("cryptography", "encryption", " aes", "crypto")),
        (TaskCategory.CONCURRENCY, ("race condition", "concurrency", "thread", "worker pool")),
        (TaskCategory.MEMORY_SAFETY, ("memory safety", "use-after-free", "buffer overflow")),
        (TaskCategory.PROTOCOL, ("protocol", "state machine")),
        (
            TaskCategory.DATABASE_MIGRATION,
            ("database migration", "schema migration", "migrate database"),
        ),
        (TaskCategory.DEPENDENCY_CHANGE, ("dependency", "dependencies", "upgrade package")),
        (TaskCategory.PUBLIC_API, ("public api", "api contract", "breaking api")),
    )
    for category, phrases in rules:
        matched = next((phrase for phrase in phrases if phrase in text), None)
        if matched:
            categories.append(category)
            signals.append(matched)
    if _contains(text, "deploy", "deployment") and _contains(text, "production", "prod", "release"):
        categories.append(TaskCategory.DEPLOYMENT)
        signals.append("production deployment")
    if possible_l0:
        categories.append(TaskCategory.FORMATTING if "format" in text else TaskCategory.LINTING)
        signals.append("deterministic formatter/lint command")
    if not categories:
        categories.append(TaskCategory.UNKNOWN)
    return list(dict.fromkeys(categories)), signals


def _complexity(
    categories: list[TaskCategory], text: str, possible_l0: bool
) -> tuple[Complexity, list[str]]:
    high_categories = {
        TaskCategory.ARCHITECTURE,
        TaskCategory.CONCURRENCY,
        TaskCategory.SECURITY,
        TaskCategory.AUTHENTICATION,
        TaskCategory.AUTHORIZATION,
        TaskCategory.CRYPTOGRAPHY,
        TaskCategory.MEMORY_SAFETY,
        TaskCategory.PROTOCOL,
        TaskCategory.DATABASE_MIGRATION,
    }
    if possible_l0:
        return Complexity.LOW, ["unambiguous deterministic maintenance request"]
    if high_categories.intersection(categories) or "several" in text or "multiple" in text:
        return Complexity.HIGH, ["high-risk domain or multi-module wording"]
    if TaskCategory.UNKNOWN in categories:
        return Complexity.UNKNOWN, ["no clear task category"]
    localized_categories = {
        TaskCategory.FRONTEND,
        TaskCategory.DOCUMENTATION,
        TaskCategory.DOCKER,
        TaskCategory.CONFIGURATION,
    }
    if localized_categories.intersection(categories) and TaskCategory.REFACTORING not in categories:
        return Complexity.LOW, ["localized category with no high-risk signal"]
    if TaskCategory.BUGFIX in categories or TaskCategory.REFACTORING in categories:
        return Complexity.MEDIUM, ["behavioral change requires investigation"]
    return Complexity.LOW, ["localized category with no high-risk signal"]


def _scope(categories: list[TaskCategory], complexity: Complexity, text: str) -> ScopeEstimate:
    if complexity == Complexity.HIGH or "several" in text or "multiple" in text:
        return ScopeEstimate(
            size=ScopeSize.LARGE, reasons=["high-complexity or multi-module wording"]
        )
    if complexity == Complexity.UNKNOWN:
        return ScopeEstimate(size=ScopeSize.UNKNOWN, reasons=["insufficient scope evidence"])
    if complexity == Complexity.MEDIUM:
        return ScopeEstimate(
            size=ScopeSize.MEDIUM, reasons=["behavioral change may span implementation and tests"]
        )
    return ScopeEstimate(
        size=ScopeSize.SMALL,
        estimated_files_min=1,
        estimated_files_max=3,
        reasons=["localized maintenance request"],
    )


def _repository_hints(repository: RepositoryState, categories: list[TaskCategory]) -> list[str]:
    category_systems = {
        TaskCategory.DOCKER: "Docker",
        TaskCategory.FRONTEND: "Node.js",
        TaskCategory.DOCUMENTATION: "Python",
    }
    return [
        f"repository reports {system}"
        for category, system in category_systems.items()
        if category in categories and system in repository.project_signals.systems
    ]


def analyze_task(task_text: str, repository: RepositoryState) -> TaskAnalysis:
    """Classify obvious textual signals without attempting natural-language understanding."""
    normalized = task_text.lower().strip()
    possible_l0 = _is_l0_candidate(normalized)
    categories, signals = _categories(normalized, possible_l0)
    complexity, complexity_indicators = _complexity(categories, normalized, possible_l0)
    scope = _scope(categories, complexity, normalized)
    risk_indicators = [
        category.value for category in categories if category != TaskCategory.UNKNOWN
    ]
    if TaskCategory.UNKNOWN in categories:
        risk_indicators.append("ambiguous task")
    return TaskAnalysis(
        task_text=task_text,
        categories=categories,
        signals=signals,
        risk_indicators=risk_indicators,
        complexity_indicators=complexity_indicators,
        possible_l0=possible_l0,
        repository_hints=_repository_hints(repository, categories),
        complexity=complexity,
        scope=scope,
    )
