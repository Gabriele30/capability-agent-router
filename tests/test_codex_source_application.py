"""Offline real-Git tests for pending-verification Codex source transactions."""

import json
import subprocess
from pathlib import Path

import car.codex_write.application as application
from car.codex_write.application import CodexSourceApplicationService
from car.codex_write.baseline import SourceBaselineService
from car.codex_write.delta import CodexWorkspaceDeltaDetector, CodexWorkspaceDeltaValidator
from car.codex_write.models import (
    CodexSourceTransactionState,
    CodexWriteAuthorization,
    CodexWriteFailureKind,
    CodexWritePolicy,
)
from car.codex_write.pipeline import ControlledCodexWritePipeline
from car.codex_write.projection import BaselineProjectionService
from car.codex_write.runtime_models import ControlledCodexWriteResult
from car.codex_write.verification import CodexSourceVerificationCoordinator
from car.codex_write.workspace import IsolatedWorkspaceManager
from car.execution.models import CommandResult, CommandSpec
from car.verification.models import VerificationPlan, VerificationResult, VerificationStatus


def _git(root: Path, *args: str, check: bool = True):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


class _VerificationEngine:
    def __init__(self, result: VerificationResult, hook=None) -> None:
        self.result = result
        self.hook = hook
        self.calls = 0

    def verify(
        self, plan: VerificationPlan, *, stop_on_failure: bool = False
    ) -> VerificationResult:
        self.calls += 1
        if self.hook is not None:
            self.hook()
        return self.result


class _SyntheticRuntime:
    def __init__(self, changes: dict[str, bytes]) -> None:
        self.changes = changes
        self.calls = 0

    def execute(self, request, authorization):
        self.calls += 1
        changes = []
        for path, content in self.changes.items():
            target = request.workspace.workspace.path / path
            before = target.read_text(encoding="utf-8") if target.exists() else None
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            after = content.decode("utf-8")
            if before is None:
                patch = f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+{after.rstrip()}\n"
                operation = "create"
            else:
                patch = (
                    f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n"
                    f"-{before.rstrip()}\n+{after.rstrip()}\n"
                )
                operation = "modify"
            changes.append({"path": path, "operation": operation, "patch": patch})
        return ControlledCodexWriteResult(
            attempted=True,
            process_succeeded=True,
            final_message=(
                json.dumps({"summary": "synthetic proposal", "changes": changes})
                if changes
                else None
            ),
            baseline_digest=request.workspace.baseline_digest,
            baseline_head_oid=request.workspace.baseline_head_oid,
        )


def _command(root: Path) -> CommandSpec:
    return CommandSpec(args=["python", "-m", "pytest"], cwd=str(root), timeout_seconds=10)


def _verification(root: Path, status: VerificationStatus, *, timeout: bool = False):
    return VerificationResult(
        status=status,
        checks=[
            CommandResult(
                command=_command(root),
                exit_code=0 if status == VerificationStatus.PASSED else 1,
                timed_out=timeout,
            )
        ],
        message="offline test",
    )


def _prepared(source: Path, path: str, content: bytes, policy: CodexWritePolicy | None = None):
    policy = policy or CodexWritePolicy(enabled=True)
    baseline = SourceBaselineService().capture(source, policy).baseline
    assert baseline is not None
    manager = IsolatedWorkspaceManager()
    projection = BaselineProjectionService(workspace_manager=manager)
    projected_result = projection.project(source, baseline, policy)
    assert projected_result.projected_workspace is not None
    projected = projected_result.projected_workspace
    target = projected.workspace.path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    detector = CodexWorkspaceDeltaDetector(manager)
    detected = detector.detect(projected, baseline, policy)
    validated = CodexWorkspaceDeltaValidator().validate(
        detected,
        baseline,
        policy,
        CodexWriteAuthorization(authorized=True),
        (path,),
        source,
    )
    assert validated.valid and validated.validated_change_set is not None
    return (
        baseline,
        projected,
        manager,
        projection,
        policy,
        validated.validated_change_set,
        detector,
    )


