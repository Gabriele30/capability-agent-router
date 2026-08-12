"""Offline execution tests for the three internal benchmark strategies."""

import subprocess
from datetime import UTC, datetime
from pathlib import Path

from car.benchmark import BenchmarkCase, BenchmarkRunner, BenchmarkStrategy
from car.benchmark.aggregation import aggregate_benchmark
from car.benchmark.context import build_execution_context
from car.benchmark.executors import BenchmarkExecutionDependencies, CARBenchmarkExecutor
from car.benchmark.models import BenchmarkRunMetadata
from car.benchmark.results import BenchmarkFailureKind
from car.benchmark.workspace import BenchmarkWorkspaceSet
from car.codex_write.models import CodexWritePolicy
from car.codex_write.pipeline import ControlledCodexWritePipeline
from car.codex_write.runtime_models import ControlledCodexWriteResult
from car.coding.models import CodingProposal, FileChangeOperation, ProposedFileChange
from car.providers.models import ProviderCapabilities, ProviderHealth, ProviderStatus
from car.telemetry import AttemptCapability, TokenUsage
from car.telemetry.models import UsageSource


class _Provider:
    name = "synthetic-gemini"

    def __init__(
        self,
        replacement: str,
        *,
        model: str | None = None,
        usage: TokenUsage | None = None,
    ) -> None:
        self.replacement = replacement
        self.model = model
        self.last_usage = usage
        self.calls = 0

    def capabilities(self):
        return ProviderCapabilities(supports_code_changes=True)

    def health(self):
        return ProviderHealth(status=ProviderStatus.CONFIGURED)

    def propose(self, context):
        self.calls += 1
        before = context.files[0].content.rstrip()
        return CodingProposal(
            summary="synthetic benchmark change",
            changes=[
                ProposedFileChange(
                    path="target.py",
                    operation=FileChangeOperation.MODIFY,
                    patch=(
                        "--- a/target.py\n+++ b/target.py\n@@ -1 +1 @@\n"
                        f"-{before}\n+{self.replacement}\n"
                    ),
                )
            ],
        )


class _ControlledRuntime:
    def __init__(self, replacement: str = "value = 2\n") -> None:
        self.replacement = replacement
        self.calls = 0
        self.models: list[str | None] = []

    def execute(self, request, authorization):
        self.calls += 1
        self.models.append(request.model)
        (request.workspace.workspace.path / "target.py").write_text(
            self.replacement, encoding="utf-8"
        )
        return ControlledCodexWriteResult(
            attempted=True,
            process_succeeded=True,
            baseline_digest=request.workspace.baseline_digest,
            baseline_head_oid=request.workspace.baseline_head_oid,
        )


class _ReadOnlyRuntime:
    def health(self):
        raise AssertionError("read-only Codex must not run in this benchmark")

    def execute(self, request):
        raise AssertionError("read-only Codex must not run in this benchmark")


class _UnauthorizedRuntime:
    def __init__(self, usage: TokenUsage | None = None) -> None:
        self.usage = usage

    def execute(self, request, authorization):
        (request.workspace.workspace.path / "unauthorized.py").write_text(
            "marker = 'rejected'\n", encoding="utf-8"
        )
        return ControlledCodexWriteResult(
            attempted=True,
            process_succeeded=True,
            usage=self.usage,
            baseline_digest=request.workspace.baseline_digest,
            baseline_head_oid=request.workspace.baseline_head_oid,
        )


