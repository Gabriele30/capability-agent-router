"""Manifest-level benchmark orchestration, kept separate from the CLI."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from car import __version__
from car.benchmark.aggregation import BenchmarkReport, aggregate_benchmark
from car.benchmark.manifest import manifest_sha256
from car.benchmark.models import BenchmarkManifest, BenchmarkRunMetadata, BenchmarkStrategy
from car.benchmark.runner import BenchmarkRunner
from car.economics.models import CostBasis
from car.economics.pricing import DEFAULT_PRICE_CATALOG


def run_manifest_benchmark(
    manifest: BenchmarkManifest,
    manifest_path: Path,
    strategies: tuple[BenchmarkStrategy, ...],
    runner: BenchmarkRunner,
    *,
    gemini_model: str | None = None,
    codex_model: str | None = None,
    codex_reasoning_effort: str | None = None,
) -> BenchmarkReport:
    if not strategies:
        raise ValueError("at least one benchmark strategy is required")
    metadata = BenchmarkRunMetadata(
        run_id=str(uuid4()),
        manifest_hash=manifest_sha256(manifest),
        car_version=__version__,
        started_at=datetime.now(UTC),
        strategies=strategies,
        price_catalog_version=DEFAULT_PRICE_CATALOG.version,
        price_catalog_verified_on=str(DEFAULT_PRICE_CATALOG.prices[0].verified_on),
        cost_basis=CostBasis.PUBLIC_API_LIST_PRICE.value,
        gemini_model=gemini_model,
        codex_model=codex_model,
        codex_reasoning_effort=codex_reasoning_effort,
    )
    results = tuple(
        item
        for case in manifest.cases
        for item in runner.run_case(
            case, (manifest_path.parent / case.fixture).resolve(), strategies
        )
    )
    return aggregate_benchmark(metadata, results)
