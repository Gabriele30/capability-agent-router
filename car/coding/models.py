"""Typed, data-only contracts for future coding-provider proposals."""

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

from car.providers.models import ProviderErrorKind, RepositoryClassificationContext
from car.router.models import Route


def normalize_repository_relative_path(value: str) -> str:
    """Normalize and validate a repository-relative path without filesystem access."""
    normalized = value.replace("\\", "/")
    if not normalized or normalized.startswith("/") or ":" in normalized:
        raise ValueError("path must be repository-relative")
    if any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise ValueError(
            "path must not contain empty, current-directory, or parent-directory segments"
        )
    return normalized


def _repository_relative_path(value: str) -> str:
    """Backward-compatible internal alias for the canonical path validator."""
    return normalize_repository_relative_path(value)


class CodingFileContext(BaseModel):
    path: str
    content: str

    @field_validator("path")
    @classmethod
    def path_must_be_repository_relative(cls, value: str) -> str:
        return _repository_relative_path(value)


class CodingTaskContext(BaseModel):
    task: str = Field(min_length=1, max_length=10_000)
    route: Route
    repository: RepositoryClassificationContext
    files: list[CodingFileContext] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)

    @field_validator("task")
    @classmethod
    def task_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("task must not be blank")
        return normalized


class FileChangeOperation(StrEnum):
    MODIFY = "modify"
    CREATE = "create"


class ProposedFileChange(BaseModel):
    path: str
    operation: FileChangeOperation
    patch: str

    @field_validator("path")
    @classmethod
    def path_must_be_repository_relative(cls, value: str) -> str:
        return _repository_relative_path(value)

    @field_validator("patch")
    @classmethod
    def patch_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("patch must not be blank")
        return value


class CodingProposal(BaseModel):
    summary: str
    changes: list[ProposedFileChange] = Field(min_length=1)
    reasons: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)

    @field_validator("summary")
    @classmethod
    def summary_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("summary must not be blank")
        return normalized

    @model_validator(mode="after")
    def paths_must_be_unique(self) -> "CodingProposal":
        paths = [change.path for change in self.changes]
        if len(paths) != len(set(paths)):
            raise ValueError("each proposed file path may appear only once")
        return self


class CodingExecutionPolicy(BaseModel):
    max_files_per_proposal: int = Field(default=10, ge=1, le=100)
    allow_create_files: bool = True
    allow_modify_files: bool = True


class CodingAttemptResult(BaseModel):
    provider: str = Field(min_length=1)
    attempted: bool
    succeeded: bool
    proposal: CodingProposal | None = None
    error_kind: ProviderErrorKind | None = None

    @model_validator(mode="after")
    def result_fields_must_be_consistent(self) -> "CodingAttemptResult":
        if self.succeeded:
            if not self.attempted or self.proposal is None or self.error_kind is not None:
                raise ValueError("successful attempts require a proposal and no error")
        elif self.attempted:
            if self.proposal is not None or self.error_kind is None:
                raise ValueError("failed attempts require an error and no proposal")
        elif self.proposal is not None or self.error_kind is not None:
            raise ValueError("unattempted results cannot include a proposal or error")
        return self
