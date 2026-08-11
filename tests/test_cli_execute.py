"""Offline CLI tests for previewed, explicitly authorized coding execution."""

from importlib import import_module
from pathlib import Path

from typer.testing import CliRunner

from car.application.execution_gateway import CodingFlowGateway
from car.cli.app import app
from car.codex.models import CodexExecutionResult, CodexRuntimeHealth, CodexRuntimeHealthStatus
from car.coding.models import CodingProposal, FileChangeOperation, ProposedFileChange
from car.coding.verification import CodingVerificationCoordinator
from car.providers.models import ProviderCapabilities, ProviderHealth, ProviderStatus
from car.verification.models import VerificationResult, VerificationStatus

runner = CliRunner()


class FakeProvider:
    name = "fake"

    def __init__(
        self, proposal: CodingProposal, status: ProviderStatus = ProviderStatus.CONFIGURED
    ) -> None:
        self.proposal = proposal
        self.status = status
        self.calls = 0

    def capabilities(self):
        return ProviderCapabilities(supports_code_changes=True)

    def health(self):
        return ProviderHealth(status=self.status)

    def propose(self, context):
        self.calls += 1
        return self.proposal


class FakeEngine:
    def __init__(self, status: VerificationStatus) -> None:
        self.status = status

    def verify(self, plan, stop_on_failure=True):
        return VerificationResult(status=self.status, message="test")


class FakeRuntime:
    def __init__(self) -> None:
        self.health_calls = 0
        self.execute_calls = 0

    def health(self):
        self.health_calls += 1
        return CodexRuntimeHealth(status=CodexRuntimeHealthStatus.READY)

    def execute(self, request):
        self.execute_calls += 1
        return CodexExecutionResult(attempted=True, succeeded=True, final_message="diagnostic")


def _proposal(old: str, new: str = "value = 2\n") -> CodingProposal:
    return CodingProposal(
        summary="change",
        changes=[
            ProposedFileChange(
                path="sample.py",
                operation=FileChangeOperation.MODIFY,
                patch=(
                    "--- a/sample.py\n+++ b/sample.py\n@@ -1 +1 @@\n"
                    f"-{old.rstrip()}\n+{new.rstrip()}\n"
                ),
            )
        ],
    )


def _patch_dependencies(monkeypatch, provider: FakeProvider, runtime: FakeRuntime, status):
    cli = import_module("car.cli.app")
    monkeypatch.setattr(cli, "_build_coding_provider", lambda config: provider)
    monkeypatch.setattr(cli, "_build_codex_runtime", lambda: runtime)
    monkeypatch.setattr(
        cli,
        "CodingFlowGateway",
        lambda coding_provider, codex_runtime: CodingFlowGateway(
            coding_provider,
            codex_runtime,
            verification_coordinator=CodingVerificationCoordinator(FakeEngine(status)),
        ),
    )


def test_execute_decline_is_preview_only(git_repository: Path, monkeypatch):
    target = git_repository / "sample.py"
    target.write_bytes(b"value = 1\n")
    provider, runtime = FakeProvider(_proposal("value = 1\n")), FakeRuntime()
    _patch_dependencies(monkeypatch, provider, runtime, VerificationStatus.PASSED)
    monkeypatch.chdir(git_repository)

    result = runner.invoke(
        app, ["execute", "Fix CSS spacing", "--file", "sample.py", "--verify", "ruff"], input="n\n"
    )

    assert result.exit_code == 0 and "Coding execution preview" in result.stdout
    assert "Execution cancelled" in result.stdout and provider.calls == runtime.execute_calls == 0
    assert target.read_bytes() == b"value = 1\n" and not (git_repository / ".car-context").exists()


def test_execute_yes_runs_gateway_once_and_retains_verified_patch(
    git_repository: Path, monkeypatch
):
    target = git_repository / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")
    provider, runtime = FakeProvider(_proposal("value = 1\n")), FakeRuntime()
    _patch_dependencies(monkeypatch, provider, runtime, VerificationStatus.PASSED)
    monkeypatch.chdir(git_repository)

    result = runner.invoke(
        app,
        ["execute", "Fix CSS spacing", "--file", "sample.py", "--verify", "ruff", "--yes"],
    )

    assert result.exit_code == 0 and "Coding execution preview" in result.stdout
    assert "CAR Execution Result" in result.stdout and "Task: RESOLVED" in result.stdout
    assert "Workspace: updated safely" in result.stdout and provider.calls == 1
    assert "Codex analysis: not required" in result.stdout
    assert runtime.health_calls == runtime.execute_calls == 0
    assert target.read_text(encoding="utf-8") == "value = 2\n"


def test_execute_gemini_to_codex_verified_success_does_not_call_codex(
    git_repository: Path, monkeypatch
):
    target = git_repository / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")
    provider, runtime = FakeProvider(_proposal("value = 1\n")), FakeRuntime()
    _patch_dependencies(monkeypatch, provider, runtime, VerificationStatus.PASSED)
    monkeypatch.chdir(git_repository)

    result = runner.invoke(
        app,
        ["execute", "Fix parser regression", "--file", "sample.py", "--verify", "ruff", "--yes"],
    )

    assert result.exit_code == 0 and "Route: GEMINI_TO_CODEX" in result.stdout
    assert "Task: RESOLVED" in result.stdout and "Codex analysis: not required" in result.stdout
    assert runtime.health_calls == runtime.execute_calls == 0


