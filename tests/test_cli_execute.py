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

    def __init__(self, proposal: CodingProposal) -> None:
        self.proposal = proposal
        self.calls = 0

    def capabilities(self):
        return ProviderCapabilities(supports_code_changes=True)

    def health(self):
        return ProviderHealth(status=ProviderStatus.CONFIGURED)

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
    assert "coding task verified" in result.stdout and provider.calls == 1
    assert runtime.health_calls == runtime.execute_calls == 0
    assert target.read_text(encoding="utf-8") == "value = 2\n"


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
    assert "Codex read-only analysis: succeeded" in result.stdout
    assert "Task remains unresolved" in result.stdout
    assert provider.calls == runtime.health_calls == runtime.execute_calls == 1
    assert target.read_bytes() == b"value = 1\n" and not (git_repository / ".car-context").exists()
