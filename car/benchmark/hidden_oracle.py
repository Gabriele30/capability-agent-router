"""Trusted, provider-invisible behavioral checks for local pilot benchmarks."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable

HIDDEN_ORACLE_IDS = (
    "double-value",
    "username-normalization",
    "discounted-invoice-tax",
    "pagination-boundaries",
    "merge-adjacent-intervals",
)


def run(oracle_id: str) -> None:
    """Run one CAR-owned oracle from the verified workspace's current directory."""
    try:
        _ORACLES[oracle_id]()
    except KeyError as error:
        raise ValueError("unsupported hidden benchmark oracle") from error


def _double_value() -> None:
    double = importlib.import_module("double").double
    assert double(7) == 14
    assert double(0) == 0
    assert double(-3) == -6


def _username_normalization() -> None:
    normalize = importlib.import_module("usernames").normalize_username
    assert normalize("  Ada.Lovelace  ") == "ada.lovelace"
    assert normalize("\tGrace.Hopper\n") == "grace.hopper"


def _discounted_invoice_tax() -> None:
    total = importlib.import_module("invoice").total
    assert total(100, 20, 10) == 88
    assert total(100, 0, 10) == 110
    assert total(100, 50, 20) == 60


def _pagination_boundaries() -> None:
    page_count = importlib.import_module("pagination").page_count
    assert page_count(0, 10) == 0
    assert page_count(1, 10) == 1
    assert page_count(20, 10) == 2
    assert page_count(21, 10) == 3
    try:
        page_count(5, 0)
    except ValueError:
        pass
    else:
        raise AssertionError("non-positive page size must be rejected")


def _merge_adjacent_intervals() -> None:
    merge = importlib.import_module("intervals").merge_intervals
    assert merge([]) == []
    assert merge([(5, 7), (1, 2), (3, 4), (9, 9), (8, 8)]) == [(1, 9)]
    assert merge([(4, 6), (1, 3), (10, 11)]) == [(1, 6), (10, 11)]


_ORACLES: dict[str, Callable[[], None]] = {
    "double-value": _double_value,
    "username-normalization": _username_normalization,
    "discounted-invoice-tax": _discounted_invoice_tax,
    "pagination-boundaries": _pagination_boundaries,
    "merge-adjacent-intervals": _merge_adjacent_intervals,
}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("expected one hidden oracle identifier")
    run(sys.argv[1])
