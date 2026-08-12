"""Offline benchmark manifest, isolation, and strategy-runner infrastructure."""

from typing import TYPE_CHECKING, Any

from car.benchmark.manifest import load_manifest, manifest_sha256
from car.benchmark.models import BenchmarkCase, BenchmarkManifest, BenchmarkStrategy
from car.benchmark.workspace import BenchmarkWorkspaceSet

if TYPE_CHECKING:
    from car.benchmark.aggregation import (
        BenchmarkComparison,
        BenchmarkReport,
        BenchmarkStrategySummary,
    )
    from car.benchmark.executors import (
        BenchmarkExecutionDependencies,
        CARBenchmarkExecutor,
    )
    from car.benchmark.results import BenchmarkTaskResult
    from car.benchmark.runner import BenchmarkRunner

__all__ = [
    "BenchmarkCase",
    "BenchmarkComparison",
    "BenchmarkExecutionDependencies",
    "BenchmarkManifest",
    "BenchmarkStrategy",
    "BenchmarkWorkspaceSet",
    "BenchmarkRunner",
    "BenchmarkReport",
    "BenchmarkStrategySummary",
    "BenchmarkTaskResult",
    "CARBenchmarkExecutor",
    "load_manifest",
    "manifest_sha256",
]


def __getattr__(name: str) -> Any:
    """Load execution integrations lazily to keep manifest imports lightweight."""
    if name in {"BenchmarkExecutionDependencies", "CARBenchmarkExecutor"}:
        from car.benchmark.executors import (
            BenchmarkExecutionDependencies,
            CARBenchmarkExecutor,
        )

        return {
            "BenchmarkExecutionDependencies": BenchmarkExecutionDependencies,
            "CARBenchmarkExecutor": CARBenchmarkExecutor,
        }[name]
    if name in {"BenchmarkComparison", "BenchmarkReport", "BenchmarkStrategySummary"}:
        from car.benchmark.aggregation import (
            BenchmarkComparison,
            BenchmarkReport,
            BenchmarkStrategySummary,
        )

        return {
            "BenchmarkComparison": BenchmarkComparison,
            "BenchmarkReport": BenchmarkReport,
            "BenchmarkStrategySummary": BenchmarkStrategySummary,
        }[name]
    if name == "BenchmarkTaskResult":
        from car.benchmark.results import BenchmarkTaskResult

        return BenchmarkTaskResult
    if name == "BenchmarkRunner":
        from car.benchmark.runner import BenchmarkRunner

        return BenchmarkRunner
    raise AttributeError(name)
