"""Models at the boundary between CLI task intake and future routing."""

from pydantic import BaseModel, Field, field_validator


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
