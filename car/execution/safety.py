"""Safety classification for CAR-created command templates."""

from enum import StrEnum

from car.execution.models import ExecutionPlan


class SafetyLevel(StrEnum):
    SAFE = "safe"
    REVIEW_REQUIRED = "review_required"
    FORBIDDEN = "forbidden"


def classify_plan(plan: ExecutionPlan) -> SafetyLevel:
    """Allow only exact formatter/linter templates built by the L0 resolver."""
    for command in [*plan.commands, *plan.verification_commands]:
        executable, *arguments = command.args
        if plan.tool == "ruff":
            if executable != "ruff" or arguments[:1] not in (["format"], ["check"]):
                return SafetyLevel.FORBIDDEN
        else:
            return SafetyLevel.FORBIDDEN
    return SafetyLevel.SAFE