def test_execute_blocks_missing_scope_verification_unsafe_file_and_direct_codex(
    git_repository: Path, monkeypatch
):
    target = git_repository / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")
    provider, runtime = FakeProvider(_proposal("value = 1\n")), FakeRuntime()
    _patch_dependencies(monkeypatch, provider, runtime, VerificationStatus.PASSED)
    monkeypatch.chdir(git_repository)

    no_files = runner.invoke(app, ["execute", "Fix CSS spacing", "--yes"])
    no_verify = runner.invoke(app, ["execute", "Fix CSS spacing", "--file", "sample.py", "--yes"])
    unsafe = runner.invoke(
        app, ["execute", "Fix CSS spacing", "--file", "../outside.py", "--verify", "ruff", "--yes"]
    )
    protected = runner.invoke(
        app, ["execute", "Fix CSS spacing", "--file", ".env", "--verify", "ruff", "--yes"]
    )
    direct_codex = runner.invoke(app, ["execute", "Fix authentication bypass", "--yes"])

    assert no_files.exit_code == no_verify.exit_code == unsafe.exit_code == protected.exit_code == 2
    assert "No files selected" in no_files.stdout and "verification check" in no_verify.stdout
    assert "Unsafe selected file" in unsafe.stdout and "Protected selected file" in protected.stdout
    assert direct_codex.exit_code == 0 and "Direct Codex coding execution" in direct_codex.stdout
    assert provider.calls == runtime.execute_calls == 0


def test_execute_failure_rolls_back_and_optional_codex_analysis_stays_unresolved(
    git_repository: Path, monkeypatch
):
    target = git_repository / "sample.py"
    target.write_bytes(b"value = 1\n")
    provider, runtime = FakeProvider(_proposal("value = 1\n")), FakeRuntime()
    _patch_dependencies(monkeypatch, provider, runtime, VerificationStatus.FAILED)
    monkeypatch.chdir(git_repository)

    result = runner.invoke(
        app,
        [
            "execute",
            "Fix parser regression",
            "--file",
            "sample.py",
            "--verify",
            "ruff",
            "--yes",
            "--codex-analysis",
        ],
    )

    assert result.exit_code == 1
    assert "Codex analysis: succeeded (read-only)" in result.stdout
    assert "Workspace: restored" in result.stdout and "Task: UNRESOLVED" in result.stdout
    assert provider.calls == runtime.health_calls == runtime.execute_calls == 1
    assert target.read_bytes() == b"value = 1\n" and not (git_repository / ".car-context").exists()


def test_execute_provider_unavailable_reports_unchanged_workspace(
    git_repository: Path, monkeypatch
):
    target = git_repository / "sample.py"
    target.write_bytes(b"value = 1\n")
    provider = FakeProvider(_proposal("value = 1\n"), ProviderStatus.MISSING_CREDENTIALS)
    runtime = FakeRuntime()
    _patch_dependencies(monkeypatch, provider, runtime, VerificationStatus.PASSED)
    monkeypatch.chdir(git_repository)

    result = runner.invoke(
        app, ["execute", "Fix CSS spacing", "--file", "sample.py", "--verify", "ruff", "--yes"]
    )

    assert result.exit_code == 1 and "Reason: Provider unavailable" in result.stdout
    assert "Workspace: unchanged" in result.stdout and "Task: UNRESOLVED" in result.stdout
    assert provider.calls == runtime.health_calls == runtime.execute_calls == 0
    assert target.read_bytes() == b"value = 1\n"


def test_execute_maps_invocation_scoped_codex_write_authorization_and_paths(
    git_repository: Path, monkeypatch
):
    target = git_repository / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")
    provider, runtime = FakeProvider(_proposal("value = 1\n")), FakeRuntime()
    _patch_dependencies(monkeypatch, provider, runtime, VerificationStatus.PASSED)
    captured = {}
    original_execute = CodingFlowGateway.execute

    def capture_execute(self, request, authorization=None):
        captured["request"] = request
        return original_execute(self, request, authorization)

    monkeypatch.setattr(CodingFlowGateway, "execute", capture_execute)
    monkeypatch.chdir(git_repository)

    result = runner.invoke(
        app,
        [
            "execute",
            "Fix CSS spacing",
            "--file",
            "sample.py",
            "--verify",
            "ruff",
            "--yes",
            "--allow-codex-write",
            "--codex-write-path",
            "sample.py",
        ],
    )

    request = captured["request"]
    assert result.exit_code == 0 and "ENABLED FOR THIS RUN" in result.stdout
    assert request.codex_write_authorization.authorized is True
    assert request.codex_write_paths == ("sample.py",)
    assert request.codex_write_policy.enabled is False


def test_execute_rejects_incomplete_or_unconsented_codex_write_flags(
    git_repository: Path, monkeypatch
):
    monkeypatch.chdir(git_repository)

    missing_scope = runner.invoke(app, ["execute", "Fix CSS spacing", "--allow-codex-write"])
    missing_consent = runner.invoke(
        app, ["execute", "Fix CSS spacing", "--codex-write-path", "sample.py"]
    )

    assert missing_scope.exit_code == missing_consent.exit_code == 2
    assert "requires at least one --codex-write-path" in missing_scope.stdout
    assert "requires --allow-codex-write" in missing_consent.stdout
