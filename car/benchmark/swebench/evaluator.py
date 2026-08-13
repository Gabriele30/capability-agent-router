"""Offline preflight and result mapping for the official SWE-bench evaluator."""

from __future__ import annotations

import platform
import shutil
import subprocess
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from car.benchmark.results import BenchmarkFailureKind
from car.benchmark.swebench.models import SWEBENCH_DATASET, SWEBENCH_SPLIT


class SWEbenchEvaluationStatus(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"


class SWEbenchEvaluationResult(BaseModel):
    """Content-free result emitted by the trusted evaluator boundary."""

    status: SWEbenchEvaluationStatus
    diagnostic: str = Field(min_length=1, max_length=256)
    image_digest: str | None = None


class SWEbenchEvaluationRequest(BaseModel):
    """Runtime-only invocation details for a pinned official harness checkout.

    The evaluator accepts a prediction file owned by the trusted evaluator
    boundary. This contract has no patch, test-patch, source, or provider data.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    harness_directory: Path
    predictions_path: Path
    run_id: str = Field(min_length=1, max_length=128)
    instance_ids: tuple[str, ...] = Field(min_length=1)
    dataset: str = SWEBENCH_DATASET
    split: str = SWEBENCH_SPLIT
    max_workers: int = Field(default=1, ge=1, le=8)

    @field_validator("instance_ids")
    @classmethod
    def unique_instance_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("instance IDs must be unique")
        return values

    def command(self) -> tuple[str, ...]:
        """Build a shell-free official evaluator command; do not execute it here."""
        return (
            "python",
            "-m",
            "swebench.harness.run_evaluation",
            "--dataset_name",
            self.dataset,
            "--split",
            self.split,
            "--predictions_path",
            str(self.predictions_path),
            "--max_workers",
            str(self.max_workers),
            "--run_id",
            self.run_id,
            "--instance_ids",
            *self.instance_ids,
        )


class SWEbenchEvaluationMapping(BaseModel):
    verified_success: bool
    failure_kind: BenchmarkFailureKind | None = None
    infrastructure_failure: bool = False


def map_evaluation_result(result: SWEbenchEvaluationResult) -> SWEbenchEvaluationMapping:
    """Keep evaluator setup failures distinct from unresolved model attempts."""
    if result.status == SWEbenchEvaluationStatus.RESOLVED:
        return SWEbenchEvaluationMapping(verified_success=True)
    if result.status == SWEbenchEvaluationStatus.UNRESOLVED:
        return SWEbenchEvaluationMapping(
            verified_success=False,
            failure_kind=BenchmarkFailureKind.TASK_FAILED,
        )
    return SWEbenchEvaluationMapping(
        verified_success=False,
        failure_kind=BenchmarkFailureKind.EXECUTION_FAILED,
        infrastructure_failure=True,
    )


class DockerCommandRunner(Protocol):
    def __call__(self, args: list[str]) -> tuple[int, str, str]: ...


class SWEbenchPreflight(BaseModel):
    docker_available: bool
    linux_containers: bool
    architecture_compatible: bool
    free_disk_gib: int
    required_disk_gib: int
    ready: bool
    messages: tuple[str, ...] = ()


def check_preflight(
    cache_directory: Path,
    *,
    required_disk_gib: int = 120,
    command_runner: DockerCommandRunner | None = None,
) -> SWEbenchPreflight:
    """Inspect Docker prerequisites without changing host configuration."""
    messages: list[str] = []
    docker_available = shutil.which("docker") is not None
    linux_containers = False
    if docker_available and command_runner is not None:
        return_code, stdout, _stderr = command_runner(["docker", "info", "--format", "{{.OSType}}"])
        linux_containers = return_code == 0 and stdout.strip().lower() == "linux"
    elif docker_available:
        messages.append("Docker was found but container mode was not checked")
    else:
        messages.append("Docker is unavailable")

    machine = platform.machine().lower()
    architecture_compatible = machine in {"amd64", "x86_64"}
    if not architecture_compatible:
        messages.append("SWE-bench requires an x86_64-compatible Linux container host")
    usage = shutil.disk_usage(cache_directory)
    free_disk_gib = usage.free // (1024**3)
    if free_disk_gib < required_disk_gib:
        messages.append("Available disk is below the SWE-bench planning minimum")
    ready = (
        docker_available
        and linux_containers
        and architecture_compatible
        and free_disk_gib >= required_disk_gib
    )
    return SWEbenchPreflight(
        docker_available=docker_available,
        linux_containers=linux_containers,
        architecture_compatible=architecture_compatible,
        free_disk_gib=free_disk_gib,
        required_disk_gib=required_disk_gib,
        ready=ready,
        messages=tuple(messages),
    )


def run_docker_info(args: list[str]) -> tuple[int, str, str]:
    """Small injectable runner used only by an explicit preflight invocation."""
    completed = subprocess.run(args, capture_output=True, check=False, text=True, encoding="utf-8")
    return completed.returncode, completed.stdout, completed.stderr
