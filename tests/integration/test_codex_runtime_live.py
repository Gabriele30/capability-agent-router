"""Explicit opt-in validation of the local, read-only Codex CLI transport."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

if os.getenv("CAR_RUN_LIVE_CODEX_TESTS") != "1":
    pytest.skip(
        "set CAR_RUN_LIVE_CODEX_TESTS=1 to validate the local Codex CLI runtime",
        allow_module_level=True,
    )


from car.codex.models import (  # noqa: E402
    CodexExecutionRequest,
    CodexRuntimeHealthStatus,
)
from car.codex.runtime import LocalCodexRuntime  # noqa: E402
from car.escalation.models import (  # noqa: E402
    CodexHandoff,
    CodingAttemptSummary,
    EscalationReason,
    PatchAttemptSummary,
    RepositoryHandoffSummary,
    RoutingHandoffSummary,
    VerificationHandoffSummary,
)
from car.router.models import Route  # noqa: E402


def _synthetic_handoff() -> CodexHandoff:
    return CodexHandoff(
        task=(
            "Review example.py and explain one small improvement that could make the "
            "function easier to document or maintain. Do not modify any files."
        ),
        routing=RoutingHandoffSummary(
            deterministic_route=Route.GEMINI_TO_CODEX,
            final_route=Route.GEMINI_TO_CODEX,
            decision_sources=["synthetic"],
            fusion_reasons=["synthetic_live_transport_validation"],
            provider_influenced_decision=False,
            deterministic_risk=0.4,
            final_risk=0.4,
        ),
        repository=RepositoryHandoffSummary(
            name="synthetic-codex-runtime-repo",
            branch="main",
            dirty=False,
            languages={"Python": 1},
            systems=["Python"],
        ),
        selected_files=["example.py"],
        coding_attempt=CodingAttemptSummary(
            provider="synthetic-provider",
            attempted=True,
            succeeded=True,
            proposal_summary="Add a short docstring to example.py.",
            reasons=["synthetic prior proposal"],
        ),
        patch_attempt=PatchAttemptSummary(
            paths=["example.py"],
            operations=["modify"],
            diffs=['@@ -1,2 +1,3 @@\n+"""Add two numbers."""'],
            validation_valid=True,
            apply_succeeded=True,
        ),
        verification=VerificationHandoffSummary(
            executed_checks=[
                {
                    "command": ["ruff", "check", "example.py"],
                    "exit_code": 1,
                    "timeout": False,
                    "stdout": "synthetic verification failure",
                    "stderr": "synthetic diagnostic",
                }
            ],
            failure_kind="check_failed",
            rollback_attempted=True,
            rollback_succeeded=True,
        ),
        escalation_reason=EscalationReason.VERIFICATION_FAILED,
        recommended_next_step="Provide a read-only corrective recommendation.",
    )


def _source_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        check=False,
        shell=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        pytest.skip("Git is unavailable for synthetic live Codex validation")
    return completed.stdout


@pytest.mark.live
def test_local_codex_runtime_is_read_only_in_synthetic_repository(tmp_path: Path):
    if shutil.which("git") is None:
        pytest.skip("Git is unavailable for synthetic live Codex validation")
    _git(tmp_path, "init", "-q")
    example = tmp_path / "example.py"
    example.write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n",
        encoding="utf-8",
    )
    before_files = _source_snapshot(tmp_path)
    before_status = _git(tmp_path, "status", "--porcelain")
    runtime = LocalCodexRuntime()
    health = runtime.health()
    if health.status != CodexRuntimeHealthStatus.READY:
        pytest.skip(f"local Codex runtime is not ready: {health.status.value}")

    result = runtime.execute(
        CodexExecutionRequest(
            repository_root=tmp_path,
            handoff=_synthetic_handoff(),
            timeout_seconds=90,
        )
    )

    assert result.attempted is True
    assert result.succeeded is True
    assert result.failure_kind is None
    assert result.timed_out is False
    assert result.exit_code == 0
    assert result.final_message is not None and result.final_message.strip()
    assert _source_snapshot(tmp_path) == before_files
    assert _git(tmp_path, "status", "--porcelain") == before_status
