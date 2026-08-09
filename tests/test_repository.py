import subprocess
from pathlib import Path

from car.repository.scanner import scan_repository


def test_language_and_project_detection(git_repository: Path) -> None:
    (git_repository / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (git_repository / "web.ts").write_text("export {};\n", encoding="utf-8")
    (git_repository / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (git_repository / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(git_repository), "add", "."], check=True)

    state = scan_repository(git_repository)

    assert state.languages.counts == {"Python": 1, "TypeScript": 1, "Markdown": 1}
    assert state.project_signals.systems == ["Python", "Docker"]
    assert state.tracked_file_count == 5