def _pipeline(root: Path, runtime, verification: VerificationStatus):
    manager = IsolatedWorkspaceManager()
    baseline = SourceBaselineService()
    projection = BaselineProjectionService(baseline_service=baseline, workspace_manager=manager)
    detector = CodexWorkspaceDeltaDetector(manager)
    verifier = CodexSourceVerificationCoordinator(
        _VerificationEngine(_verification(root, verification))
    )
    return ControlledCodexWritePipeline(
        workspace_manager=manager,
        baseline_service=baseline,
        projection_service=projection,
        runtime=runtime,
        detector=detector,
        application_service=CodexSourceApplicationService(detector, baseline_service=baseline),
        verification_coordinator=verifier,
    )


def _prepared_many(source: Path, changes: dict[str, bytes], policy: CodexWritePolicy | None = None):
    policy = policy or CodexWritePolicy(enabled=True)
    baseline = SourceBaselineService().capture(source, policy).baseline
    assert baseline is not None
    manager = IsolatedWorkspaceManager()
    projection = BaselineProjectionService(workspace_manager=manager)
    projected_result = projection.project(source, baseline, policy)
    assert projected_result.projected_workspace is not None
    projected = projected_result.projected_workspace
    for path, content in changes.items():
        target = projected.workspace.path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    detector = CodexWorkspaceDeltaDetector(manager)
    detected = detector.detect(projected, baseline, policy)
    validated = CodexWorkspaceDeltaValidator().validate(
        detected,
        baseline,
        policy,
        CodexWriteAuthorization(authorized=True),
        tuple(changes),
        source,
    )
    assert validated.valid and validated.validated_change_set is not None
    return (
        baseline,
        projected,
        manager,
        projection,
        policy,
        validated.validated_change_set,
        detector,
    )


def test_modify_copies_exact_bytes_and_remains_pending_verification(git_repository: Path):
    source_before = (git_repository / "README.md").read_bytes()
    content = b"# CRLF\r\nnon-utf8: \xff\r\n"
    baseline, projected, manager, projection, policy, validated, detector = _prepared(
        git_repository, "README.md", content
    )
    try:
        result, transaction = CodexSourceApplicationService(detector).apply(
            git_repository, projected, validated, baseline, policy
        )
        assert result.applied and not result.changes_accepted
        assert (git_repository / "README.md").read_bytes() == content
        assert transaction is not None
        assert transaction.state == CodexSourceTransactionState.APPLIED_PENDING_VERIFICATION
        assert transaction.rollback()
        assert (git_repository / "README.md").read_bytes() == source_before
        assert transaction.state == CodexSourceTransactionState.ROLLED_BACK
    finally:
        assert projection.cleanup(projected).removed


def test_create_is_untracked_and_rollback_removes_only_transaction_file(git_repository: Path):
    baseline, projected, manager, projection, policy, validated, detector = _prepared(
        git_repository, "created.py", b"value = 1\n"
    )
    try:
        result, transaction = CodexSourceApplicationService(detector).apply(
            git_repository, projected, validated, baseline, policy
        )
        assert result.applied and result.created_paths == ["created.py"]
        assert "?? created.py" in _git(git_repository, "status", "--porcelain").stdout
        assert transaction is not None and transaction.rollback()
        assert not (git_repository / "created.py").exists()
    finally:
        assert projection.cleanup(projected).removed


