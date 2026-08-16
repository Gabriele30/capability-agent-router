"""Offline execution tests for the three internal benchmark strategies."""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from car.application.coding import CodingPipelineOutcome
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
from car.coding.base import CodingProviderFailure
from car.coding.models import CodingProposal, FileChangeOperation, ProposedFileChange
from car.providers.models import (
    ProviderCapabilities,
    ProviderErrorKind,
    ProviderHealth,
    ProviderStatus,
)
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
        error: ProviderErrorKind | None = None,
    ) -> None:
        self.replacement = replacement
        self.model = model
        self.last_usage = usage
        self.error = error
        self.calls = 0

    def capabilities(self):
        return ProviderCapabilities(supports_code_changes=True)

    def health(self):
        return ProviderHealth(status=ProviderStatus.CONFIGURED)

    def propose(self, context):
        self.calls += 1
        if self.error is not None:
            raise CodingProviderFailure(self.error)
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


class _TaskAndAuxiliaryProvider(_Provider):
    def propose(self, context):
        self.calls += 1
        before = context.files[0].content.rstrip()
        return CodingProposal(
            summary="synthetic task and auxiliary change",
            changes=[
                ProposedFileChange(
                    path="target.py",
                    operation=FileChangeOperation.MODIFY,
                    patch=(
                        "--- a/target.py\n+++ b/target.py\n@@ -1 +1 @@\n"
                        f"-{before}\n+{self.replacement}\n"
                    ),
                ),
                ProposedFileChange(
                    path=".gitignore",
                    operation=FileChangeOperation.MODIFY,
                    patch=("--- a/.gitignore\n+++ b/.gitignore\n@@ -1 +1 @@\n-.cache\n+.cache/\n"),
                ),
            ],
        )


class _InvalidProposalProvider(_Provider):
    def propose(self, context):
        self.calls += 1
        return object()


class _UnexpectedProvider(_Provider):
    def propose(self, context):
        self.calls += 1
        raise OSError("synthetic local provider fault")


class _ControlledRuntime:
    def __init__(
        self,
        replacement: str = "value = 2\n",
        usage: TokenUsage | None = None,
        auxiliary_path: str | None = None,
    ) -> None:
        self.replacement = replacement
        self.usage = usage
        self.auxiliary_path = auxiliary_path
        self.calls = 0
        self.models: list[str | None] = []
        self.efforts: list[object] = []
        self.scope_summaries: list[str | None] = []

    def execute(self, request, authorization):
        self.calls += 1
        self.models.append(request.model)
        self.efforts.append(request.reasoning_effort)
        self.scope_summaries.append(request.authorization_summary)
        auxiliary_exists = (
            self.auxiliary_path is not None
            and (request.workspace.workspace.path / self.auxiliary_path).exists()
        )
        (request.workspace.workspace.path / "target.py").write_text(
            self.replacement, encoding="utf-8"
        )
        if self.auxiliary_path:
            (request.workspace.workspace.path / self.auxiliary_path).write_text(
                ".cache/\n", encoding="utf-8"
            )
        changes = [
            {
                "path": "target.py",
                "operation": "modify",
                "patch": (
                    "--- a/target.py\n+++ b/target.py\n@@ -1 +1 @@\n"
                    f"-value = 1\n+{self.replacement.rstrip()}\n"
                ),
            }
        ]
        if self.auxiliary_path:
            before = ".cache" if auxiliary_exists else ""
            changes.append(
                {
                    "path": self.auxiliary_path,
                    "operation": "modify" if auxiliary_exists else "create",
                    "patch": (
                        f"--- {'a/' + self.auxiliary_path if auxiliary_exists else '/dev/null'}\n"
                        f"+++ b/{self.auxiliary_path}\n"
                        + (
                            f"@@ -1 +1 @@\n-{before}\n+.cache/\n"
                            if auxiliary_exists
                            else "@@ -0,0 +1 @@\n+.cache/\n"
                        )
                    ),
                }
            )
        return ControlledCodexWriteResult(
            attempted=True,
            process_succeeded=True,
            final_message=json.dumps({"summary": "synthetic Codex proposal", "changes": changes}),
            usage=self.usage,
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
            final_message=json.dumps(
                {
                    "summary": "synthetic unauthorized Codex proposal",
                    "changes": [
                        {
                            "path": "unauthorized.py",
                            "operation": "create",
                            "patch": (
                                "--- /dev/null\n+++ b/unauthorized.py\n"
                                "@@ -0,0 +1 @@\n+marker = 'rejected'\n"
                            ),
                        }
                    ],
                }
            ),
            usage=self.usage,
            baseline_digest=request.workspace.baseline_digest,
            baseline_head_oid=request.workspace.baseline_head_oid,
        )


