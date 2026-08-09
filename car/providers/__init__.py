"""Provider-neutral contracts and provider-specific adapters."""

from car.providers.base import ClassificationProvider
from car.providers.context import build_classification_context
from car.providers.models import (
    ClassificationContext,
    ProviderCapabilities,
    ProviderClassification,
    ProviderError,
    ProviderErrorKind,
    ProviderHealth,
    ProviderStatus,
)

__all__ = [
    "ClassificationContext",
    "ClassificationProvider",
    "build_classification_context",
    "ProviderCapabilities",
    "ProviderClassification",
    "ProviderError",
    "ProviderErrorKind",
    "ProviderHealth",
    "ProviderStatus",
]