def test_staged_and_unstaged_source_index_is_preserved(git_repository: Path):
    path = git_repository / "README.md"
    path.write_bytes(b"index B\n")
    _git(git_repository, "add", "README.md")
    path.write_bytes(b"working C\n")
    index_before = _git(git_repository, "diff", "--cached", "--binary").stdout
    head_before = _git(git_repository, "rev-parse", "HEAD").stdout
    branch_before = _git(git_repository, "branch", "--show-current").stdout
    baseline, projected, manager, projection, policy, validated, detector = _prepared(
        git_repository, "README.md", b"Codex D\n"
    )
    try:
        result, transaction = CodexSourceApplicationService(detector).apply(
            git_repository, projected, validated, baseline, policy
        )
        assert result.applied and transaction is not None
        assert path.read_bytes() == b"Codex D\n"
        assert _git(git_repository, "diff", "--cached", "--binary").stdout == index_before
        assert _git(git_repository, "rev-parse", "HEAD").stdout == head_before
        assert _git(git_repository, "branch", "--show-current").stdout == branch_before
        assert transaction.rollback()
        assert path.read_bytes() == b"working C\n"
        assert _git(git_repository, "diff", "--cached", "--binary").stdout == index_before
        assert _git(git_repository, "rev-parse", "HEAD").stdout == head_before
        assert _git(git_repository, "branch", "--show-current").stdout == branch_before
    finally:
        assert projection.cleanup(projected).removed


def test_multi_file_failure_rolls_back_all_prior_writes(git_repository: Path, monkeypatch):
    original = (git_repository / "README.md").read_bytes()
    baseline, projected, manager, projection, policy, validated, detector = _prepared_many(
        git_repository,
        {"README.md": b"Codex D\n", "created.py": b"value = 1\n"},
    )
    try:
        monkeypatch.setattr(
            application,
            "_exclusive_create",
            lambda target, content: (_ for _ in ()).throw(OSError("simulated create failure")),
        )
        result, transaction = CodexSourceApplicationService(detector).apply(
            git_repository, projected, validated, baseline, policy
        )
        assert not result.applied
        assert result.failure_kind == CodexWriteFailureKind.SOURCE_APPLICATION_FAILED
        assert result.rollback_attempted and result.rollback_succeeded
        assert (git_repository / "README.md").read_bytes() == original
        assert not (git_repository / "created.py").exists()
        assert transaction is not None
        assert transaction.state == CodexSourceTransactionState.ROLLED_BACK
    finally:
        assert projection.cleanup(projected).removed


def test_rollback_preserves_post_application_user_edit(git_repository: Path):
    baseline, projected, manager, projection, policy, validated, detector = _prepared(
        git_repository, "README.md", b"Codex D\n"
    )
    try:
        result, transaction = CodexSourceApplicationService(detector).apply(
            git_repository, projected, validated, baseline, policy
        )
        assert result.applied and transaction is not None
        (git_repository / "README.md").write_bytes(b"user edit after application\n")
        assert not transaction.rollback()
        assert transaction.state == CodexSourceTransactionState.FAILED
        assert (git_repository / "README.md").read_bytes() == b"user edit after application\n"
    finally:
        assert projection.cleanup(projected).removed


def test_source_or_isolated_change_after_validation_blocks_zero_source_writes(git_repository: Path):
    original = (git_repository / "README.md").read_bytes()
    baseline, projected, manager, projection, policy, validated, detector = _prepared(
        git_repository, "README.md", b"Codex D\n"
    )
    try:
        (git_repository / "README.md").write_bytes(b"user concurrent\n")
        result, transaction = CodexSourceApplicationService(detector).apply(
            git_repository, projected, validated, baseline, policy
        )
        assert result.failure_kind == CodexWriteFailureKind.CONCURRENT_MODIFICATION
        assert (
            transaction is None
            and (git_repository / "README.md").read_bytes() == b"user concurrent\n"
        )
    finally:
        assert projection.cleanup(projected).removed
    (git_repository / "README.md").write_bytes(original)

    baseline, projected, manager, projection, policy, validated, detector = _prepared(
        git_repository, "README.md", b"Codex D\n"
    )
    try:
        (projected.workspace.path / "README.md").write_bytes(b"changed after validation\n")
        result, transaction = CodexSourceApplicationService(detector).apply(
            git_repository, projected, validated, baseline, policy
        )
        assert result.failure_kind == CodexWriteFailureKind.WORKSPACE_CHANGED_AFTER_VALIDATION
        assert transaction is None and (git_repository / "README.md").read_bytes() == original
    finally:
        assert projection.cleanup(projected).removed


