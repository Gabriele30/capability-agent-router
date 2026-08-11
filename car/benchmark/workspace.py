import hashlib
import shutil
import tempfile
from pathlib import Path

from car.benchmark.models import BenchmarkStrategy


class BenchmarkWorkspaceSet:
    def __init__(self, fixture: Path) -> None:
        self.fixture = fixture.resolve()
        self.root = Path(tempfile.mkdtemp(prefix="car-benchmark-"))
        self.workspaces = {}
        for strategy in BenchmarkStrategy:
            target = self.root / strategy.value
            shutil.copytree(self.fixture, target, ignore=shutil.ignore_patterns(".git"))
            self.workspaces[strategy] = target
        self.baseline = self.identity(self.fixture)
        if any(self.identity(path) != self.baseline for path in self.workspaces.values()):
            raise RuntimeError("benchmark baseline mismatch")

    @staticmethod
    def identity(root: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*")):
            if path.is_file() and ".git" not in path.parts:
                digest.update(path.relative_to(root).as_posix().encode())
                digest.update(path.read_bytes())
        return digest.hexdigest()

    def cleanup(self) -> None:
        shutil.rmtree(self.root)
