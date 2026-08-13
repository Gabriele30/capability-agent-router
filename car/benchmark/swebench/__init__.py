"""Pinned, provider-free SWE-bench Verified benchmark adapter contracts."""

from car.benchmark.swebench.evaluator import (
    SWEbenchEvaluationRequest,
    SWEbenchEvaluationResult,
    SWEbenchEvaluationStatus,
    SWEbenchPreflight,
    map_evaluation_result,
)
from car.benchmark.swebench.manifest import load_sample_spec
from car.benchmark.swebench.models import (
    SWEbenchInstance,
    SWEbenchProviderProjection,
    SWEbenchSampleSpec,
)
from car.benchmark.swebench.projection import (
    EVALUATOR_ONLY_FIELDS,
    explicit_repository_scope,
    project_provider_visible,
    validate_base_checkout,
)
from car.benchmark.swebench.selection import select_verified_sample

__all__ = [
    "EVALUATOR_ONLY_FIELDS",
    "SWEbenchEvaluationResult",
    "SWEbenchEvaluationRequest",
    "SWEbenchEvaluationStatus",
    "SWEbenchInstance",
    "SWEbenchPreflight",
    "SWEbenchProviderProjection",
    "SWEbenchSampleSpec",
    "explicit_repository_scope",
    "map_evaluation_result",
    "load_sample_spec",
    "project_provider_visible",
    "select_verified_sample",
    "validate_base_checkout",
]