class _InvalidProposalRuntime:
    def execute(self, request, authorization):
        return ControlledCodexWriteResult(
            attempted=True,
            process_succeeded=True,
            final_message=json.dumps(
                {
                    "summary": "synthetic invalid Codex proposal",
                    "changes": [
                        {
                            "path": "target.py",
                            "operation": "modify",
                            "patch": "SENSITIVE PATCH CONTENT",
                        }
                    ],
                }
            ),
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


def _case(root: Path, *, task: str = "Fix parser regression") -> BenchmarkCase:
    return BenchmarkCase(
        id="parser-regression",
        category="bugfix",
        task=task,
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


@pytest.mark.parametrize(
    "error_kind",
    (
        ProviderErrorKind.AUTHENTICATION_ERROR,
        ProviderErrorKind.RATE_LIMITED,
        ProviderErrorKind.QUOTA_EXHAUSTED,
        ProviderErrorKind.INVALID_REQUEST,
        ProviderErrorKind.TIMEOUT,
    ),
)
def test_gemini_only_preserves_safe_provider_failure_category(
    tmp_path: Path, error_kind: ProviderErrorKind
):
    fixture = _fixture(tmp_path)
    result = BenchmarkRunner(
        _executor(_Provider("value = 2", error=error_kind), _ControlledRuntime())
    ).run_case(_case(fixture), fixture, (BenchmarkStrategy.GEMINI_ONLY,))[0]

    assert result.verified_success is False
    assert result.telemetry and result.telemetry.attempts[0].failure_kind == "pipeline_failed"
    assert result.pipeline_outcome == CodingPipelineOutcome.CODING_PROVIDER_FAILED
    assert result.provider_error_kind == error_kind
    serialized = result.model_dump_json()
    assert error_kind.value in serialized
    for forbidden in (str(fixture.resolve()), "synthetic local provider fault", "stdout", "stderr"):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    ("provider", "expected"),
    (
        (_InvalidProposalProvider("value = 2"), ProviderErrorKind.INVALID_RESPONSE),
        (_UnexpectedProvider("value = 2"), ProviderErrorKind.UNKNOWN_ERROR),
    ),
)
def test_gemini_only_preserves_safe_local_proposal_failure_category(
    tmp_path: Path, provider: _Provider, expected: ProviderErrorKind
):
    fixture = _fixture(tmp_path)
    result = BenchmarkRunner(_executor(provider, _ControlledRuntime())).run_case(
        _case(fixture), fixture, (BenchmarkStrategy.GEMINI_ONLY,)
    )[0]

    assert result.pipeline_outcome == CodingPipelineOutcome.CODING_PROVIDER_FAILED
    assert result.provider_error_kind == expected
    assert provider.calls == 1


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


def test_benchmark_exports_safe_patch_violation_without_patch_content(tmp_path: Path):
    fixture = _fixture(tmp_path)
    executor = CARBenchmarkExecutor(
        BenchmarkExecutionDependencies(
            coding_provider=_Provider("value = 2"),
            codex_runtime=_ReadOnlyRuntime(),
            controlled_pipeline=ControlledCodexWritePipeline(runtime=_InvalidProposalRuntime()),
            codex_write_policy=CodexWritePolicy(enabled=True),
        )
    )
    result = BenchmarkRunner(executor).run_case(
        _case(fixture), fixture, (BenchmarkStrategy.CODEX_ONLY,)
    )[0]

    assert result.verified_success is False
    assert result.telemetry.attempts[0].failure_kind == "unauthorized_change"
    assert result.rejected_paths == ("target.py",)
    assert len(result.patch_violations) == 1
    violation = result.patch_violations[0]
    assert violation.kind.value == "invalid_diff"
    assert violation.path == "target.py"
    assert violation.summary == "missing unified diff file headers"
    serialized = result.model_dump_json()
    for forbidden in ("SENSITIVE PATCH CONTENT", str(fixture.resolve()), "stdout", "stderr"):
        assert forbidden not in serialized


def test_successful_benchmark_has_no_patch_violations(tmp_path: Path):
    fixture = _fixture(tmp_path)
    result = BenchmarkRunner(_executor(_Provider("value = 2"), _ControlledRuntime())).run_case(
        _case(fixture), fixture, (BenchmarkStrategy.CODEX_ONLY,)
    )[0]
    assert result.verified_success is True
    assert result.patch_violations == ()


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


def test_gemini_only_accepts_and_reports_safe_auxiliary_change(tmp_path: Path):
    fixture = _fixture(tmp_path)
    (fixture / ".gitignore").write_text(".cache\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore"], cwd=fixture, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=benchmark@example.invalid",
            "-c",
            "user.name=CAR Benchmark",
            "commit",
            "-m",
            "add ignore",
        ],
        cwd=fixture,
        check=True,
        capture_output=True,
    )
    result = BenchmarkRunner(
        _executor(_TaskAndAuxiliaryProvider("value = 2"), _ControlledRuntime())
    ).run_case(_case(fixture), fixture, (BenchmarkStrategy.GEMINI_ONLY,))[0]

    assert result.verified_success is True
    assert result.task_changed_paths == ("target.py",)
    assert result.auxiliary_changed_paths == (".gitignore",)


