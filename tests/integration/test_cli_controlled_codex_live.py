"""Manual opt-in validation of the public Gemini-to-controlled-Codex CLI path."""

import os
import shutil
import subprocess
from importlib import import_module
from pathlib import Path

import pytest
from typer.testing import CliRunner

if os.getenv("CAR_RUN_LIVE_CLI_CONTROLLED_CODEX_TESTS") != "1":
    pytest.skip(
        "set CAR_RUN_LIVE_CLI_CONTROLLED_CODEX_TESTS=1 for live CLI controlled-Codex validation",
        allow_module_level=True,
    )


from car.application.execution_gateway import CodingFlowGateway  # noqa: E402
from car.codex_write.models import CodexWritePolicy  # noqa: E402
from car.codex_write.runtime import ControlledCodexWriteRuntime  # noqa: E402
from car.codex_write.runtime_models import ControlledCodexHealthStatus  # noqa: E402
from car.codex_write.workspace import IsolatedWorkspaceManager  # noqa: E402
from car.coding.gemini import GeminiCodingProvider  # noqa: E402
from car.coding.verification import CodingVerificationCoordinator  # noqa: E402
from car.config.models import CarConfig  # noqa: E402
from car.execution.models import CommandResult  # noqa: E402
from car.providers.models import ProviderStatus  # noqa: E402
from car.router.models import Route  # noqa: E402
from car.verification.models import VerificationResult, VerificationStatus  # noqa: E402

TASK = (
    "Fix the parser regression in calculator.py so that add correctly adds "
    "the two arguments. Modify only calculator.py. Make the smallest change and "
    "do not create files."
)
runner = CliRunner()


class _DeterministicGeminiVerificationFailure:
    """Test-only engine: forces rollback after real Gemini patch application."""

    def verify(self, plan, stop_on_failure=True):
        return VerificationResult(
            status=VerificationStatus.FAILED,
            checks=[
                CommandResult(
                    command=plan.commands[0], exit_code=1, stderr="test-only verification fault"
                )
            ],
            message="test-only deterministic Gemini verification failure",
        )


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        check=True,
        shell=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout


def _live_config(source_root: Path) -> CarConfig:
    config_path = source_root / ".car-context" / "config.json"
    if not config_path.is_file():
        pytest.skip("live CLI validation requires local CAR Gemini configuration")
    config = CarConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
    if GeminiCodingProvider(config.providers.gemini).health().status != ProviderStatus.CONFIGURED:
        pytest.skip("live CLI validation requires locally configured Gemini")
    return config.model_copy(update={"codex_write": CodexWritePolicy(enabled=True)})


def _assert_no_artifacts(root: Path) -> None:
    forbidden_names = {"__pycache__", ".pytest_cache", "htmlcov", "build", "dist", ".coverage"}
    artifacts = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if ".git" not in path.relative_to(root).parts
        and (path.name in forbidden_names or path.suffix == ".pyc")
    ]
    assert artifacts == []


@pytest.mark.live
def test_cli_execute_real_gemini_to_controlled_codex_write(tmp_path: Path, monkeypatch) -> None:
    """Exercise the public CLI; only the first Gemini verification is test-faulted."""
    source_root = Path.cwd()
    config = _live_config(source_root)
    if shutil.which("git") is None:
        pytest.skip("Git is unavailable for live CLI controlled-Codex validation")
    health = ControlledCodexWriteRuntime(
        workspace_manager=IsolatedWorkspaceManager(), policy=config.codex_write
    ).health()
    if health.status in {
        ControlledCodexHealthStatus.CLI_NOT_FOUND,
        ControlledCodexHealthStatus.NOT_AUTHENTICATED,
    }:
        pytest.skip(f"local Codex CLI prerequisite is unavailable: {health.status.value}")
    assert health.status == ControlledCodexHealthStatus.READY

    calculator = tmp_path / "calculator.py"
    test_file = tmp_path / "test_calculator.py"
    calculator.write_text("def add(a: int, b: int) -> int:\n    return a - b\n", encoding="utf-8")
    test_file.write_text(
        "from calculator import add\n\n\ndef test_add() -> None:\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "live@example.invalid")
    _git(tmp_path, "config", "user.name", "CAR Live")
    _git(tmp_path, "add", "calculator.py", "test_calculator.py")
    _git(tmp_path, "commit", "-qm", "baseline")
    before_calculator = calculator.read_bytes()
    before_test = test_file.read_bytes()
    before_head = _git(tmp_path, "rev-parse", "HEAD")
    before_branch = _git(tmp_path, "branch", "--show-current")
    before_index = _git(tmp_path, "diff", "--cached", "--binary")

    external_context = tmp_path.parent / f"{tmp_path.name}-car-context"
    external_context.mkdir()
    (external_context / "config.json").write_text(config.model_dump_json(), encoding="utf-8")
    cli = import_module("car.cli.app")
    monkeypatch.setattr(
        cli,
        "_context_paths",
        lambda root: (
            external_context,
            external_context / "config.json",
            external_context / "state.json",
        ),
    )
    captured = []

    def build_gateway(provider, runtime):
        gateway = CodingFlowGateway(
            provider,
            runtime,
            verification_coordinator=CodingVerificationCoordinator(
                _DeterministicGeminiVerificationFailure()
            ),
        )
        execute = gateway.execute

        def capture_execute(request, authorization=None):
            result = execute(request, authorization)
            captured.append((request, result))
            return result

        gateway.execute = capture_execute
        return gateway

    monkeypatch.setattr(cli, "_build_coding_flow_gateway", build_gateway)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        cli.app,
        [
            "execute",
            TASK,
            "--file",
            "calculator.py",
            "--verify",
            "pytest",
            "--yes",
            "--allow-codex-write",
            "--codex-write-path",
            "calculator.py",
        ],
    )

    assert result.exit_code == 0, result.stdout[-2_000:]
    assert "Resolved by: Codex controlled write" in result.stdout
    assert "Verification: passed" in result.stdout
    assert "Workspace: updated and accepted" in result.stdout
    assert len(captured) == 1
    request, gateway = captured[0]
    assert request.codex_write_policy.enabled
    assert request.codex_write_authorization.authorized
    assert request.codex_write_paths == ("calculator.py",)
    flow = gateway.flow_result
    assert gateway.authorized and gateway.attempted and gateway.succeeded
    assert flow is not None and flow.succeeded
    assert flow.outcome.value == "codex_controlled_write_succeeded"
    pipeline = flow.coding.pipeline_result
    assert pipeline is not None and pipeline.route == Route.GEMINI_TO_CODEX
    assert pipeline.patch_apply is not None and pipeline.patch_apply.succeeded
    assert pipeline.verification is not None and pipeline.verification.rolled_back
    assert calculator.read_bytes() != before_calculator
    assert (
        calculator.read_text(encoding="utf-8")
        == "def add(a: int, b: int) -> int:\n    return a + b\n"
    )
    assert test_file.read_bytes() == before_test
    assert _git(tmp_path, "rev-parse", "HEAD") == before_head
    assert _git(tmp_path, "branch", "--show-current") == before_branch
    assert _git(tmp_path, "diff", "--cached", "--binary") == before_index
    assert _git(tmp_path, "status", "--porcelain").splitlines() == [" M calculator.py"]
    assert not (tmp_path / ".car-context").exists()
    _assert_no_artifacts(tmp_path)
