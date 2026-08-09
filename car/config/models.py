"""Versioned local CAR configuration."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from car.router.models import RoutingPolicy, UserMode


class CarConfig(BaseModel):
    """Minimal configuration designed to grow with future milestones."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=2, ge=1)
    default_mode: UserMode = UserMode.AUTO
    routing_policy: RoutingPolicy = Field(default_factory=RoutingPolicy)

    @model_validator(mode="before")
    @classmethod
    def migrate_v1(cls, value: Any) -> Any:
        """Accept the Milestone 1 configuration without discarding user values."""
        if isinstance(value, dict) and value.get("schema_version", 1) == 1:
            migrated = dict(value)
            migrated["schema_version"] = 2
            migrated.setdefault("routing_policy", {})
            return migrated
        return value
