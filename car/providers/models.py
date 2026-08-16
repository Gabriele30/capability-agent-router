"""Provider-neutral domain models; no SDK types belong here."""

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from car.router.models import Complexity, Route, ScopeSize, TaskCategory


class ProviderStatus(StrEnum):
    AVAILABLE = "available"
    CONFIGURED = "configured"
    DISABLED = "disabled"
    NOT_CONFIGURED = "not_configured"
    MISSING_CREDENTIALS = "missing_credentials"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    SERVICE_ERROR = "service_error"
    INVALID_RESPONSE = "invalid_response"
    UNKNOWN_ERROR = "unknown_error"


class ProviderErrorKind(StrEnum):
    AUTHENTICATION_ERROR = "authentication_error"
    PERMISSION_DENIED = "permission_denied"
    INVALID_REQUEST = "invalid_request"
    MODEL_NOT_FOUND = "model_not_found"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    SERVICE_ERROR = "service_error"
    INVALID_RESPONSE = "invalid_response"
    UNKNOWN_ERROR = "unknown_error"


class ProviderError(BaseModel):
    kind: ProviderErrorKind
    message: str | None = Field(default=None, max_length=500)
    http_status: int | None = Field(default=None, ge=100, le=599)
    status: str | None = Field(default=None, max_length=64)


class ProviderCapabilities(BaseModel):
    supports_classification: bool = False
    supports_planning: bool = False
    supports_code_changes: bool = False


class ProviderHealth(BaseModel):
    status: ProviderStatus
    configured: bool = False
    detail: str | None = None
    model: str | None = None


class RepositoryClassificationContext(BaseModel):
    name: str
    branch: str | None = None
    dirty: bool
    languages: dict[str, int]
    systems: list[str]


class DeterministicClassificationContext(BaseModel):
    categories: list[TaskCategory]
    complexity: Complexity
    scope: ScopeSize
    risk: float = Field(ge=0.0, le=1.0)
    signals: list[str] = Field(default_factory=list)


class ClassificationContext(BaseModel):
    task: str
    repository: RepositoryClassificationContext
    deterministic: DeterministicClassificationContext
    candidate_paths: list[str] = Field(default_factory=list)

    @field_validator("candidate_paths")
    @classmethod
    def paths_must_be_relative(cls, paths: list[str]) -> list[str]:
        if any(path.startswith(("/", "\\")) or ":" in path for path in paths):
            raise ValueError("candidate paths must be repository-relative")
        return paths


class ProviderClassification(BaseModel):
    categories: list[TaskCategory]
    complexity: Complexity
    risk: float = Field(ge=0.0, le=1.0)
    scope: ScopeSize
    suggested_route: Route
    confidence: float = Field(ge=0.0, le=1.0)
    relevant_paths: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)

    @field_validator("suggested_route")
    @classmethod
    def l0_is_not_provider_selectable(cls, route: Route) -> Route:
        if route == Route.L0:
            raise ValueError("providers cannot suggest L0")
        return route
