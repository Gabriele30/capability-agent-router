"""Application composition for CLI and future integrations."""

from car.application.codex import (
    CodexApplicationFailureKind,
    CodexApplicationResult,
    CodexExecutionPolicy,
    execute_codex_handoff,
)

__all__ = [
    "CodexApplicationFailureKind",
    "CodexApplicationResult",
    "CodexExecutionPolicy",
    "execute_codex_handoff",
]
