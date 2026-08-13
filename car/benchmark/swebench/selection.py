"""Deterministic, non-solution-aware selection for SWE-bench Verified."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Iterable

from car.benchmark.swebench.models import SAMPLE_PREFIX, SWEbenchInstance, SWEbenchSelectedInstance


def select_verified_sample(
    instances: Iterable[SWEbenchInstance], *, sample_size: int = 24, maximum_per_repository: int = 3
) -> tuple[SWEbenchSelectedInstance, ...]:
    """Select a stable difficulty-stratified sample without reading gold fields."""
    if sample_size < 1:
        raise ValueError("sample_size must be positive")
    if maximum_per_repository < 1:
        raise ValueError("maximum_per_repository must be positive")

    records = tuple(instances)
    if len({record.instance_id for record in records}) != len(records):
        raise ValueError("instance IDs must be unique")
    if len(records) < sample_size:
        raise ValueError("not enough instances for requested sample")

    grouped: dict[str, list[SWEbenchInstance]] = defaultdict(list)
    for record in records:
        grouped[record.difficulty].append(record)
    quotas = _difficulty_quotas(grouped, sample_size)
    selected: list[SWEbenchInstance] = []
    repository_counts: Counter[str] = Counter()
    selected_ids: set[str] = set()

    for difficulty in sorted(grouped):
        candidates = sorted(grouped[difficulty], key=_ranking_key)
        _take_candidates(
            candidates,
            quotas[difficulty],
            selected,
            selected_ids,
            repository_counts,
            maximum_per_repository,
        )

    if len(selected) != sample_size:
        remaining = sorted(
            (record for record in records if record.instance_id not in selected_ids),
            key=_ranking_key,
        )
        _take_candidates(
            remaining,
            sample_size - len(selected),
            selected,
            selected_ids,
            repository_counts,
            maximum_per_repository,
        )
    if len(selected) != sample_size:
        raise ValueError("repository cap prevents selecting requested sample")

    return tuple(
        SWEbenchSelectedInstance(
            instance_id=record.instance_id,
            repo=record.repo,
            base_commit=record.base_commit,
            difficulty=record.difficulty,
            version=record.version,
        )
        for record in selected
    )


def _difficulty_quotas(
    groups: dict[str, list[SWEbenchInstance]], sample_size: int
) -> dict[str, int]:
    """Allocate proportionally, retaining each available difficulty band."""
    total = sum(len(records) for records in groups.values())
    if not total:
        raise ValueError("at least one instance is required")
    if len(groups) > sample_size:
        raise ValueError("sample is smaller than the number of difficulty bands")

    quotas = {
        difficulty: max(1, sample_size * len(records) // total)
        for difficulty, records in groups.items()
    }
    while sum(quotas.values()) > sample_size:
        candidate = max(quotas, key=lambda item: (quotas[item], len(groups[item]), item))
        if quotas[candidate] == 1:
            raise ValueError("cannot represent each difficulty band")
        quotas[candidate] -= 1
    remainders = sorted(
        groups,
        key=lambda item: (-(sample_size * len(groups[item]) % total), item),
    )
    index = 0
    while sum(quotas.values()) < sample_size:
        difficulty = remainders[index % len(remainders)]
        if quotas[difficulty] < len(groups[difficulty]):
            quotas[difficulty] += 1
        index += 1
    return quotas


def _take_candidates(
    candidates: Iterable[SWEbenchInstance],
    count: int,
    selected: list[SWEbenchInstance],
    selected_ids: set[str],
    repository_counts: Counter[str],
    maximum_per_repository: int,
) -> None:
    for candidate in candidates:
        if count == 0:
            return
        if (
            candidate.instance_id in selected_ids
            or repository_counts[candidate.repo] >= maximum_per_repository
        ):
            continue
        selected.append(candidate)
        selected_ids.add(candidate.instance_id)
        repository_counts[candidate.repo] += 1
        count -= 1


def _ranking_key(instance: SWEbenchInstance) -> tuple[str, str]:
    digest = hashlib.sha256(f"{SAMPLE_PREFIX}:{instance.instance_id}".encode()).hexdigest()
    return digest, instance.instance_id
