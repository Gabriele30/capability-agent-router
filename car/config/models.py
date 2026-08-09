"""Versioned local CAR configuration."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from car.router.models import RoutingPolicy, UserMode


class L0Config(BaseModel):
    enabled: bool = True
    max_files: int = Field(default=50, ge=1, le=500)
    command_timeout_seconds: int = Field(default=60, ge=1, le=600)


class CarConfig(BaseModel):
    """Minimal configuration designed to grow with future milestones."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=3, ge=1)
    default_mode: UserMode = UserMode.AUTO
    routing_policy: RoutingPolicy = Field(default_factory=RoutingPolicy)
    l0: L0Config = Field(default_factory=L0Config)

    @model_validator(mode="before")
    @classmethod
    def migrate_v1(cls, value: Any) -> Any:
        """Accept the Milestone 1 configuration without discarding user values."""
        if isinstance(value, dict) and value.get("schema_version", 1) < 3:
            migrated = dict(value)
            migrated["schema_version"] = 3
            migrated.setdefault("routing_policy", {})
            migrated.setdefault("l0", {})
            return migrated
        return value
