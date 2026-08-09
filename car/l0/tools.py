"""Deterministic availability checks for L0 tools."""

from collections.abc import Callable
from shutil import which


def find_tool(name: str, lookup: Callable[[str], str | None] = which) -> str | None:
    """Return an already-installed executable; never install or download tools."""
    return lookup(name)