def test_car_direct_codex_route_uses_one_controlled_attempt_without_gemini(tmp_path: Path):
    fixture = _fixture(tmp_path)
    spaces = BenchmarkWorkspaceSet(fixture)
    provider = _Provider("value = 2", model="gemini-3.5-flash-lite")
    runtime = _ControlledRuntime()
    try:
        telemetry = CARBenchmarkExecutor(
            BenchmarkExecutionDependencies(
                coding_provider=provider,
                codex_runtime=_ReadOnlyRuntime(),
                controlled_pipeline=ControlledCodexWritePipeline(runtime=runtime),
                codex_write_policy=CodexWritePolicy(enabled=True),
                codex_model="gpt-5.6-sol",
            )
        ).execute(
            _case(fixture, task="Fix authentication bypass"),
            spaces.workspaces[BenchmarkStrategy.CAR],
            BenchmarkStrategy.CAR,
        )
        assert telemetry.initial_route.value == "codex"
        assert telemetry.final_route.value == "codex"
        assert telemetry.verified_success is True
        assert telemetry.escalated is False
        assert [attempt.capability for attempt in telemetry.attempts] == [
            AttemptCapability.CODEX_CONTROLLED_WRITE
        ]
        assert telemetry.attempts[0].provider == "codex"
        assert telemetry.attempts[0].model == "gpt-5.6-sol"
        assert provider.calls == 0 and runtime.calls == 1
    finally:
        spaces.cleanup()


def test_car_direct_codex_failure_preserves_controlled_write_result(tmp_path: Path):
    fixture = _fixture(tmp_path)
    spaces = BenchmarkWorkspaceSet(fixture)
    provider = _Provider("value = 2")
    runtime = _ControlledRuntime("value = (\n")
    workspace = spaces.workspaces[BenchmarkStrategy.CAR]
    before = (workspace / "target.py").read_bytes()
    try:
        telemetry = _executor(provider, runtime).execute(
            _case(fixture, task="Fix authentication bypass"), workspace, BenchmarkStrategy.CAR
        )
        assert telemetry.verified_success is False and telemetry.escalated is False
        assert telemetry.attempts[0].capability == AttemptCapability.CODEX_CONTROLLED_WRITE
        assert telemetry.attempts[0].failure_kind == "verification_failed"
        assert provider.calls == 0 and runtime.calls == 1
        assert (workspace / "target.py").read_bytes() == before
    finally:
        spaces.cleanup()


