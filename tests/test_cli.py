from pathlib import Path

from typer.testing import CliRunner

from car.cli.app import app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "version 0.3.0" in result.stdout


def test_init_is_idempotent(git_repository: Path, monkeypatch) -> None:
    monkeypatch.chdir(git_repository)
    first = runner.invoke(app, ["init"])
    second = runner.invoke(app, ["init"])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert (git_repository / ".car-context" / "config.json").is_file()
    assert "already valid" in second.stdout


def test_status_in_repository(git_repository: Path, monkeypatch) -> None:
    monkeypatch.chdir(git_repository)
    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Tracked files: 1" in result.stdout
    assert "Git state:" in result.stdout


def test_status_shows_dirty_worktree(git_repository: Path, monkeypatch) -> None:
    (git_repository / "README.md").write_text("changed\n", encoding="utf-8")
    (git_repository / "new.txt").write_text("new\n", encoding="utf-8")
    monkeypatch.chdir(git_repository)
    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Git state:" in result.stdout
    assert "Working tree" in result.stdout
    assert "Modified:   1" in result.stdout
    assert "Untracked:  1" in result.stdout


def test_empty_task_is_rejected(git_repository: Path, monkeypatch) -> None:
    monkeypatch.chdir(git_repository)
    result = runner.invoke(app, ["task", "   "])

    assert result.exit_code == 2
    assert "Invalid task" in result.stdout


def test_analyze_is_read_only(git_repository: Path, monkeypatch) -> None:
    target = git_repository / "sample.py"
    original = b"x=1\n"
    target.write_bytes(original)
    monkeypatch.chdir(git_repository)
    result = runner.invoke(app, ["analyze", "Format sample.py"])

    assert result.exit_code == 0
    assert target.read_bytes() == original


def test_l0_dry_run_does_not_execute(git_repository: Path, monkeypatch) -> None:
    target = git_repository / "sample.py"
    original = b"x=1\n"
    target.write_bytes(original)
    monkeypatch.chdir(git_repository)
    result = runner.invoke(app, ["task", "Format sample.py", "--dry-run"])

    assert result.exit_code == 0
    assert "Dry run" in result.stdout
    assert target.read_bytes() == original
