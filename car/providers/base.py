"""Small provider-independent classification contract."""

from typing import Protocol

from car.providers.models import (
    ClassificationContext,
    ProviderCapabilities,
    ProviderClassification,
    ProviderHealth,
)


class ClassificationProvider(Protocol):
    def capabilities(self) -> ProviderCapabilities: ...

    def health(self) -> ProviderHealth: ...

    def classify(self, context: ClassificationContext) -> ProviderClassification: ...
