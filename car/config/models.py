"""Versioned local CAR configuration."""

from pydantic import BaseModel, ConfigDict, Field


class CarConfig(BaseModel):
    """Minimal configuration designed to grow with future milestones."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    default_mode: str = "auto"
