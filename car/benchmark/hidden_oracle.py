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
    "slug-normalization",
    "retry-delay-cap",
    "bounded-percentage",
    "chunk-boundaries",
    "inclusive-date-overlap",
    "deduplicate-preserve-order",
    "shipping-after-discount",
    "inventory-reservation",
    "permission-inheritance",
    "cache-expiration",
    "configuration-precedence",
    "event-deduplication",
    "atomic-account-transfer",
    "dependency-order",
    "ttl-lru-cache",
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


def _slug_normalization() -> None:
    slugify = importlib.import_module("slug").slugify
    assert slugify("  Hello__World  ") == "hello-world"
    assert slugify("foo---bar") == "foo-bar"
    assert slugify(" -- Mixed _ Space -- ") == "mixed-space"


def _retry_delay_cap() -> None:
    delay_for = importlib.import_module("retry").delay_for
    assert delay_for(2, 0, 10) == 2
    assert delay_for(2, 2, 10) == 8
    assert delay_for(2, 4, 10) == 10


def _bounded_percentage() -> None:
    percentage = importlib.import_module("percentage").percentage
    assert percentage(1, 4) == 25
    assert percentage(-1, 4) == 0
    assert percentage(9, 4) == 100
    assert percentage(1, 0) == 0


def _chunk_boundaries() -> None:
    chunks = importlib.import_module("chunks").chunks
    assert chunks([], 2) == []
    assert chunks([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]
    assert chunks([1, 2, 3], 2) == [[1, 2], [3]]
    assert chunks([1, 2], 1) == [[1], [2]]
    try:
        chunks([1], 0)
    except ValueError:
        pass
    else:
        raise AssertionError("non-positive chunk size must be rejected")


def _inclusive_date_overlap() -> None:
    from datetime import date

    overlaps = importlib.import_module("date_ranges").overlaps
    assert overlaps(date(2026, 1, 1), date(2026, 1, 3), date(2026, 1, 3), date(2026, 1, 5))
    assert overlaps(date(2026, 1, 2), date(2026, 1, 4), date(2026, 1, 1), date(2026, 1, 5))
    assert not overlaps(date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3), date(2026, 1, 4))
    try:
        overlaps(date(2026, 1, 3), date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 2))
    except ValueError:
        pass
    else:
        raise AssertionError("reversed range must be rejected")


def _deduplicate_preserve_order() -> None:
    deduplicate = importlib.import_module("records").deduplicate
    records = [{"id": "a", "value": 1}, {"id": "b", "value": 2}, {"id": "a", "value": 3}]
    assert deduplicate(records) == records[:2]
    assert records[2]["value"] == 3
    assert deduplicate([]) == []


def _shipping_after_discount() -> None:
    shipping = importlib.import_module("shipping").shipping_cost
    assert shipping(100, 20, 90, 8) == 8
    assert shipping(100, 10, 90, 8) == 0
    assert shipping(50, 0, 90, 8) == 8


def _inventory_reservation() -> None:
    Inventory = importlib.import_module("inventory").Inventory
    inventory = Inventory({"a": 3, "b": 2})
    assert not inventory.reserve({"a": 2, "b": 3})
    assert inventory.quantities == {"a": 3, "b": 2}
    assert inventory.reserve({"a": 2, "b": 1})
    assert inventory.quantities == {"a": 1, "b": 1}


def _permission_inheritance() -> None:
    permissions_for = importlib.import_module("permissions").permissions_for
    roles = {
        "viewer": {"permissions": ["read"], "parents": []},
        "editor": {"permissions": ["write", "read"], "parents": ["viewer"]},
        "loop": {"permissions": ["x"], "parents": ["loop"]},
    }
    assert permissions_for("editor", roles) == ["read", "write"]
    assert permissions_for("loop", roles) == ["x"]


def _cache_expiration() -> None:
    Cache = importlib.import_module("cache").Cache
    now = [0]
    cache = Cache(lambda: now[0])
    cache.set("a", "first", 5)
    assert cache.get("a") == "first"
    now[0] = 5
    assert cache.get("a") is None
    cache.set("a", "second", 10)
    assert cache.get("a") == "second"


def _configuration_precedence() -> None:
    resolve = importlib.import_module("configuration").resolve
    assert resolve({"x": 1, "zero": 4}, {"x": 2, "zero": 0}, {"x": 3}) == {"x": 3, "zero": 0}
    assert resolve({"x": 1}, {}, {}) == {"x": 1}
    assert resolve({"enabled": True}, {"enabled": False}, {})["enabled"] is False


def _event_deduplication() -> None:
    Processor = importlib.import_module("events").Processor
    processor = Processor()
    assert processor.process("a", 4) == 4
    assert processor.process("a", 4) == 4
    assert processor.total == 4
    assert not processor.process("bad", None)
    assert processor.process("bad", 2) == 6


def _atomic_account_transfer() -> None:
    Bank = importlib.import_module("accounts").Bank
    bank = Bank({"a": 10, "b": 3})
    assert not bank.transfer("a", "b", 20)
    assert bank.balances == {"a": 10, "b": 3}
    assert bank.transfer("a", "b", 4)
    assert bank.balances == {"a": 6, "b": 7}
    assert sum(bank.balances.values()) == 13
    assert not bank.transfer("a", "missing", 1)
    assert bank.balances == {"a": 6, "b": 7}


def _dependency_order() -> None:
    order = importlib.import_module("dependencies").order
    result = order({"app": ["lib"], "lib": [], "tool": []})
    assert result.index("lib") < result.index("app") and set(result) == {"app", "lib", "tool"}
    try:
        order({"a": ["b"], "b": ["a"]})
    except ValueError:
        pass
    else:
        raise AssertionError("cycles must be rejected")


def _ttl_lru_cache() -> None:
    Cache = importlib.import_module("lru_cache").Cache
    now = [0]
    cache = Cache(2, lambda: now[0])
    cache.set("a", 1, 2)
    cache.set("b", 2, 10)
    assert cache.get("a") == 1
    cache.set("c", 3, 10)
    assert cache.get("b") is None and cache.get("a") == 1 and cache.get("c") == 3
    now[0] = 3
    assert cache.get("a") is None
    cache.set("d", 4, 10)
    assert cache.get("c") == 3 and cache.get("d") == 4


_ORACLES: dict[str, Callable[[], None]] = {
    "double-value": _double_value,
    "username-normalization": _username_normalization,
    "discounted-invoice-tax": _discounted_invoice_tax,
    "pagination-boundaries": _pagination_boundaries,
    "merge-adjacent-intervals": _merge_adjacent_intervals,
    "slug-normalization": _slug_normalization,
    "retry-delay-cap": _retry_delay_cap,
    "bounded-percentage": _bounded_percentage,
    "chunk-boundaries": _chunk_boundaries,
    "inclusive-date-overlap": _inclusive_date_overlap,
    "deduplicate-preserve-order": _deduplicate_preserve_order,
    "shipping-after-discount": _shipping_after_discount,
    "inventory-reservation": _inventory_reservation,
    "permission-inheritance": _permission_inheritance,
    "cache-expiration": _cache_expiration,
    "configuration-precedence": _configuration_precedence,
    "event-deduplication": _event_deduplication,
    "atomic-account-transfer": _atomic_account_transfer,
    "dependency-order": _dependency_order,
    "ttl-lru-cache": _ttl_lru_cache,
}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("expected one hidden oracle identifier")
    run(sys.argv[1])
