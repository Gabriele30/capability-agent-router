import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from car.benchmark.manifest import load_manifest, manifest_sha256
from car.benchmark.models import BenchmarkCase, BenchmarkManifest
from car.benchmark.workspace import BenchmarkWorkspaceSet


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "fixture"
    root.mkdir()
    (root / "calculator.py").write_text("value = 1\n")
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=x@y.z", "-c", "user.name=x", "commit", "-m", "base"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return root


def _manifest(root: Path) -> BenchmarkManifest:
    return BenchmarkManifest(
        cases=(
            BenchmarkCase(
                id="one",
                category="bugfix",
                task="Fix it",
                fixture=root.name,
                authorized_paths=("calculator.py",),
                verification=("pytest",),
            ),
        )
    )


def test_manifest_validation_and_hash(tmp_path: Path):
    root = _repo(tmp_path)
    path = tmp_path / "manifest.json"
    path.write_text(_manifest(root).model_dump_json())
    loaded = load_manifest(path)
    assert manifest_sha256(loaded) == manifest_sha256(loaded)
    with pytest.raises(ValidationError):
        BenchmarkManifest(cases=())
    with pytest.raises(ValidationError):
        BenchmarkManifest(cases=(_manifest(root).cases[0], _manifest(root).cases[0]))
    with pytest.raises(ValidationError):
        BenchmarkCase(
            id="x",
            category="x",
            task="x",
            fixture="x",
            authorized_paths=("../x",),
            verification=("pytest",),
        )


def test_real_git_workspaces_are_isolated_and_cleanup(tmp_path: Path):
    root = _repo(tmp_path)
    before = (root / "calculator.py").read_bytes()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).strip()
    spaces = BenchmarkWorkspaceSet(root)
    assert len(spaces.workspaces) == 3
    assert all(
        BenchmarkWorkspaceSet.identity(path) == spaces.baseline
        for path in spaces.workspaces.values()
    )
    for index, path in enumerate(spaces.workspaces.values()):
        (path / "calculator.py").write_text(f"value = {index}\n")
    assert len({(path / "calculator.py").read_bytes() for path in spaces.workspaces.values()}) == 3
    assert (root / "calculator.py").read_bytes() == before and subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root
    ).strip() == head
    workspace_root = spaces.root
    spaces.cleanup()
    spaces.cleanup()
    assert not workspace_root.exists()