def test_create_parent_missing_and_existing_target_fail_closed(git_repository: Path):
    baseline, projected, manager, projection, policy, validated, detector = _prepared(
        git_repository, "nested/created.py", b"value = 1\n"
    )
    try:
        result, transaction = CodexSourceApplicationService(detector).apply(
            git_repository, projected, validated, baseline, policy
        )
        assert result.failure_kind == CodexWriteFailureKind.CREATE_PARENT_NOT_FOUND
        assert transaction is None
    finally:
        assert projection.cleanup(projected).removed


def test_b2_pass_finalizes_only_after_integrity_validation(git_repository: Path):
    baseline, projected, manager, projection, policy, validated, detector = _prepared(
        git_repository, "README.md", b"Codex D\n"
    )
    try:
        applied, transaction = CodexSourceApplicationService(detector).apply(
            git_repository, projected, validated, baseline, policy
        )
        assert applied.applied and transaction is not None
        engine = _VerificationEngine(_verification(git_repository, VerificationStatus.PASSED))
        result = CodexSourceVerificationCoordinator(engine).verify_and_finalize(
            transaction,
            VerificationPlan(commands=[_command(git_repository)]),
            git_repository,
            policy,
        )
        assert result.accepted and result.finalized and result.verification_passed
        assert result.changed_paths == ["README.md"]
        assert transaction.state == CodexSourceTransactionState.FINALIZED
    finally:
        assert projection.cleanup(projected).removed


def test_b2_real_pytest_verification_has_no_cache_artifacts(git_repository: Path):
    (git_repository / "test_smoke.py").write_text("def test_smoke():\n    assert True\n")
    baseline, projected, manager, projection, policy, validated, detector = _prepared(
        git_repository, "README.md", b"Codex D\n"
    )
    try:
        _, transaction = CodexSourceApplicationService(detector).apply(
            git_repository, projected, validated, baseline, policy
        )
        assert transaction is not None
        result = CodexSourceVerificationCoordinator().verify_and_finalize(
            transaction,
            VerificationPlan(commands=[_command(git_repository)]),
            git_repository,
            policy,
        )
        assert result.accepted
        assert not (git_repository / ".pytest_cache").exists()
        assert not list(git_repository.rglob("__pycache__"))
    finally:
        assert projection.cleanup(projected).removed


def test_b2_failure_timeout_and_empty_plan_rollback(git_repository: Path):
    original = (git_repository / "README.md").read_bytes()
    for verification, expected in (
        (
            _verification(git_repository, VerificationStatus.FAILED),
            CodexWriteFailureKind.VERIFICATION_FAILED,
        ),
        (
            _verification(git_repository, VerificationStatus.FAILED, timeout=True),
            CodexWriteFailureKind.VERIFICATION_TIMEOUT,
        ),
    ):
        baseline, projected, manager, projection, policy, validated, detector = _prepared(
            git_repository, "README.md", b"Codex D\n"
        )
        try:
            _, transaction = CodexSourceApplicationService(detector).apply(
                git_repository, projected, validated, baseline, policy
            )
            assert transaction is not None
            result = CodexSourceVerificationCoordinator(
                _VerificationEngine(verification)
            ).verify_and_finalize(
                transaction,
                VerificationPlan(commands=[_command(git_repository)]),
                git_repository,
                policy,
            )
            assert result.failure_kind == expected and result.rollback_succeeded
            assert (git_repository / "README.md").read_bytes() == original
        finally:
            assert projection.cleanup(projected).removed

    baseline, projected, manager, projection, policy, validated, detector = _prepared(
        git_repository, "README.md", b"Codex D\n"
    )
    try:
        _, transaction = CodexSourceApplicationService(detector).apply(
            git_repository, projected, validated, baseline, policy
        )
        assert transaction is not None
        result = CodexSourceVerificationCoordinator(
            _VerificationEngine(_verification(git_repository, VerificationStatus.PASSED))
        ).verify_and_finalize(transaction, VerificationPlan(), git_repository, policy)
        assert result.failure_kind == CodexWriteFailureKind.VERIFICATION_REQUIRED
        assert result.rollback_succeeded
        assert (git_repository / "README.md").read_bytes() == original
    finally:
        assert projection.cleanup(projected).removed