def test_car_safe_gemini_pipeline_failure_escalates_once_to_pinned_codex(tmp_path: Path):
    fixture = _fixture(tmp_path)
    spaces = BenchmarkWorkspaceSet(fixture)
    provider = _Provider("value = 2", error=ProviderErrorKind.TIMEOUT)
    runtime = _ControlledRuntime()
    try:
        telemetry = CARBenchmarkExecutor(
            BenchmarkExecutionDependencies(
                coding_provider=provider,
                codex_runtime=_ReadOnlyRuntime(),
                controlled_pipeline=ControlledCodexWritePipeline(runtime=runtime),
                codex_write_policy=CodexWritePolicy(enabled=True),
                codex_model="gpt-5.6-sol",
            )
        ).execute(_case(fixture), spaces.workspaces[BenchmarkStrategy.CAR], BenchmarkStrategy.CAR)
        assert telemetry.verified_success is True and telemetry.escalated is True
        assert telemetry.escalation_from == AttemptCapability.GEMINI
        assert telemetry.escalation_to == AttemptCapability.CODEX_CONTROLLED_WRITE
        assert [attempt.capability for attempt in telemetry.attempts] == [
            AttemptCapability.GEMINI,
            AttemptCapability.CODEX_CONTROLLED_WRITE,
        ]
        assert telemetry.attempts[0].failure_kind == "pipeline_failed"
        assert telemetry.attempts[1].model == "gpt-5.6-sol"
        assert provider.calls == runtime.calls == 1
    finally:
        spaces.cleanup()


def test_car_escalation_keeps_both_attempt_costs(tmp_path: Path):
    fixture = _fixture(tmp_path)
    gemini_usage = TokenUsage(
        input_tokens=100,
        output_tokens=10,
        source=UsageSource.PROVIDER_REPORTED,
    )
    codex_usage = TokenUsage(
        input_tokens=100,
        output_tokens=10,
        source=UsageSource.RUNTIME_REPORTED,
    )
    result = BenchmarkRunner(
        CARBenchmarkExecutor(
            BenchmarkExecutionDependencies(
                coding_provider=_Provider(
                    "value = (", model="gemini-3.5-flash-lite", usage=gemini_usage
                ),
                codex_runtime=_ReadOnlyRuntime(),
                controlled_pipeline=ControlledCodexWritePipeline(
                    runtime=_ControlledRuntime(usage=codex_usage)
                ),
                codex_write_policy=CodexWritePolicy(enabled=True),
                codex_model="gpt-5.6-sol",
            )
        )
    ).run_case(_case(fixture), fixture, (BenchmarkStrategy.CAR,))[0]

    assert [attempt.usage for attempt in result.telemetry.attempts] == [
        gemini_usage,
        codex_usage,
    ]
    assert result.cost_complete is True
    assert result.reference_cost is not None
    assert result.reference_cost.reference_inference_cost_usd is not None
    assert result.reference_cost.reference_inference_cost_usd > 0


def test_codex_only_and_car_share_safe_auxiliary_authorization(tmp_path: Path):
    fixture = _fixture(tmp_path)
    runtime = _ControlledRuntime(auxiliary_path=".gitignore")
    results = BenchmarkRunner(
        CARBenchmarkExecutor(
            BenchmarkExecutionDependencies(
                coding_provider=_Provider("value = ("),
                codex_runtime=_ReadOnlyRuntime(),
                controlled_pipeline=ControlledCodexWritePipeline(runtime=runtime),
                codex_write_policy=CodexWritePolicy(enabled=True),
            )
        )
    ).run_case(_case(fixture), fixture, (BenchmarkStrategy.CODEX_ONLY, BenchmarkStrategy.CAR))

    assert [result.verified_success for result in results] == [True, True]
    assert all(result.task_changed_paths == ("target.py",) for result in results)
    assert all(result.auxiliary_changed_paths == (".gitignore",) for result in results)
    assert runtime.calls == 2


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
    provider = _Provider("value = 2", model="gemini-3.5-flash-lite", usage=usage)
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
            assert attempt.model == "gemini-3.5-flash-lite"
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


