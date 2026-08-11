"""Manual, opt-in real Gemini -> controlled Codex write integration validation."""

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.live


def test_real_gemini_to_controlled_codex_chain(tmp_path: Path) -> None:
    """Runs no network, process, or worktree operation before the dedicated gate."""
    if os.environ.get("CAR_RUN_LIVE_GEMINI_TO_CONTROLLED_CODEX_TESTS") != "1":
        pytest.skip("Dedicated Gemini-to-controlled-Codex live opt-in is not enabled.")

    from car.application.codex import CodexExecutionPolicy
    from car.application.coding import execute_coding_pipeline
    from car.application.post_failure import (
        PostFailurePipelineOutcome,
        process_verified_coding_outcome,
    )
    from car.codex.runtime import LocalCodexRuntime
    from car.codex_write.models import CodexWriteAuthorization, CodexWritePolicy
    from car.codex_write.runtime import ControlledCodexWriteRuntime
    from car.codex_write.workspace import IsolatedWorkspaceManager
    from car.coding.gemini import GeminiCodingProvider
    from car.coding.models import CodingExecutionPolicy, CodingFileContext, CodingTaskContext
    from car.coding.verification import CodingVerificationCoordinator
    from car.config.models import CarConfig
    from car.execution.models import CommandResult, CommandSpec
    from car.providers.models import RepositoryClassificationContext
    from car.repository.models import GitState, LanguageStats, ProjectSignals, RepositoryState
    from car.router.consultation import (
        DecisionSource,
        ProviderConsultationResult,
        RoutingEvaluation,
    )
    from car.router.models import (
        Complexity,
        RiskAssessment,
        RiskLevel,
        Route,
        RoutingDecision,
        ScopeEstimate,
        ScopeSize,
        TaskCategory,
    )
    from car.verification.models import VerificationPlan, VerificationResult, VerificationStatus

    config_path = Path.cwd() / ".car-context" / "config.json"
    config = (
        CarConfig.model_validate_json(config_path.read_text())
        if config_path.exists()
        else CarConfig()
    )
    gemini = config.providers.gemini
    if not gemini.enabled or not gemini.model or not os.environ.get(gemini.api_key_env):
        pytest.skip("Gemini live prerequisites are unavailable.")

    manager = IsolatedWorkspaceManager()
    runtime = ControlledCodexWriteRuntime(
        workspace_manager=manager, policy=CodexWritePolicy(enabled=True)
    )
    health = runtime.health()
    if health.status.value in {"cli_not_found", "not_authenticated"}:
        pytest.skip("Local Codex CLI prerequisite is unavailable.")
    assert health.status.value == "ready"

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True
        ).stdout

    calculator = tmp_path / "calculator.py"
    tests = tmp_path / "test_calculator.py"
    calculator.write_text("def add(a: int, b: int) -> int:\n    return a - b\n")
    tests.write_text("from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True, capture_output=True)
    git("config", "user.email", "live@example.invalid")
    git("config", "user.name", "CAR Live")
    git("add", "calculator.py", "test_calculator.py")
    git("commit", "-m", "baseline")
    initial = calculator.read_bytes()
    head = git("rev-parse", "HEAD")
    index = git("diff", "--cached", "--binary")
    decision = RoutingDecision(
        route=Route.GEMINI_TO_CODEX,
        risk=RiskAssessment(score=0.4, level=RiskLevel.MEDIUM),
        complexity=Complexity.MEDIUM,
        scope=ScopeEstimate(size=ScopeSize.SMALL),
        confidence=0.7,
        reasons=["live"],
        matched_rules=["live"],
        categories=[TaskCategory.BUGFIX],
    )
    evaluation = RoutingEvaluation(
        deterministic_decision=decision,
        provider_consultation=ProviderConsultationResult(attempted=False, succeeded=False),
        final_decision=decision,
        deterministic_risk=0.4,
        final_risk=0.4,
        fusion_reasons=["live"],
        decision_sources=[DecisionSource.DETERMINISTIC],
    )
    context = CodingTaskContext(
        task=(
            "Fix calculator.py so add(a: int, b: int) returns the sum. "
            "Make the smallest possible change."
        ),
        route=Route.GEMINI_TO_CODEX,
        repository=RepositoryClassificationContext(
            name="calculator",
            branch="main",
            dirty=False,
            languages={"Python": 2},
            systems=["Python"],
        ),
        files=[CodingFileContext(path="calculator.py", content=initial.decode())],
        constraints=["Modify only calculator.py."],
    )
    command = CommandSpec(args=["python", "-m", "pytest"], cwd=str(tmp_path), timeout_seconds=60)

    class FaultEngine:
        def verify(self, plan, *, stop_on_failure=False):
            return VerificationResult(
                status=VerificationStatus.FAILED,
                checks=[
                    CommandResult(
                        command=command, exit_code=1, stderr="test-only deterministic fault"
                    )
                ],
                message="test-only deterministic Gemini verification failure",
            )

    gemini_result = execute_coding_pipeline(
        repository_root=tmp_path,
        routing_evaluation=evaluation,
        coding_context=context,
        coding_provider=GeminiCodingProvider(gemini),
        coding_policy=CodingExecutionPolicy(enabled=True),
        patch_validation_policy=None,
        verification_plan=VerificationPlan(commands=[command]),
        verification_coordinator=CodingVerificationCoordinator(FaultEngine()),
    )
    assert (
        gemini_result.attempted
        and gemini_result.verification
        and gemini_result.verification.rolled_back
    )
    assert (
        calculator.read_bytes() == initial
        and git("rev-parse", "HEAD") == head
        and git("diff", "--cached", "--binary") == index
    )
    repository = RepositoryState(
        root=tmp_path,
        name="calculator",
        git=GitState(available=True, is_repository=True, branch="main"),
        languages=LanguageStats(counts={"Python": 2}),
        project_signals=ProjectSignals(systems=["Python"]),
    )
    result = process_verified_coding_outcome(
        task=context.task,
        routing_evaluation=evaluation,
        repository_state=repository,
        coding_context=context,
        coding_attempt=gemini_result.coding_attempt,
        patch_validation=gemini_result.patch_validation,
        patch_apply=gemini_result.patch_apply,
        verification=gemini_result.verification,
        codex_runtime=LocalCodexRuntime(),
        codex_execution_policy=CodexExecutionPolicy(enabled=False),
        verification_plan=VerificationPlan(commands=[command]),
        codex_write_policy=CodexWritePolicy(enabled=True),
        codex_write_authorization=CodexWriteAuthorization(authorized=True),
        codex_write_paths=("calculator.py",),
    )
    assert (
        result.selected_codex_mode == "controlled_write"
        and result.controlled_write
        and result.controlled_write.accepted
    )
    assert calculator.read_text() == "def add(a: int, b: int) -> int:\n    return a + b\n"
    assert result.outcome == PostFailurePipelineOutcome.CODEX_CONTROLLED_WRITE_SUCCEEDED
