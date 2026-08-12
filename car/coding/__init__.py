"""Provider-neutral coding proposal contracts; proposal application is intentionally absent."""

from typing import TYPE_CHECKING, Any

from car.coding.base import CodingProvider, CodingProviderFailure
from car.coding.models import (
    CodingAttemptResult,
    CodingExecutionPolicy,
    CodingFileContext,
    CodingProposal,
    CodingTaskContext,
    FileChangeOperation,
    ProposedFileChange,
)
from car.coding.verification import CodingVerificationCoordinator, CodingVerificationResult

if TYPE_CHECKING:
    from car.coding.gemini import GeminiCodingProvider

__all__ = [
    "CodingAttemptResult",
    "CodingExecutionPolicy",
    "CodingFileContext",
    "CodingProposal",
    "CodingProvider",
    "CodingProviderFailure",
    "CodingTaskContext",
    "CodingVerificationCoordinator",
    "CodingVerificationResult",
    "FileChangeOperation",
    "GeminiCodingProvider",
    "ProposedFileChange",
]


def __getattr__(name: str) -> Any:
    """Keep the provider adapter out of foundational model-import paths."""
    if name == "GeminiCodingProvider":
        from car.coding.gemini import GeminiCodingProvider

        return GeminiCodingProvider
    raise AttributeError(name)