def test_terra_effort_is_shared_by_codex_only_direct_and_car_fallback(tmp_path: Path):
    from car.codex_write.runtime_models import CodexReasoningEffort

    fixture = _fixture(tmp_path)
    runtime = _ControlledRuntime()
    executor = CARBenchmarkExecutor(
        BenchmarkExecutionDependencies(
            coding_provider=_Provider("value = ("),
            codex_runtime=_ReadOnlyRuntime(),
            controlled_pipeline=ControlledCodexWritePipeline(runtime=runtime),
            codex_write_policy=CodexWritePolicy(enabled=True),
            codex_model="gpt-5.6-terra",
            codex_reasoning_effort=CodexReasoningEffort.MEDIUM,
        )
    )
    results = BenchmarkRunner(executor).run_case(
        _case(fixture), fixture, (BenchmarkStrategy.CODEX_ONLY, BenchmarkStrategy.CAR)
    )
    spaces = BenchmarkWorkspaceSet(fixture)
    try:
        direct = executor.execute(
            _case(fixture, task="Fix authentication bypass"),
            spaces.workspaces[BenchmarkStrategy.CAR],
            BenchmarkStrategy.CAR,
        )
    finally:
        spaces.cleanup()

    assert runtime.models == ["gpt-5.6-terra", "gpt-5.6-terra", "gpt-5.6-terra"]
    assert runtime.efforts == [CodexReasoningEffort.MEDIUM] * 3
    assert [result.telemetry.attempts[-1].model for result in results] == [
        "gpt-5.6-terra",
        "gpt-5.6-terra",
    ]
    assert direct.attempts[0].model == "gpt-5.6-terra"


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


def test_large_benchmark_repository_keeps_context_bounded_and_finalizes_small_proposals(
    tmp_path: Path,
):
    fixture = _fixture(tmp_path)
    for number in range(30):
        (fixture / f"support_{number:02}.py").write_text(
            f"VALUE_{number} = {number}\n", encoding="utf-8"
        )
    subprocess.run(["git", "add", "."], cwd=fixture, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=benchmark@example.invalid",
            "-c",
            "user.name=CAR Benchmark",
            "commit",
            "-m",
            "large fixture",
        ],
        cwd=fixture,
        check=True,
        capture_output=True,
    )
    authorized = tuple(sorted(path.name for path in fixture.iterdir() if path.is_file()))
    case = _case(fixture, task="Fix target behavior").model_copy(
        update={"authorized_paths": authorized}
    )
    runtime = _ControlledRuntime()
    provider = _Provider("value = 2")
    executor = _executor(provider, runtime)
    spaces = BenchmarkWorkspaceSet(fixture)
    try:
        gemini = executor.execute_outcome(
            case, spaces.workspaces[BenchmarkStrategy.GEMINI_ONLY], BenchmarkStrategy.GEMINI_ONLY
        )
        codex = executor.execute_outcome(
            case, spaces.workspaces[BenchmarkStrategy.CODEX_ONLY], BenchmarkStrategy.CODEX_ONLY
        )
        auto = executor.execute_outcome(
            case, spaces.workspaces[BenchmarkStrategy.CAR], BenchmarkStrategy.CAR
        )
    finally:
        spaces.cleanup()

    assert gemini.telemetry.verified_success is True
    assert codex.telemetry.verified_success is True
    assert auto.telemetry.verified_success is True
    assert provider.calls == 2
    assert runtime.calls == 1
    assert runtime.scope_summaries == [
        "WRITE SCOPE\n"
        "CAR authorizes final task changes only to existing tracked regular files in this "
        "isolated repository. CAR retains the exact membership set and independently validates "
        "every proposed path. Do not modify tests or verification files unless they are existing "
        "tracked regular files needed for the task. Optional safe auxiliary paths remain subject "
        "to CAR's fixed policy; everything else is read-only."
    ]


def test_invalid_execution_context_is_an_invariant_failure(tmp_path: Path):
    fixture = _fixture(tmp_path)
    case = _case(fixture).model_copy(update={"authorized_paths": ("missing.py",)})
    results = BenchmarkRunner(_executor(_Provider("value = 2"), _ControlledRuntime())).run_case(
        case, fixture
    )

    assert all(result.failure_kind == BenchmarkFailureKind.INVARIANT_FAILED for result in results)
    assert all(result.attempt_count == 0 for result in results)
