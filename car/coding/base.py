"""Provider-neutral contract for proposing code changes without applying them."""

from typing import Protocol

from car.coding.models import CodingProposal, CodingTaskContext
from car.providers.models import (
    ProviderCapabilities,
    ProviderError,
    ProviderErrorKind,
    ProviderHealth,
)


class CodingProviderFailure(RuntimeError):
    """Safe provider failure carrying only CAR's normalized error taxonomy."""

    def __init__(self, error: ProviderError | ProviderErrorKind) -> None:
        self.error = (
            error
            if isinstance(error, ProviderError)
            else ProviderError(kind=error, message="provider coding proposal failed")
        )
        self.kind = self.error.kind
        super().__init__(self.kind.value)


class CodingProvider(Protocol):
    """Propose structured changes only; providers never write or execute commands."""

    name: str

    def capabilities(self) -> ProviderCapabilities: ...

    def health(self) -> ProviderHealth: ...

    def propose(self, context: CodingTaskContext) -> CodingProposal: ...
