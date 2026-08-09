"""Provider-independent routing-domain models."""

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class Route(StrEnum):
    L0 = "l0"
    GEMINI = "gemini"
    GEMINI_TO_CODEX = "gemini_to_codex"
    CODEX = "codex"
    PLAN = "plan"


class UserMode(StrEnum):
    AUTO = "auto"
    GEMINI = "gemini"
    GEMINI_TO_CODEX = "gemini_to_codex"
    CODEX = "codex"
    PLAN = "plan"


class TaskCategory(StrEnum):
    FORMATTING = "formatting"
    LINTING = "linting"
    DOCUMENTATION = "documentation"
    FRONTEND = "frontend"
    CONFIGURATION = "configuration"
    DOCKER = "docker"
    TESTING = "testing"
    BUGFIX = "bugfix"
    REFACTORING = "refactoring"
    ARCHITECTURE = "architecture"
    SECURITY = "security"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    CRYPTOGRAPHY = "cryptography"
    CONCURRENCY = "concurrency"
    MEMORY_SAFETY = "memory_safety"
    PROTOCOL = "protocol"
    DATABASE_MIGRATION = "database_migration"
    DEPLOYMENT = "deployment"
    DEPENDENCY_CHANGE = "dependency_change"
    PUBLIC_API = "public_api"
    UNKNOWN = "unknown"


class Complexity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class ScopeSize(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    UNKNOWN = "unknown"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TaskRequest(BaseModel):
    """A validated task accepted by CAR."""

    description: str = Field(min_length=1, max_length=10_000)

    @field_validator("description")
    @classmethod
    def description_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Task description must not be empty.")
        return normalized


class ScopeEstimate(BaseModel):
    size: ScopeSize
    estimated_files_min: int | None = Field(default=None, ge=0)
    estimated_files_max: int | None = Field(default=None, ge=0)
    reasons: list[str] = Field(default_factory=list)


class RiskAssessment(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    level: RiskLevel
    indicators: list[str] = Field(default_factory=list)


class TaskAnalysis(BaseModel):
    task_text: str
    categories: list[TaskCategory]
    signals: list[str] = Field(default_factory=list)
    risk_indicators: list[str] = Field(default_factory=list)
    complexity_indicators: list[str] = Field(default_factory=list)
    possible_l0: bool = False
    repository_hints: list[str] = Field(default_factory=list)
    complexity: Complexity
    scope: ScopeEstimate


class RoutingPolicy(BaseModel):
    max_gemini_risk: float = Field(default=0.35, ge=0.0, le=1.0)
    direct_codex_risk: float = Field(default=0.75, ge=0.0, le=1.0)
    hard_codex_categories: set[TaskCategory] = Field(
        default_factory=lambda: {
            TaskCategory.SECURITY,
            TaskCategory.AUTHENTICATION,
            TaskCategory.AUTHORIZATION,
            TaskCategory.CRYPTOGRAPHY,
            TaskCategory.CONCURRENCY,
            TaskCategory.MEMORY_SAFETY,
            TaskCategory.PROTOCOL,
            TaskCategory.DATABASE_MIGRATION,
            TaskCategory.ARCHITECTURE,
        }
    )


class RoutingDecision(BaseModel):
    route: Route
    risk: RiskAssessment
    complexity: Complexity
    scope: ScopeEstimate
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: list[str]
    matched_rules: list[str]
    categories: list[TaskCategory]