def test_b2_post_verification_mutation_is_not_accepted(git_repository: Path):
    baseline, projected, manager, projection, policy, validated, detector = _prepared(
        git_repository, "README.md", b"Codex D\n"
    )
    try:
        _, transaction = CodexSourceApplicationService(detector).apply(
            git_repository, projected, validated, baseline, policy
        )
        assert transaction is not None
        engine = _VerificationEngine(
            _verification(git_repository, VerificationStatus.PASSED),
            hook=lambda: (git_repository / "README.md").write_bytes(b"user edit\n"),
        )
        result = CodexSourceVerificationCoordinator(engine).verify_and_finalize(
            transaction,
            VerificationPlan(commands=[_command(git_repository)]),
            git_repository,
            policy,
        )
        assert not result.accepted and result.rollback_attempted and not result.rollback_succeeded
        assert (git_repository / "README.md").read_bytes() == b"user edit\n"
    finally:
        assert projection.cleanup(projected).removed


def test_pipeline_composes_real_workspace_apply_and_finalization(git_repository: Path):
    runtime = _SyntheticRuntime({"README.md": b"Codex D\n"})
    pipeline = _pipeline(git_repository, runtime, VerificationStatus.PASSED)
    policy = CodexWritePolicy(enabled=True)

    result = pipeline.execute(
        git_repository,
        "update readme",
        ("README.md",),
        VerificationPlan(commands=[_command(git_repository)]),
        policy,
        CodexWriteAuthorization(authorized=True),
    )

    assert result.accepted and result.source_state.value == "updated_and_accepted"
    assert result.workspace_cleanup_succeeded and runtime.calls == 1
    assert (git_repository / "README.md").read_text(encoding="utf-8") == "Codex D\n"


def test_pipeline_failure_paths_do_not_accept_or_leave_workspace(git_repository: Path):
    original = (git_repository / "README.md").read_bytes()
    runtime = _SyntheticRuntime({"README.md": b"Codex D\n"})
    pipeline = _pipeline(git_repository, runtime, VerificationStatus.FAILED)
    policy = CodexWritePolicy(enabled=True)
    result = pipeline.execute(
        git_repository,
        "update readme",
        ("README.md",),
        VerificationPlan(commands=[_command(git_repository)]),
        policy,
        CodexWriteAuthorization(authorized=True),
    )
    assert not result.accepted and result.source_state.value == "restored"
    assert (
        result.workspace_cleanup_succeeded
        and (git_repository / "README.md").read_bytes() == original
    )

    no_change_runtime = _SyntheticRuntime({})
    no_change = _pipeline(git_repository, no_change_runtime, VerificationStatus.PASSED).execute(
        git_repository,
        "no change",
        ("README.md",),
        VerificationPlan(commands=[_command(git_repository)]),
        policy,
        CodexWriteAuthorization(authorized=True),
    )
    assert no_change.failure_kind == CodexWriteFailureKind.CODEX_INVALID_OUTPUT
    assert no_change.workspace_cleanup_succeeded


def test_pipeline_disabled_or_unauthorized_has_zero_runtime_calls(git_repository: Path):
    runtime = _SyntheticRuntime({"README.md": b"Codex D\n"})
    pipeline = _pipeline(git_repository, runtime, VerificationStatus.PASSED)
    plan = VerificationPlan(commands=[_command(git_repository)])
    disabled = pipeline.execute(
        git_repository,
        "update",
        ("README.md",),
        plan,
        CodexWritePolicy(),
        CodexWriteAuthorization(authorized=True),
    )
    unauthorized = pipeline.execute(
        git_repository,
        "update",
        ("README.md",),
        plan,
        CodexWritePolicy(enabled=True),
        CodexWriteAuthorization(),
    )
    assert disabled.failure_kind == CodexWriteFailureKind.DISABLED
    assert unauthorized.failure_kind == CodexWriteFailureKind.NOT_AUTHORIZED
    assert runtime.calls == 0
