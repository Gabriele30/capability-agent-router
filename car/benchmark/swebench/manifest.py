"""Loading for the tracked, non-gold SWE-bench sample specification."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from car.benchmark.swebench.models import SWEbenchSampleSpec


def load_sample_spec(path: Path) -> SWEbenchSampleSpec:
    """Load and verify the canonical sample identity without dataset access."""
    try:
        return SWEbenchSampleSpec.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as error:
        raise ValueError("invalid SWE-bench sample specification") from error
