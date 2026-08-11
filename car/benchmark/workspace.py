import hashlib
import os
import shutil
import stat
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
            shutil.copytree(self.fixture, target)
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
        if self.root.exists():
            shutil.rmtree(self.root, onexc=self._make_writable_and_retry)

    @staticmethod
    def _make_writable_and_retry(function, path, error) -> None:
        if not isinstance(error, PermissionError):
            raise error
        os.chmod(path, stat.S_IWRITE)
        function(path)
