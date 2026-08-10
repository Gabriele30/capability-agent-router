"""Provider-neutral coding proposal contracts; proposal application is intentionally absent."""

from car.coding.base import CodingProvider, CodingProviderFailure
from car.coding.gemini import GeminiCodingProvider
from car.coding.models import (
    CodingAttemptResult,
    CodingExecutionPolicy,
    CodingFileContext,
    CodingProposal,
    CodingTaskContext,
    FileChangeOperation,
    ProposedFileChange,
)

__all__ = [
    "CodingAttemptResult",
    "CodingExecutionPolicy",
    "CodingFileContext",
    "CodingProposal",
    "CodingProvider",
    "CodingProviderFailure",
    "CodingTaskContext",
    "FileChangeOperation",
    "GeminiCodingProvider",
    "ProposedFileChange",
]
