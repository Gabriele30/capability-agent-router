from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

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
    run_id: str
    manifest_hash: str
    car_version: str
    strategies: tuple[BenchmarkStrategy, ...]
