import pytest
from pydantic import ValidationError

from car.router.models import TaskRequest


def test_task_request_strips_description() -> None:
    request = TaskRequest(description="  Fix test  ")
    assert request.description == "Fix test"


def test_task_request_rejects_blank_description() -> None:
    with pytest.raises(ValidationError):
        TaskRequest(description="\t \n")
