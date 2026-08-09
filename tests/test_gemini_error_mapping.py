import pytest

from car.providers.gemini import _map_gemini_error, is_retryable
from car.providers.models import ProviderErrorKind


class FakeError(Exception):
    def __init__(self, code=None, message=""):
        self.code, self.message = code, message


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
