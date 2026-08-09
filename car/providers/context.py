"""Build secret-minimized provider context from existing deterministic facts."""

from car.providers.models import (
    ClassificationContext,
    DeterministicClassificationContext,
    RepositoryClassificationContext,
)
from car.repository.models import RepositoryState
from car.router.models import RiskAssessment, TaskAnalysis


def build_classification_context(
    task: str,
    repository: RepositoryState,
    analysis: TaskAnalysis,
    risk: RiskAssessment,
) -> ClassificationContext:
    """Return metadata only: no root path, file content, diff, or environment values."""
    return ClassificationContext(
        task=task,
        repository=RepositoryClassificationContext(
            name=repository.name,
            branch=repository.git.branch,
            dirty=repository.git.dirty,
            languages=repository.languages.counts,
            systems=repository.project_signals.systems,
        ),
        deterministic=DeterministicClassificationContext(
            categories=analysis.categories,
            complexity=analysis.complexity,
            scope=analysis.scope.size,
            risk=risk.score,
            signals=[*analysis.signals, *analysis.risk_indicators],
        ),
    )
