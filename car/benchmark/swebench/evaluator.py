"""Qualified Linux-side preflight and result mapping for SWE-bench Verified."""

from __future__ import annotations

import shutil
import subprocess
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from car.benchmark.results import BenchmarkFailureKind
from car.benchmark.swebench.models import SWEBENCH_EVALUATOR_DATASET, SWEBENCH_SPLIT

QUALIFIED_DATASET_REVISION = "c104f840cc67f8b6eec6f759ebc8b2693d585d4a"
QUALIFIED_SWEBENCH_VERSION = "4.1.0"
QUALIFIED_WSL_DISTRIBUTION = "Ubuntu-24.04"
QUALIFIED_LINUX_ARCHITECTURE = "x86_64"
QUALIFIED_DOCKER_ARCHITECTURE = "amd64"
QUALIFIED_MAX_WORKERS = 1


class SWEbenchEvaluationStatus(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    EMPTY_PATCH = "empty_patch"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"


class SWEbenchEvaluationResult(BaseModel):
    """Content-free result emitted by the trusted evaluator boundary."""

    status: SWEbenchEvaluationStatus
    diagnostic: str = Field(min_length=1, max_length=256)
    image_digest: str | None = None


class SWEbenchEvaluationRequest(BaseModel):
    """Runtime-only invocation details for the qualified Linux evaluator.

    The evaluator runs only through WSL Linux userland. Its prediction file is
    owned by the trusted evaluator boundary and this contract has no patch,
    test-patch, source, or provider data.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    evaluator_directory: Path
    predictions_path: Path
    run_id: str = Field(min_length=1, max_length=128)
    instance_ids: tuple[str, ...] = Field(min_length=1)
    dataset: str = SWEBENCH_EVALUATOR_DATASET
    dataset_revision: str = QUALIFIED_DATASET_REVISION
    split: str = SWEBENCH_SPLIT
    swebench_version: str = QUALIFIED_SWEBENCH_VERSION
    wsl_distribution: str = QUALIFIED_WSL_DISTRIBUTION
    linux_python: str = "python3"
    max_workers: int = Field(default=QUALIFIED_MAX_WORKERS, ge=1, le=QUALIFIED_MAX_WORKERS)

    @field_validator("instance_ids")
    @classmethod
    def unique_instance_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("instance IDs must be unique")
        return values

    def command(self) -> tuple[str, ...]:
        """Build a shell-free WSL command; do not execute it here."""
        return (
            "wsl.exe",
            "-d",
            self.wsl_distribution,
            "--",
            self.linux_python,
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
    if result.status in {SWEbenchEvaluationStatus.UNRESOLVED, SWEbenchEvaluationStatus.EMPTY_PATCH}:
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
    linux_execution: bool
    docker_available: bool
    linux_containers: bool
    architecture_compatible: bool
    swebench_version: str | None = None
    swebench_version_compatible: bool
    runtime_contract_valid: bool
    dataset_contract_valid: bool
    max_workers_policy_valid: bool
    free_disk_gib: int
    required_disk_gib: int
    ready: bool
    messages: tuple[str, ...] = ()


def check_preflight(
    cache_directory: Path,
    *,
    request: SWEbenchEvaluationRequest | None = None,
    required_disk_gib: int = 120,
    command_runner: DockerCommandRunner | None = None,
) -> SWEbenchPreflight:
    """Fail closed unless the qualified WSL/Linux evaluator contract is available."""
    messages: list[str] = []
    request = request or SWEbenchEvaluationRequest(
        evaluator_directory=cache_directory,
        predictions_path=cache_directory / "evaluator-owned-predictions.jsonl",
        run_id="preflight",
        instance_ids=("preflight",),
    )
    runner = command_runner or run_docker_info
    wsl_available = shutil.which("wsl.exe") is not None
    linux_execution = False
    architecture_compatible = False
    swebench_version: str | None = None
    docker_available = False
    linux_containers = False
    if not wsl_available:
        messages.append("WSL is unavailable; Windows Python evaluation is unsupported")
    else:
        prefix = ["wsl.exe", "-d", request.wsl_distribution, "--"]
        linux_code, linux_stdout, _ = runner([*prefix, "uname", "-s"])
        linux_execution = linux_code == 0 and linux_stdout.strip() == "Linux"
        architecture_code, architecture_stdout, _ = runner([*prefix, "uname", "-m"])
        architecture_compatible = (
            architecture_code == 0 and architecture_stdout.strip() == QUALIFIED_LINUX_ARCHITECTURE
        )
        version_code, version_stdout, _ = runner(
            [
                *prefix,
                request.linux_python,
                "-c",
                "import importlib.metadata; print(importlib.metadata.version('swebench'))",
            ]
        )
        swebench_version = version_stdout.strip() if version_code == 0 else None
        docker_code, docker_stdout, _ = runner(
            [*prefix, "docker", "info", "--format", "{{.OSType}} {{.Architecture}}"]
        )
        docker_available = docker_code == 0
        docker_platform = docker_stdout.strip().lower().split()
        linux_containers = docker_available and docker_platform in (
            ["linux", QUALIFIED_DOCKER_ARCHITECTURE],
            ["linux", QUALIFIED_LINUX_ARCHITECTURE],
        )

    if not linux_execution:
        messages.append("Qualified evaluator must run from Linux userland")
    if not architecture_compatible:
        messages.append("SWE-bench requires an x86_64-compatible Linux container host")
    runtime_contract_valid = (
        request.swebench_version == QUALIFIED_SWEBENCH_VERSION
        and request.wsl_distribution == QUALIFIED_WSL_DISTRIBUTION
    )
    if not runtime_contract_valid:
        messages.append("The qualified WSL runtime contract is invalid")
    if swebench_version != QUALIFIED_SWEBENCH_VERSION:
        messages.append("Qualified swebench==4.1.0 runtime is unavailable")
    if not docker_available:
        messages.append("Docker is unavailable through the qualified WSL environment")
    elif not linux_containers:
        messages.append("Docker must provide Linux amd64 containers through WSL")
    dataset_contract_valid = (
        request.dataset == SWEBENCH_EVALUATOR_DATASET
        and request.dataset_revision == QUALIFIED_DATASET_REVISION
        and request.split == SWEBENCH_SPLIT
    )
    if not dataset_contract_valid:
        messages.append("The qualified official dataset identity or revision is invalid")
    max_workers_policy_valid = request.max_workers == QUALIFIED_MAX_WORKERS
    if not max_workers_policy_valid:
        messages.append("Qualified local resource policy requires max_workers=1")
    usage = shutil.disk_usage(cache_directory)
    free_disk_gib = usage.free // (1024**3)
    if free_disk_gib < required_disk_gib:
        messages.append("Available disk is below the SWE-bench planning minimum")
    ready = (
        linux_execution
        and docker_available
        and linux_containers
        and architecture_compatible
        and runtime_contract_valid
        and swebench_version == QUALIFIED_SWEBENCH_VERSION
        and dataset_contract_valid
        and max_workers_policy_valid
        and free_disk_gib >= required_disk_gib
    )
    return SWEbenchPreflight(
        linux_execution=linux_execution,
        docker_available=docker_available,
        linux_containers=linux_containers,
        architecture_compatible=architecture_compatible,
        swebench_version=swebench_version,
        swebench_version_compatible=swebench_version == QUALIFIED_SWEBENCH_VERSION,
        runtime_contract_valid=runtime_contract_valid,
        dataset_contract_valid=dataset_contract_valid,
        max_workers_policy_valid=max_workers_policy_valid,
        free_disk_gib=free_disk_gib,
        required_disk_gib=required_disk_gib,
        ready=ready,
        messages=tuple(messages),
    )


def run_docker_info(args: list[str]) -> tuple[int, str, str]:
    """Small injectable runner used only by an explicit preflight invocation."""
    completed = subprocess.run(args, capture_output=True, check=False, text=True, encoding="utf-8")
    return completed.returncode, completed.stdout, completed.stderr
