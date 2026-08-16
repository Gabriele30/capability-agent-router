"""Live, opt-in orchestration for one isolated SWE-bench Verified instance.

This boundary owns only disposable checkouts, prediction serialization, and the
qualified evaluator hand-off.  Provider execution remains delegated to the
existing benchmark executor and therefore retains CAR's normal validation,
application, verification, and telemetry controls.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Protocol
from uuid import uuid4

from car.benchmark.executors import CARBenchmarkExecutor
from car.benchmark.models import BenchmarkCase, BenchmarkStrategy
from car.benchmark.results import BenchmarkFailureKind, BenchmarkTaskResult
from car.benchmark.swebench.evaluator import (
    SWEbenchEvaluationRequest,
    SWEbenchEvaluationResult,
    SWEbenchEvaluationStatus,
    map_evaluation_result,
    run_qualified_evaluator,
)
from car.benchmark.swebench.models import SWEbenchInstance
from car.benchmark.swebench.projection import (
    explicit_repository_scope,
    project_provider_visible,
    validate_base_checkout,
)
from car.economics.pricing import ReferenceCostCalculator
from car.repository.git import run_git
from car.telemetry.models import FinalOutcome


class SWEbenchInstanceLoader(Protocol):
    """Loads one raw row at the evaluator boundary, never for provider use."""

    def __call__(self, instance_id: str) -> SWEbenchInstance: ...


@dataclass(frozen=True)
class SWEbenchLiveRun:
    """Privacy-safe outcome for one external strategy invocation."""

    result: BenchmarkTaskResult
    evaluator: SWEbenchEvaluationResult | None


def load_public_instance(instance_id: str) -> SWEbenchInstance:
    """Load only public task metadata from the pinned official dataset.

    ``datasets`` is intentionally imported lazily: ordinary CAR installs and
    all normal tests retain no Hugging Face dependency or network activity.
    Extra dataset fields are discarded immediately by ``SWEbenchInstance``.
    """
    try:
        from datasets import load_dataset  # type: ignore[import-not-found]
    except ImportError as error:  # pragma: no cover - exercised at live boundary
        raise RuntimeError(
            "SWE-bench live execution requires the optional datasets package"
        ) from error
    from car.benchmark.swebench.evaluator import QUALIFIED_DATASET_REVISION
    from car.benchmark.swebench.models import SWEBENCH_EVALUATOR_DATASET, SWEBENCH_SPLIT

    rows = load_dataset(
        SWEBENCH_EVALUATOR_DATASET,
        split=SWEBENCH_SPLIT,
        revision=QUALIFIED_DATASET_REVISION,
    )
    for row in rows:
        if row.get("instance_id") == instance_id:
            return SWEbenchInstance.model_validate(row)
    raise ValueError(f"SWE-bench instance was not found: {instance_id}")


class SWEbenchWorkspace:
    """CAR-owned disposable base and trusted candidate repositories."""

    def __init__(self, instance: SWEbenchInstance) -> None:
        self.instance = instance
        self.root = Path(tempfile.mkdtemp(prefix="car-swebench-"))
        self.baseline = self.root / "baseline"
        self.candidate = self.root / "candidate"

    def prepare(self) -> None:
        url = f"https://github.com/{self.instance.repo}.git"
        _run(("git", "clone", "--no-checkout", url, str(self.baseline)))
        _checkout_exact(self.baseline, self.instance.base_commit)
        validate_base_checkout(self.instance, self.baseline)
        _run(("git", "clone", "--no-checkout", str(self.baseline), str(self.candidate)))
        _checkout_exact(self.candidate, self.instance.base_commit)
        validate_base_checkout(self.instance, self.candidate)

    def cleanup(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root, onexc=self._make_writable_and_retry)

    def _make_writable_and_retry(self, function, path, error) -> None:
        """Retry only a permission failure under this CAR-owned temporary root."""
        target = Path(path).resolve()
        try:
            target.relative_to(self.root.resolve())
        except ValueError:
            raise error from None
        if not isinstance(error, PermissionError):
            raise error from None
        os.chmod(target, stat.S_IWRITE)
        function(path)


def run_swebench_instance(
    instance_id: str,
    strategy: BenchmarkStrategy,
    *,
    executor: CARBenchmarkExecutor,
    instance_loader: SWEbenchInstanceLoader = load_public_instance,
    verification: tuple[str, ...] = ("pytest",),
    evaluator_runner: Callable[
        [SWEbenchEvaluationRequest], SWEbenchEvaluationResult
    ] = run_qualified_evaluator,
) -> SWEbenchLiveRun:
    """Run one strategy through CAR's trusted pipeline and the native oracle.

    This is deliberately an explicit programmatic live boundary.  Callers must
    choose a strategy and may not provide a patch, gold data, or writable paths.
    """
    started = monotonic()
    instance = instance_loader(instance_id)
    projection = project_provider_visible(instance)
    if projection.instance_id != instance_id:
        raise ValueError("loaded SWE-bench instance identity mismatch")
    spaces = SWEbenchWorkspace(instance)
    try:
        spaces.prepare()
        scope = explicit_repository_scope(spaces.baseline)
        case = BenchmarkCase(
            id=instance.instance_id,
            category="swebench_verified",
            task=projection.task,
            fixture="external-owned",
            authorized_paths=scope,
            verification=verification,
        )
        outcome = executor.execute_outcome(case, spaces.candidate, strategy)
        result = _task_result(case, strategy, outcome, started)
        accepted = (*outcome.task_changed_paths, *outcome.auxiliary_changed_paths)
        patch = extract_candidate_patch(instance, spaces.candidate, accepted)
        if not patch:
            return SWEbenchLiveRun(
                result=result,
                evaluator=SWEbenchEvaluationResult(
                    status=SWEbenchEvaluationStatus.EMPTY_PATCH,
                    diagnostic="no accepted candidate delta",
                ),
            )
        prediction = spaces.root / "prediction.jsonl"
        write_prediction(prediction, instance.instance_id, strategy, patch)
        request = SWEbenchEvaluationRequest(
            evaluator_directory=spaces.root,
            predictions_path=prediction,
            run_id=f"car-swebench-{uuid4().hex}",
            instance_ids=(instance.instance_id,),
        )
        evaluator = evaluator_runner(request)
        mapping = map_evaluation_result(evaluator)
        result = result.model_copy(
            update={
                "verified_success": mapping.verified_success,
                "final_outcome": (
                    FinalOutcome.VERIFIED_SUCCESS
                    if mapping.verified_success
                    else FinalOutcome.FAILED
                ),
                "failure_kind": mapping.failure_kind,
                "failure_reason": None if mapping.verified_success else evaluator.diagnostic,
            }
        )
        return SWEbenchLiveRun(result=result, evaluator=evaluator)
    finally:
        spaces.cleanup()


def extract_candidate_patch(
    instance: SWEbenchInstance, candidate: Path, accepted_paths: tuple[str, ...]
) -> str:
    """Return a deterministic patch only for CAR-accepted candidate paths."""
    root = candidate.resolve()
    head = run_git(root, "rev-parse", "HEAD")
    if head is None or head.returncode != 0 or head.stdout.strip() != instance.base_commit:
        raise ValueError("candidate repository does not retain the pinned base commit")
    allowed = set(accepted_paths)
    status = run_git(root, "status", "--porcelain")
    if status is None or status.returncode != 0:
        raise ValueError("candidate repository is not usable")
    changed = tuple(line[3:] for line in status.stdout.splitlines() if len(line) >= 4)
    if any(path not in allowed for path in changed):
        raise ValueError("candidate contains changes outside the accepted proposal")
    if not changed:
        return ""
    tracked = run_git(
        root, "diff", "--binary", "--no-ext-diff", "--full-index", instance.base_commit
    )
    if tracked is None or tracked.returncode != 0:
        raise ValueError("could not derive candidate patch")
    untracked = run_git(root, "ls-files", "--others", "--exclude-standard")
    if untracked is None or untracked.returncode != 0:
        raise ValueError("could not inspect candidate files")
    patches = [tracked.stdout]
    for path in untracked.stdout.splitlines():
        if path not in allowed:
            raise ValueError("candidate contains an unaccepted created file")
        created = run_git(root, "diff", "--binary", "--no-index", "--", "/dev/null", path)
        if created is None or created.returncode not in (0, 1):
            raise ValueError("could not derive created-file patch")
        patches.append(created.stdout)
    return "".join(patches)


def write_prediction(path: Path, instance_id: str, strategy: BenchmarkStrategy, patch: str) -> None:
    """Write the minimal official prediction row inside disposable state."""
    if not patch:
        raise ValueError("empty model patches must not be serialized as predictions")
    row = {
        "instance_id": instance_id,
        "model_name_or_path": f"car-{strategy.value}",
        "model_patch": patch,
    }
    path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")


def _checkout_exact(path: Path, base_commit: str) -> None:
    _run(("git", "-C", str(path), "checkout", "--detach", base_commit))


def _run(args: tuple[str, ...]) -> None:
    completed = subprocess.run(args, capture_output=True, check=False, text=True, encoding="utf-8")
    if completed.returncode:
        raise RuntimeError("SWE-bench disposable checkout preparation failed")


def _task_result(case, strategy, outcome, started: float) -> BenchmarkTaskResult:
    costs = ReferenceCostCalculator()
    attempt_costs = tuple(
        costs.calculate(provider=item.provider, model=item.model, usage=item.usage)
        for item in outcome.telemetry.attempts
    )
    cost = costs.aggregate(attempt_costs)
    return BenchmarkTaskResult(
        case_id=case.id,
        strategy=strategy,
        verified_success=False,
        duration_ms=round((monotonic() - started) * 1000),
        attempt_count=len(outcome.telemetry.attempts),
        telemetry=outcome.telemetry,
        reference_cost=cost,
        cost_complete=cost.complete,
        final_outcome=outcome.telemetry.final_outcome,
        source_state=(
            outcome.telemetry.source_state.value if outcome.telemetry.source_state else None
        ),
        failure_kind=BenchmarkFailureKind.TASK_FAILED,
        failure_reason="native SWE-bench evaluation has not succeeded",
        rejected_paths=outcome.rejected_paths,
        patch_violations=outcome.patch_violations,
        task_changed_paths=outcome.task_changed_paths,
        auxiliary_changed_paths=outcome.auxiliary_changed_paths,
        pipeline_outcome=outcome.pipeline_outcome,
        provider_error_kind=outcome.provider_error_kind,
        provider_http_status=outcome.provider_http_status,
        provider_error_status=outcome.provider_error_status,
        provider_error_message=outcome.provider_error_message,
    )
