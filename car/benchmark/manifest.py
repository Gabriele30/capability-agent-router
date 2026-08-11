import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from car.benchmark.models import BenchmarkManifest


def load_manifest(path: Path) -> BenchmarkManifest:
    try:
        manifest = BenchmarkManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid benchmark manifest: {error}") from error
    for case in manifest.cases:
        fixture = (path.parent / case.fixture).resolve()
        if not fixture.is_dir() or not (fixture / ".git").exists():
            raise ValueError(f"fixture is not a local Git repository: {case.fixture}")
    return manifest


def manifest_sha256(manifest: BenchmarkManifest) -> str:
    payload = json.dumps(manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()
