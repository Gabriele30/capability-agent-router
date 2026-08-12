"""Provider-neutral failure evidence and future Codex handoff contracts."""

from typing import TYPE_CHECKING, Any

from car.escalation.models import CodexHandoff, EscalationDecision, HandoffPolicy

if TYPE_CHECKING:
    from car.escalation.handoff import (
        build_codex_handoff,
        render_codex_handoff_markdown,
        write_codex_handoff,
    )

__all__ = [
    "CodexHandoff",
    "EscalationDecision",
    "HandoffPolicy",
    "build_codex_handoff",
    "render_codex_handoff_markdown",
    "write_codex_handoff",
]


def __getattr__(name: str) -> Any:
    """Avoid importing handoff composition while its model dependencies initialize."""
    if name in {"build_codex_handoff", "render_codex_handoff_markdown", "write_codex_handoff"}:
        from car.escalation.handoff import (
            build_codex_handoff,
            render_codex_handoff_markdown,
            write_codex_handoff,
        )

        return {
            "build_codex_handoff": build_codex_handoff,
            "render_codex_handoff_markdown": render_codex_handoff_markdown,
            "write_codex_handoff": write_codex_handoff,
        }[name]
    raise AttributeError(name)
