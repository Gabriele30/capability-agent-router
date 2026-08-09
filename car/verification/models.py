"""Verification result models."""

from enum import StrEnum

from pydantic import BaseModel, Field

from car.execution.models import CommandResult, CommandSpec


class VerificationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"


class VerificationPlan(BaseModel):
    commands: list[CommandSpec] = Field(min_length=1)


class VerificationResult(BaseModel):
    status: VerificationStatus
    checks: list[CommandResult] = Field(default_factory=list)
    message: str
