"""Typed, data-only contracts for future coding-provider proposals."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from car.authorization import DEFAULT_SAFE_AUXILIARY_PATHS
from car.paths import normalize_repository_relative_path
from car.providers.models import ProviderErrorKind, RepositoryClassificationContext
from car.router.models import Route


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
    # The files supplied as text are deliberately not necessarily the complete
    # write-authorization universe (large repository benchmarks use a bounded
    # textual subset).  Empty preserves the historic selected-files contract.
    authorized_paths: tuple[str, ...] = ()
    authorization_summary: str | None = Field(default=None, max_length=1_000)
    constraints: list[str] = Field(default_factory=list)
    safe_auxiliary_paths: tuple[str, ...] = DEFAULT_SAFE_AUXILIARY_PATHS

    @field_validator("task")
    @classmethod
    def task_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("task must not be blank")
        return normalized

    @field_validator("authorized_paths")
    @classmethod
    def authorized_paths_must_be_repository_relative(
        cls, values: tuple[str, ...]
    ) -> tuple[str, ...]:
        normalized = tuple(_repository_relative_path(value) for value in values)
        if len(normalized) != len(set(normalized)):
            raise ValueError("authorized paths must be unique")
        return normalized

    @property
    def task_authorized_paths(self) -> tuple[str, ...]:
        """Return the authoritative task scope, falling back to supplied text files."""
        return self.authorized_paths or tuple(file.path for file in self.files)


class FileChangeOperation(StrEnum):
    MODIFY = "modify"
    CREATE = "create"


class ProposedFileChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    model_config = ConfigDict(extra="forbid")

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
    model: str | None = None
    attempted: bool
    succeeded: bool
    proposal: CodingProposal | None = None
    error_kind: ProviderErrorKind | None = None
    provider_http_status: int | None = Field(default=None, ge=100, le=599)
    provider_error_status: str | None = Field(default=None, max_length=64)
    provider_error_message: str | None = Field(default=None, max_length=500)
    usage: object | None = None

    @model_validator(mode="after")
    def result_fields_must_be_consistent(self) -> "CodingAttemptResult":
        if self.succeeded:
            if (
                not self.attempted
                or self.proposal is None
                or self.error_kind is not None
                or self.provider_http_status is not None
                or self.provider_error_status is not None
                or self.provider_error_message is not None
            ):
                raise ValueError("successful attempts require a proposal and no error")
        elif self.attempted:
            if self.proposal is not None or self.error_kind is None:
                raise ValueError("failed attempts require an error and no proposal")
        elif (
            self.proposal is not None
            or self.error_kind is not None
            or self.provider_http_status is not None
            or self.provider_error_status is not None
            or self.provider_error_message is not None
        ):
            raise ValueError("unattempted results cannot include a proposal or error")
        return self