def _fixture(tmp_path: Path) -> Path:
    root = tmp_path / "fixture"
    root.mkdir()
    (root / "target.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=benchmark@example.invalid",
            "-c",
            "user.name=CAR Benchmark",
            "commit",
            "-m",
            "baseline",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return root


def _case(root: Path) -> BenchmarkCase:
    return BenchmarkCase(
        id="parser-regression",
        category="bugfix",
        task="Fix parser regression",
        fixture=root.name,
        authorized_paths=("target.py",),
        verification=("ruff",),
    )


def _executor(provider: _Provider, runtime: _ControlledRuntime) -> CARBenchmarkExecutor:
    return CARBenchmarkExecutor(
        BenchmarkExecutionDependencies(
            coding_provider=provider,
            codex_runtime=_ReadOnlyRuntime(),
            controlled_pipeline=ControlledCodexWritePipeline(runtime=runtime),
            codex_write_policy=CodexWritePolicy(enabled=True),
        )
    )


def test_gemini_only_verified_success_uses_real_application(tmp_path: Path):
    fixture = _fixture(tmp_path)
    spaces = BenchmarkWorkspaceSet(fixture)
    try:
        telemetry = _executor(_Provider("value = 2"), _ControlledRuntime()).execute(
            _case(fixture),
            spaces.workspaces[BenchmarkStrategy.GEMINI_ONLY],
            BenchmarkStrategy.GEMINI_ONLY,
        )
        assert telemetry.verified_success is True
        assert [item.capability for item in telemetry.attempts] == [AttemptCapability.GEMINI]
    finally:
        spaces.cleanup()


def test_gemini_only_failure_rolls_back_without_codex(tmp_path: Path):
    fixture = _fixture(tmp_path)
    spaces = BenchmarkWorkspaceSet(fixture)
    runtime = _ControlledRuntime()
    workspace = spaces.workspaces[BenchmarkStrategy.GEMINI_ONLY]
    before = (workspace / "target.py").read_bytes()
    try:
        telemetry = _executor(_Provider("value = ("), runtime).execute(
            _case(fixture), workspace, BenchmarkStrategy.GEMINI_ONLY
        )
        assert telemetry.verified_success is False
        assert [item.capability for item in telemetry.attempts] == [AttemptCapability.GEMINI]
        assert runtime.calls == 0
        assert (workspace / "target.py").read_bytes() == before
        assert (fixture / "target.py").read_text(encoding="utf-8") == "value = 1\n"
    finally:
        spaces.cleanup()


def test_codex_only_failure_uses_controlled_rollback(tmp_path: Path):
    fixture = _fixture(tmp_path)
    spaces = BenchmarkWorkspaceSet(fixture)
    runtime = _ControlledRuntime("value = (\n")
    workspace = spaces.workspaces[BenchmarkStrategy.CODEX_ONLY]
    before = (workspace / "target.py").read_bytes()
    try:
        telemetry = _executor(_Provider("value = 2"), runtime).execute(
            _case(fixture), workspace, BenchmarkStrategy.CODEX_ONLY
        )
        assert telemetry.verified_success is False
        assert [item.capability for item in telemetry.attempts] == [
            AttemptCapability.CODEX_CONTROLLED_WRITE
        ]
        assert runtime.calls == 1
        assert (workspace / "target.py").read_bytes() == before
        assert (fixture / "target.py").read_text(encoding="utf-8") == "value = 1\n"
    finally:
        spaces.cleanup()


def test_codex_only_preserves_validated_rejected_paths_in_benchmark_json(tmp_path: Path):
    fixture = _fixture(tmp_path)
    usage = TokenUsage(
        input_tokens=12,
        cached_input_tokens=8,
        output_tokens=3,
        reasoning_tokens=1,
        source=UsageSource.PROVIDER_REPORTED,
    )
    executor = CARBenchmarkExecutor(
        BenchmarkExecutionDependencies(
            coding_provider=_Provider("value = 2"),
            codex_runtime=_ReadOnlyRuntime(),
            controlled_pipeline=ControlledCodexWritePipeline(runtime=_UnauthorizedRuntime(usage)),
            codex_write_policy=CodexWritePolicy(enabled=True),
        )
    )
    result = BenchmarkRunner(executor).run_case(
        _case(fixture), fixture, (BenchmarkStrategy.CODEX_ONLY,)
    )[0]

    assert result.verified_success is False
    assert result.failure_kind == BenchmarkFailureKind.TASK_FAILED
    assert result.telemetry.attempts[0].failure_kind == "unauthorized_change"
    assert result.telemetry.attempts[0].model is None
    assert result.telemetry.attempts[0].usage == usage
    assert result.cost_complete is False
    assert result.reference_cost and result.reference_cost.reference_inference_cost_usd is None
    assert result.rejected_paths == ("unauthorized.py",)
    report = aggregate_benchmark(
        BenchmarkRunMetadata(
            run_id="test-run",
            manifest_hash="a" * 64,
            car_version="0.6.0",
            started_at=datetime.now(UTC),
            strategies=(BenchmarkStrategy.CODEX_ONLY,),
            price_catalog_version="2026-08-11",
            price_catalog_verified_on="2026-08-11",
            cost_basis="public_api_list_price",
        ),
        (result,),
    )
    serialized = report.model_dump_json()
    assert '"rejected_paths":["unauthorized.py"]' in serialized
    assert str(fixture.resolve()) not in serialized
    assert "marker = 'rejected'" not in serialized
    assert "stdout" not in serialized
    for forbidden in ("marker = 'rejected'", str(fixture.resolve()), "stdout", "stderr"):
        assert forbidden not in serialized


def test_car_gemini_success_does_not_invoke_codex(tmp_path: Path):
    fixture = _fixture(tmp_path)
    spaces = BenchmarkWorkspaceSet(fixture)
    provider = _Provider("value = 2")
    runtime = _ControlledRuntime()
    try:
        telemetry = _executor(provider, runtime).execute(
            _case(fixture),
            spaces.workspaces[BenchmarkStrategy.CAR],
            BenchmarkStrategy.CAR,
        )
        assert telemetry.verified_success is True
        assert [item.capability for item in telemetry.attempts] == [AttemptCapability.GEMINI]
        assert provider.calls == 1
        assert runtime.calls == 0
    finally:
        spaces.cleanup()


def test_gemini_usage_and_model_propagate_to_benchmark_telemetry(tmp_path: Path):
    fixture = _fixture(tmp_path)
    usage = TokenUsage(
        input_tokens=10,
        output_tokens=4,
        reasoning_tokens=2,
        cached_input_tokens=3,
        total_tokens=16,
        source=UsageSource.PROVIDER_REPORTED,
    )
    provider = _Provider("value = 2", model="gemini-3.6-flash", usage=usage)
    spaces = BenchmarkWorkspaceSet(fixture)
    try:
        executor = _executor(provider, _ControlledRuntime())
        gemini = executor.execute(
            _case(fixture),
            spaces.workspaces[BenchmarkStrategy.GEMINI_ONLY],
            BenchmarkStrategy.GEMINI_ONLY,
        )
        car = executor.execute(
            _case(fixture),
            spaces.workspaces[BenchmarkStrategy.CAR],
            BenchmarkStrategy.CAR,
        )
        for telemetry in (gemini, car):
            attempt = telemetry.attempts[0]
            assert attempt.model == "gemini-3.6-flash"
            assert attempt.usage == usage
        assert provider.calls == 2
    finally:
        spaces.cleanup()


def test_three_strategy_runner_isolated_authoritative_and_offline(tmp_path: Path):
    fixture = _fixture(tmp_path)
    original = BenchmarkWorkspaceSet.identity(fixture)
    provider = _Provider("value = (")
    runtime = _ControlledRuntime()
    results = BenchmarkRunner(_executor(provider, runtime)).run_case(_case(fixture), fixture)

    assert [result.strategy for result in results] == list(BenchmarkStrategy)
    assert [result.verified_success for result in results] == [False, True, True]
    assert results[0].failure_kind == BenchmarkFailureKind.TASK_FAILED
    assert all(
        result.telemetry is not None and result.reference_cost is not None for result in results
    )
    assert [item.capability for item in results[0].telemetry.attempts] == [AttemptCapability.GEMINI]
    assert [item.capability for item in results[1].telemetry.attempts] == [
        AttemptCapability.CODEX_CONTROLLED_WRITE
    ]
    assert [item.capability for item in results[2].telemetry.attempts] == [
        AttemptCapability.GEMINI,
        AttemptCapability.CODEX_CONTROLLED_WRITE,
    ]
    assert provider.calls == 2
    assert runtime.calls == 2
    assert not results[1].cost_complete
    assert results[1].reference_cost.reference_inference_cost_usd is None
    assert BenchmarkWorkspaceSet.identity(fixture) == original
    serialized = "".join(result.model_dump_json() for result in results)
    assert str(fixture.resolve()) not in serialized


def test_pinned_codex_model_is_shared_by_codex_only_and_car_escalation(tmp_path: Path):
    fixture = _fixture(tmp_path)
    runtime = _ControlledRuntime()
    executor = CARBenchmarkExecutor(
        BenchmarkExecutionDependencies(
            coding_provider=_Provider("value = ("),
            codex_runtime=_ReadOnlyRuntime(),
            controlled_pipeline=ControlledCodexWritePipeline(runtime=runtime),
            codex_write_policy=CodexWritePolicy(enabled=True),
            codex_model="gpt-5.6-sol",
        )
    )
    results = BenchmarkRunner(executor).run_case(
        _case(fixture), fixture, (BenchmarkStrategy.CODEX_ONLY, BenchmarkStrategy.CAR)
    )
    assert runtime.models == ["gpt-5.6-sol", "gpt-5.6-sol"]
    assert [result.telemetry.attempts[-1].model for result in results] == [
        "gpt-5.6-sol",
        "gpt-5.6-sol",
    ]


def test_context_uses_real_workspace_repository_and_same_verification(tmp_path: Path):
    fixture = _fixture(tmp_path)
    spaces = BenchmarkWorkspaceSet(fixture)
    try:
        context = build_execution_context(
            _case(fixture), spaces.workspaces[BenchmarkStrategy.CAR], BenchmarkStrategy.CAR
        )
        assert context.repository.root == spaces.workspaces[BenchmarkStrategy.CAR].resolve()
        assert context.coding.files[0].path == "target.py"
        assert context.verification.commands[0].args == ["ruff", "check", "target.py"]
    finally:
        spaces.cleanup()


def test_invalid_execution_context_is_an_invariant_failure(tmp_path: Path):
    fixture = _fixture(tmp_path)
    case = _case(fixture).model_copy(update={"authorized_paths": ("missing.py",)})
    results = BenchmarkRunner(_executor(_Provider("value = 2"), _ControlledRuntime())).run_case(
        case, fixture
    )

    assert all(result.failure_kind == BenchmarkFailureKind.INVARIANT_FAILED for result in results)
    assert all(result.attempt_count == 0 for result in results)
