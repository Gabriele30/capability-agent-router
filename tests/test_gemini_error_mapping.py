import pytest
from google.genai.errors import ClientError

from car.providers.gemini import _map_gemini_error, is_retryable
from car.providers.models import ProviderErrorKind


class FakeError(Exception):
    def __init__(self, code=None, message="", status=None):
        self.code, self.message, self.status = code, message, status


class StatusCodeError(Exception):
    def __init__(self, status_code, message=""):
        self.status_code, self.message = status_code, message


@pytest.mark.parametrize(
    "code,kind",
    [
        (400, "invalid_request"),
        (401, "authentication_error"),
        (403, "permission_denied"),
        (404, "model_not_found"),
        (408, "timeout"),
        (500, "service_error"),
        (502, "service_error"),
        (503, "service_error"),
        (504, "service_error"),
        (999, "unknown_error"),
    ],
)
def test_http_mapping(code, kind):
    assert _map_gemini_error(FakeError(code, "noise")).kind.value == kind


@pytest.mark.parametrize(
    "message,kind",
    [
        ("rate limit exceeded", "rate_limited"),
        ("requests per minute exceeded", "rate_limited"),
        ("quota exhausted", "quota_exhausted"),
        ("daily quota exceeded", "quota_exhausted"),
        ("unknown", "rate_limited"),
    ],
)
def test_429_mapping(message, kind):
    assert _map_gemini_error(FakeError(429, message)).kind.value == kind


def test_status_code_404_maps_to_model_not_found():
    assert _map_gemini_error(StatusCodeError(404, "noise")).kind.value == "model_not_found"


def test_status_code_takes_precedence_when_code_is_also_present():
    error = StatusCodeError(404, "noise")
    error.code = 500
    assert _map_gemini_error(error).kind.value == "model_not_found"


def test_retryability_and_secret_safety():
    assert all(
        is_retryable(kind)
        for kind in (
            ProviderErrorKind.TIMEOUT,
            ProviderErrorKind.RATE_LIMITED,
            ProviderErrorKind.SERVICE_ERROR,
        )
    )
    assert not is_retryable(ProviderErrorKind.QUOTA_EXHAUSTED)
    assert (
        "super-secret-test-key"
        not in _map_gemini_error(FakeError(401, "super-secret-test-key")).message
    )


def test_sdk_client_error_preserves_safe_structured_diagnostic():
    error = ClientError(
        400,
        {
            "error": {
                "status": "INVALID_ARGUMENT",
                "message": "response_format has an invalid schema reference",
            }
        },
    )

    mapped = _map_gemini_error(error)

    assert mapped.kind == ProviderErrorKind.INVALID_REQUEST
    assert mapped.http_status == 400
    assert mapped.status == "INVALID_ARGUMENT"
    assert mapped.message == "response_format has an invalid schema reference"
    assert "details" not in mapped.model_dump_json()


@pytest.mark.parametrize(
    ("error", "expected_kind"),
    (
        (
            FakeError(401, "credentials rejected", "UNAUTHENTICATED"),
            ProviderErrorKind.AUTHENTICATION_ERROR,
        ),
        (
            FakeError(403, "permission denied", "PERMISSION_DENIED"),
            ProviderErrorKind.PERMISSION_DENIED,
        ),
        (
            FakeError(429, "rate limit exceeded", "RESOURCE_EXHAUSTED"),
            ProviderErrorKind.RATE_LIMITED,
        ),
        (
            FakeError(429, "daily quota exceeded", "RESOURCE_EXHAUSTED"),
            ProviderErrorKind.QUOTA_EXHAUSTED,
        ),
        (FakeError(503, "service unavailable", "UNAVAILABLE"), ProviderErrorKind.SERVICE_ERROR),
    ),
)
def test_safe_error_mapping_retains_status_without_exception_details(error, expected_kind):
    mapped = _map_gemini_error(error)
    assert mapped.kind == expected_kind
    assert mapped.http_status == error.code
    assert mapped.status == error.status
    assert mapped.message == error.message


def test_provider_message_is_bounded_and_redacts_sensitive_values():
    oversized = "x" * 600
    mapped = _map_gemini_error(
        FakeError(400, f"api_key=super-secret-test-key token=token-value {oversized}")
    )
    assert mapped.message is not None and len(mapped.message) <= 500
    assert "super-secret-test-key" not in mapped.message
    assert "token-value" not in mapped.message
    assert "<redacted>" in mapped.message


def test_unsafe_or_absent_provider_message_uses_generic_diagnostic():
    payload = _map_gemini_error(FakeError(400, 'request payload {"prompt":"secret"}'))
    absent = _map_gemini_error(FakeError(400, None))
    assert payload.message == "Gemini rejected the request."
    assert absent.message == "Gemini rejected the request."
    assert "prompt" not in payload.model_dump_json()
