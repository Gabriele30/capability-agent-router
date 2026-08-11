import pytest
from pydantic import ValidationError

from car.config.models import CarConfig
from car.router.models import TaskRequest


def test_task_request_strips_description() -> None:
    request = TaskRequest(description="  Fix test  ")
    assert request.description == "Fix test"


def test_task_request_rejects_blank_description() -> None:
    with pytest.raises(ValidationError):
        TaskRequest(description="\t \n")


def test_legacy_config_defaults_controlled_write_to_disabled_without_runtime_consent() -> None:
    config = CarConfig.model_validate({"schema_version": 1, "default_mode": "auto"})

    assert config.schema_version == 4
    assert not config.codex_write.enabled
    persisted = config.model_dump()
    assert "codex_write" in persisted
    assert "authorized" not in persisted["codex_write"]
    assert "paths" not in persisted["codex_write"]
