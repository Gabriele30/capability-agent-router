"""Safe data contracts for the SWE-bench Verified adapter.

These contracts deliberately omit solution and evaluator fields. Raw dataset
records may be read only by an acquisition/evaluator boundary which constructs
``SWEbenchInstance`` from the public fields below.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SWEBENCH_DATASET = "SWE-bench/SWE-bench_Verified"
SWEBENCH_SPLIT = "test"
SAMPLE_PREFIX = "car-external-v1"
SAMPLE_ALGORITHM_VERSION = "swebench-verified-stratified-v1"


class SWEbenchInstance(BaseModel):
    """Only public, non-solution metadata needed before provider execution."""

    model_config = ConfigDict(extra="ignore")

    instance_id: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    base_commit: str = Field(min_length=1)
    problem_statement: str = Field(min_length=1)
    difficulty: str = Field(min_length=1)
    version: str | None = None

    @field_validator("instance_id", "repo", "base_commit", "problem_statement", "difficulty")
    @classmethod
    def non_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class SWEbenchProviderProjection(BaseModel):
    """The complete structured projection permitted to reach a provider."""

    model_config = ConfigDict(extra="forbid")

    instance_id: str
    repo: str
    base_commit: str
    task: str
    difficulty: str
    version: str | None = None


class SWEbenchSelectedInstance(BaseModel):
    """Tracked, reviewable sample identity without task text or solution data."""

    instance_id: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    base_commit: str = Field(min_length=1)
    difficulty: str = Field(min_length=1)
    version: str | None = None


class SWEbenchEvaluatorConfig(BaseModel):
    """Pinned evaluator identity; per-instance image digests are run metadata."""

    harness_revision: str = Field(min_length=40, max_length=40)
    docker_image_source: str = Field(min_length=1)
    minimum_free_disk_gib: int = Field(default=120, ge=1)

    @field_validator("harness_revision")
    @classmethod
    def revision_is_sha(cls, value: str) -> str:
        if any(character not in "0123456789abcdef" for character in value.lower()):
            raise ValueError("harness_revision must be a hexadecimal Git SHA")
        return value.lower()


class SWEbenchSampleSpec(BaseModel):
    """Canonical identity for the externally selected SWE-bench sample."""

    schema_version: Literal[1] = 1
    benchmark: Literal["swebench_verified"] = "swebench_verified"
    dataset: Literal["SWE-bench/SWE-bench_Verified"] = SWEBENCH_DATASET
    dataset_revision: str = Field(min_length=40, max_length=40)
    split: Literal["test"] = SWEBENCH_SPLIT
    sampling_prefix: Literal["car-external-v1"] = SAMPLE_PREFIX
    sampling_algorithm_version: Literal["swebench-verified-stratified-v1"] = (
        SAMPLE_ALGORITHM_VERSION
    )
    instances: tuple[SWEbenchSelectedInstance, ...] = Field(min_length=24, max_length=24)
    evaluator: SWEbenchEvaluatorConfig
    gemini_model: Literal["gemini-3.5-flash-lite"] = "gemini-3.5-flash-lite"
    codex_model: Literal["gpt-5.6-terra"] = "gpt-5.6-terra"
    codex_reasoning_effort: Literal["medium"] = "medium"
    sample_sha256: str = Field(min_length=64, max_length=64)

    @field_validator("dataset_revision")
    @classmethod
    def revision_is_sha(cls, value: str) -> str:
        if any(character not in "0123456789abcdef" for character in value.lower()):
            raise ValueError("dataset_revision must be a hexadecimal Git SHA")
        return value.lower()

    @model_validator(mode="after")
    def selected_instances_are_unique_and_hashed(self) -> SWEbenchSampleSpec:
        instance_ids = [instance.instance_id for instance in self.instances]
        if len(instance_ids) != len(set(instance_ids)):
            raise ValueError("selected instance IDs must be unique")
        if self.sample_sha256 != self.canonical_sha256():
            raise ValueError("sample_sha256 does not match the canonical sample identity")
        return self

    def canonical_payload(self) -> dict[str, object]:
        """Return semantic sample identity excluding its derived digest."""
        return self.model_dump(mode="json", exclude={"sample_sha256"})

    def canonical_sha256(self) -> str:
        payload = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sample_sha256(payload: dict[str, object]) -> str:
    """Hash a prospective spec payload before its ``sample_sha256`` is set."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
