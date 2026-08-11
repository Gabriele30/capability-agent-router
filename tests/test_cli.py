from importlib import import_module
from pathlib import Path

from typer.testing import CliRunner

from car.application.routing import evaluate_analysis
from car.cli.app import app
from car.config.models import CarConfig
from car.providers.models import (
    ProviderCapabilities,
    ProviderClassification,
    ProviderHealth,
    ProviderStatus,
)
from car.router.models import Complexity, Route, ScopeSize, TaskCategory

runner = CliRunner()


class _FakeProvider:
    def __init__(self, route: Route = Route.CODEX, confidence: float = 0.95) -> None:
        self.route = route
        self.confidence = confidence
        self.calls = 0
        self.failure: RuntimeError | None = None

    def capabilities(self):
        return ProviderCapabilities(supports_classification=True)

    def health(self):
        return ProviderHealth(status=ProviderStatus.CONFIGURED, configured=True)

    def classify(self, context):
        self.calls += 1
        if self.failure:
            raise self.failure
        return ProviderClassification(
            categories=[TaskCategory.FRONTEND],
            complexity=Complexity.LOW,
            risk=0.8,
            scope=ScopeSize.SMALL,
            suggested_route=self.route,
            confidence=self.confidence,
        )


def _configured_context(git_repository: Path) -> None:
    context = git_repository / ".car-context"
    context.mkdir()
    (context / "config.json").write_text(
        CarConfig(providers={"gemini": {"enabled": True, "model": "test-model"}}).model_dump_json(),
        encoding="utf-8",
    )


def _patch_cli_provider(monkeypatch, provider: _FakeProvider) -> None:
    cli_module = import_module("car.cli.app")

    def evaluate_with_fake(request, repository, mode, config):
        return evaluate_analysis(
            request,
            repository,
            mode,
            config,
            provider_factory=lambda _: provider,
        )

    monkeypatch.setattr(cli_module, "evaluate_analysis", evaluate_with_fake)


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "version 0.6.0" in result.stdout


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


def test_analyze_json_reports_evaluation_without_secrets(git_repository: Path, monkeypatch) -> None:
    monkeypatch.chdir(git_repository)
    result = runner.invoke(app, ["analyze", "Format README.md", "--json"])

    assert result.exit_code == 0
    assert '"deterministic_decision"' in result.stdout
    assert '"skip_reason": "deterministic_l0"' in result.stdout
    assert "GEMINI_API_KEY" not in result.stdout


def test_analyze_accepts_hyphenated_gemini_to_codex_mode(git_repository: Path, monkeypatch) -> None:
    monkeypatch.chdir(git_repository)
    result = runner.invoke(app, ["analyze", "Fix CSS spacing", "--mode", "gemini-to-codex"])

    assert result.exit_code == 0
    assert "Route:      GEMINI_TO_CODEX" in result.stdout


def test_task_uses_provider_escalated_final_route(git_repository: Path, monkeypatch) -> None:
    _configured_context(git_repository)
    provider = _FakeProvider(route=Route.CODEX)
    _patch_cli_provider(monkeypatch, provider)
    monkeypatch.chdir(git_repository)
    result = runner.invoke(app, ["task", "Fix CSS spacing in dashboard"])

    assert result.exit_code == 0
    assert provider.calls == 1
    assert "Final\nRoute:      CODEX" in result.stdout
    assert "Codex execution is not implemented yet." in result.stdout


def test_task_low_confidence_and_l0_skip_provider(git_repository: Path, monkeypatch) -> None:
    _configured_context(git_repository)
    (git_repository / "sample.py").write_text("value = 1\n", encoding="utf-8")
    provider = _FakeProvider(route=Route.CODEX, confidence=0.20)
    _patch_cli_provider(monkeypatch, provider)
    monkeypatch.chdir(git_repository)
    low_confidence = runner.invoke(app, ["task", "Fix CSS spacing"])
    l0 = runner.invoke(app, ["task", "Format sample.py", "--dry-run"])

    assert low_confidence.exit_code == 0
    assert "Final\nRoute:      GEMINI" in low_confidence.stdout
    assert "Gemini coding execution is not implemented yet." in low_confidence.stdout
    assert l0.exit_code == 0
    assert provider.calls == 1
    assert "Dry run: Nothing executed." in l0.stdout


def test_task_json_and_providers_do_not_expose_secret(git_repository: Path, monkeypatch) -> None:
    _configured_context(git_repository)
    (git_repository / "sample.py").write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-test-key")
    monkeypatch.chdir(git_repository)
    task_result = runner.invoke(app, ["task", "Format sample.py", "--dry-run", "--json"])
    providers_result = runner.invoke(app, ["providers", "--json"])

    assert task_result.exit_code == 0
    assert '"routing"' in task_result.stdout
    assert providers_result.exit_code == 0
    assert '"local_status"' in providers_result.stdout
    assert "super-secret-test-key" not in task_result.stdout
    assert "super-secret-test-key" not in providers_result.stdout


def test_providers_reports_local_disabled_status(git_repository: Path, monkeypatch) -> None:
    monkeypatch.chdir(git_repository)
    result = runner.invoke(app, ["providers"])

    assert result.exit_code == 0
    assert "Local status:   DISABLED" in result.stdout
    assert "Live checked:   no" in result.stdout


def test_task_provider_failure_is_safe_and_single_call(git_repository: Path, monkeypatch) -> None:
    _configured_context(git_repository)
    provider = _FakeProvider()
    provider.failure = RuntimeError("timeout")
    _patch_cli_provider(monkeypatch, provider)
    monkeypatch.chdir(git_repository)
    result = runner.invoke(app, ["task", "Fix CSS spacing"])

    assert result.exit_code == 0
    assert provider.calls == 1
    assert "Provider classification failed safely." in result.stdout
    assert "Final\nRoute:      GEMINI" in result.stdout


def test_providers_reports_missing_model_and_credentials(git_repository: Path, monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    context = git_repository / ".car-context"
    context.mkdir()
    (context / "config.json").write_text(
        CarConfig(providers={"gemini": {"enabled": True}}).model_dump_json(), encoding="utf-8"
    )
    monkeypatch.chdir(git_repository)
    no_model = runner.invoke(app, ["providers"])
    (context / "config.json").write_text(
        CarConfig(providers={"gemini": {"enabled": True, "model": "test-model"}}).model_dump_json(),
        encoding="utf-8",
    )
    no_credentials = runner.invoke(app, ["providers"])

    assert "Local status:   NOT_CONFIGURED" in no_model.stdout
    assert "Local status:   MISSING_CREDENTIALS" in no_credentials.stdout


def test_l0_dry_run_does_not_execute(git_repository: Path, monkeypatch) -> None:
    target = git_repository / "sample.py"
    original = b"x=1\n"
    target.write_bytes(original)
    monkeypatch.chdir(git_repository)
    result = runner.invoke(app, ["task", "Format sample.py", "--dry-run"])

    assert result.exit_code == 0
    assert "Dry run" in result.stdout
    assert target.read_bytes() == original
