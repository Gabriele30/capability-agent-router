"""Minimal provider-neutral contracts for future execution milestones."""

from typing import Protocol

from pydantic import BaseModel, Field


class ProviderCapabilities(BaseModel):
    supports_planning: bool = False
    supports_code_changes: bool = False


class ProviderHealth(BaseModel):
    available: bool
    detail: str | None = None


class AgentRequest(BaseModel):
    task: str = Field(min_length=1)


class AgentResponse(BaseModel):
    summary: str


class AgentProvider(Protocol):
    """Future providers must satisfy this contract without entering router core."""

    def capabilities(self) -> ProviderCapabilities: ...

    def health(self) -> ProviderHealth: ...
