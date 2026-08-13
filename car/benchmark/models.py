from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

from car.authorization import DEFAULT_SAFE_AUXILIARY_PATHS
from car.benchmark.hidden_oracle import HIDDEN_ORACLE_IDS
from car.coding.models import normalize_repository_relative_path


class BenchmarkStrategy(StrEnum):
    GEMINI_ONLY = "gemini_only"
    CODEX_ONLY = "codex_only"
    CAR = "car"


class BenchmarkCase(BaseModel):
    id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    task: str = Field(min_length=1)
    fixture: str = Field(min_length=1)
    authorized_paths: tuple[str, ...] = Field(min_length=1)
    verification: tuple[str, ...] = Field(min_length=1)
    hidden_verification: str | None = None
    description: str | None = None
    tags: tuple[str, ...] = ()

    @field_validator("id", "category", "task", "fixture")
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value.strip()

    @field_validator("authorized_paths")
    @classmethod
    def safe_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(normalize_repository_relative_path(value) for value in values)

    @field_validator("hidden_verification")
    @classmethod
    def supported_hidden_verification(cls, value: str | None) -> str | None:
        if value is not None and value not in HIDDEN_ORACLE_IDS:
            raise ValueError("unsupported hidden verification oracle")
        return value


class BenchmarkManifest(BaseModel):
    version: int = 1
    cases: tuple[BenchmarkCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def supported_and_unique(self) -> "BenchmarkManifest":
        if self.version != 1:
            raise ValueError("unsupported benchmark manifest version")
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("benchmark case IDs must be unique")
        return self


class BenchmarkRunMetadata(BaseModel):
    schema_version: int = 1
    run_id: str
    manifest_hash: str
    car_version: str
    started_at: datetime
    strategies: tuple[BenchmarkStrategy, ...]
    price_catalog_version: str
    price_catalog_verified_on: str
    cost_basis: str
    gemini_model: str | None = None
    codex_model: str | None = None
    codex_reasoning_effort: str | None = None
    safe_auxiliary_paths: tuple[str, ...] = DEFAULT_SAFE_AUXILIARY_PATHS
