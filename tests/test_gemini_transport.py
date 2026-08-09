import json

import pytest

from car.providers.gemini import GeminiProvider, GeminiProviderConfig
from car.providers.models import (
    ClassificationContext,
    DeterministicClassificationContext,
    ProviderClassification,
    RepositoryClassificationContext,
)
from car.router.models import Complexity, ScopeSize, TaskCategory


class Response:
    def __init__(self, output_text):
        self.output_text = output_text


class FakeError(Exception):
    def __init__(self, code):
        self.code = code
        self.message = "temporary failure"


class Interactions:
    def __init__(self, response):
        self.response, self.calls = response, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class Client:
    def __init__(self, response):
        self.interactions = Interactions(response)


def context(task="Fix CSS spacing"):
    return ClassificationContext(
        task=task,
        repository=RepositoryClassificationContext(
            name="repo", branch="main", dirty=False, languages={"Python": 2}, systems=["Python"]
        ),
        deterministic=DeterministicClassificationContext(
            categories=[TaskCategory.FRONTEND],
            complexity=Complexity.LOW,
            scope=ScopeSize.SMALL,
            risk=0.1,
        ),
    )


def payload(**updates):
    data = {
        "categories": ["frontend"],
        "complexity": "low",
        "risk": 0.1,
        "scope": "small",
        "suggested_route": "gemini",
        "confidence": 0.9,
        "relevant_paths": ["car/router/engine.py"],
        "reasons": ["local"],
        "uncertainties": [],
    }
    data.update(updates)
    return json.dumps(data)


def provider(response):
    client = Client(response)
    instance = GeminiProvider(
        GeminiProviderConfig(enabled=True, model="configured-model"),
        {"GEMINI_API_KEY": "super-secret-test-key"},
        lambda _: client,
    )
    return instance, client


def test_structured_transport_request_and_privacy():
    instance, client = provider(
        Response(payload(relevant_paths=["car/router/engine.py", "../x", ".env", "credentials/x"]))
    )
    result = instance.classify(context())
    assert result.relevant_paths == ["car/router/engine.py"]
    assert len(client.interactions.calls) == 1
    call = client.interactions.calls[0]
    assert call["model"] == "configured-model" and call["store"] is False
    assert call["response_format"][0]["schema"] == ProviderClassification.model_json_schema()
    assert (
        "CLASSIFIER INSTRUCTIONS" in call["input"]
        and "UNTRUSTED CLASSIFICATION DATA" in call["input"]
    )
    assert "super-secret-test-key" not in call["input"]


@pytest.mark.parametrize(
    "output",
    [
        None,
        "",
        "bad",
        payload(risk=1.5),
        payload(confidence=-1),
        payload(suggested_route="l0"),
        payload(categories=["bad"]),
    ],
)
def test_invalid_response_is_normalized(output):
    instance, _ = provider(Response(output))
    with pytest.raises(RuntimeError, match="invalid_response"):
        instance.classify(context())


def test_configuration_and_request_failures_make_no_or_one_call():
    disabled, client = provider(Response(payload()))
    disabled.config.enabled = False
    with pytest.raises(RuntimeError, match="disabled"):
        disabled.classify(context())
    assert not client.interactions.calls
    instance, client = provider(Exception("network"))
    with pytest.raises(RuntimeError, match="unknown_error"):
        instance.classify(context())
    assert len(client.interactions.calls) == 1


def test_retry_service_error_then_success():
    client = Client(Response(payload()))
    client.interactions.response = [FakeError(503), Response(payload())]
    original = client.interactions.create

    def create(**kwargs):
        client.interactions.calls.append(kwargs)
        item = client.interactions.response.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client.interactions.create = create
    delays = []
    instance = GeminiProvider(
        GeminiProviderConfig(enabled=True, model="configured-model", max_attempts=2),
        {"GEMINI_API_KEY": "x"},
        lambda _: client,
        delays.append,
    )
    assert instance.classify(context()).risk == 0.1
    assert len(client.interactions.calls) == 2 and delays == [0.25]
