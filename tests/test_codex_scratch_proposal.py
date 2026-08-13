"""Offline trust-boundary tests for controlled Codex scratch proposals."""

import json
import sys
from pathlib import Path

from car.codex_write.baseline import SourceBaselineService
from car.codex_write.delta import CodexWorkspaceDeltaDetector
from car.codex_write.models import (
    CodexSourceState,
    CodexSourceVerificationResult,
    CodexWriteAuthorization,
    CodexWriteFailureKind,
    CodexWritePolicy,
)
from car.codex_write.pipeline import ControlledCodexWritePipeline
from car.codex_write.projection import BaselineProjectionService
from car.codex_write.runtime_models import ControlledCodexWriteResult
from car.codex_write.workspace import IsolatedWorkspaceManager
from car.execution.models import CommandSpec
from car.patching.models import PatchViolationKind
from car.telemetry.models import TokenUsage, UsageSource
from car.verification.models import VerificationPlan


class _ScratchRuntime:
    def __init__(self, final_message: str | None, scratch_changes: dict[str, bytes]) -> None:
        self.final_message = final_message
        self.scratch_changes = scratch_changes
        self.calls = 0
        self.scratch_path: Path | None = None

    def execute(self, request, authorization):
        self.calls += 1
        self.scratch_path = request.workspace.workspace.path
        for path, content in self.scratch_changes.items():
            target = request.workspace.workspace.path / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return ControlledCodexWriteResult(
            attempted=True,
            process_succeeded=True,
            final_message=self.final_message,
            usage=TokenUsage(input_tokens=12, source=UsageSource.RUNTIME_REPORTED),
            baseline_digest=request.workspace.baseline_digest,
            baseline_head_oid=request.workspace.baseline_head_oid,
        )


class _FreshBaselineVerification:
    def __init__(self, expected_test_bytes: bytes | None = None) -> None:
        self.expected_test_bytes = expected_test_bytes
        self.calls = 0

    def verify_and_finalize(self, transaction, verification_plan, source_repository, policy):
        self.calls += 1
        if self.expected_test_bytes is not None:
            assert (
                source_repository / "tests/test_contract.py"
            ).read_bytes() == self.expected_test_bytes
        changed_paths = transaction.changed_paths
        transaction.finalize()
        return CodexSourceVerificationResult(
            attempted=True,
            verification_passed=True,
            post_verification_integrity_valid=True,
            finalized=True,
            accepted=True,
            source_state=CodexSourceState.UPDATED_AND_ACCEPTED,
            changed_paths=changed_paths,
            message="synthetic CAR verification passed",
        )


def _proposal(changes: list[dict[str, str]]) -> str:
    return json.dumps({"summary": "synthetic final proposal", "changes": changes})


def _modify(path: str, before: str, after: str) -> dict[str, str]:
    return {
        "path": path,
        "operation": "modify",
        "patch": f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{before}\n+{after}\n",
    }


def _create(path: str, content: str) -> dict[str, str]:
    return {
        "path": path,
        "operation": "create",
        "patch": f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+{content}\n",
    }


def _pipeline(
    runtime: _ScratchRuntime, verification: _FreshBaselineVerification
) -> ControlledCodexWritePipeline:
    manager = IsolatedWorkspaceManager()
    baseline = SourceBaselineService()
    return ControlledCodexWritePipeline(
        workspace_manager=manager,
        baseline_service=baseline,
        projection_service=BaselineProjectionService(
            baseline_service=baseline, workspace_manager=manager
        ),
        runtime=runtime,
        detector=CodexWorkspaceDeltaDetector(manager),
        verification_coordinator=verification,
    )


def _plan(root: Path) -> VerificationPlan:
    return VerificationPlan(
        commands=[CommandSpec(args=[sys.executable, "-c", ""], cwd=str(root), timeout_seconds=10)]
    )


def _execute(
    root: Path,
    runtime: _ScratchRuntime,
    paths: tuple[str, ...] = ("README.md",),
    verification: _FreshBaselineVerification | None = None,
):
    return _pipeline(runtime, verification or _FreshBaselineVerification()).execute(
        root,
        "Update the selected files",
        paths,
        _plan(root),
        CodexWritePolicy(enabled=True),
        CodexWriteAuthorization(authorized=True),
    )


def test_scratch_delta_is_discarded_and_only_final_proposal_reaches_source(git_repository: Path):
    original = (git_repository / "README.md").read_bytes()
    (git_repository / "tests").mkdir()
    expected_test = b"assert True\n"
    (git_repository / "tests/test_contract.py").write_bytes(expected_test)
    runtime = _ScratchRuntime(
        _proposal([_modify("README.md", "# Test", "# Trusted")]),
        {"README.md": b"# Untrusted scratch\n", "tests/scratch_only.py": b"raise AssertionError\n"},
    )

    verification = _FreshBaselineVerification(expected_test)
    result = _execute(git_repository, runtime, verification=verification)

    assert result.accepted
    assert result.task_changed_paths == ["README.md"]
    assert (git_repository / "README.md").read_text(encoding="utf-8") == "# Trusted\n"
    assert not (git_repository / "tests/scratch_only.py").exists()
    assert runtime.scratch_path is not None and not runtime.scratch_path.exists()
    assert original != (git_repository / "README.md").read_bytes()
    assert verification.calls == 1


def test_final_proposal_canonicalizes_hunk_counts_before_strict_application(git_repository: Path):
    runtime = _ScratchRuntime(
        _proposal(
            [
                {
                    "path": "README.md",
                    "operation": "modify",
                    "patch": (
                        "--- a/README.md\n+++ b/README.md\n@@ -1,2 +1,2 @@\n"
                        "-# Test\n+# Canonicalized\n"
                    ),
                }
            ]
        ),
        {},
    )

    result = _execute(git_repository, runtime)

    assert result.accepted
    assert (git_repository / "README.md").read_text(encoding="utf-8") == "# Canonicalized\n"


def test_missing_or_malformed_final_proposal_fails_closed_and_keeps_usage(git_repository: Path):
    for message in (None, "not a JSON proposal"):
        runtime = _ScratchRuntime(message, {"README.md": b"# Scratch only\n"})

        result = _execute(git_repository, runtime)

        assert result.failure_kind == CodexWriteFailureKind.CODEX_INVALID_OUTPUT
        assert result.codex_result.usage.input_tokens == 12
        assert (git_repository / "README.md").read_text(encoding="utf-8") == "# Test\n"
        assert result.source_state == CodexSourceState.UNCHANGED


def test_mixed_final_proposal_is_atomically_rejected(git_repository: Path):
    runtime = _ScratchRuntime(
        _proposal(
            [
                _modify("README.md", "# Test", "# Authorized"),
                _create("tests/test_unapproved.py", "assert False"),
            ]
        ),
        {},
    )

    result = _execute(git_repository, runtime)

    assert result.failure_kind == CodexWriteFailureKind.UNAUTHORIZED_CHANGE
    assert (git_repository / "README.md").read_text(encoding="utf-8") == "# Test\n"
    assert not (git_repository / "tests/test_unapproved.py").exists()


def test_proposal_validation_preserves_specific_patch_violation_kinds(git_repository: Path):
    cases = [
        (
            _proposal(
                [
                    {
                        "path": "README.md",
                        "operation": "modify",
                        "patch": "SENSITIVE PATCH CONTENT",
                    }
                ]
            ),
            PatchViolationKind.INVALID_DIFF,
        ),
        (
            _proposal(
                [
                    {
                        "path": "README.md",
                        "operation": "create",
                        "patch": (
                            "--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-# Test\n+# Changed\n"
                        ),
                    }
                ]
            ),
            PatchViolationKind.OPERATION_MISMATCH,
        ),
        (
            _proposal(
                [
                    {
                        "path": "README.md",
                        "operation": "modify",
                        "patch": "--- a/other.py\n+++ b/other.py\n@@ -1 +1 @@\n-old\n+new\n",
                    }
                ]
            ),
            PatchViolationKind.PATH_MISMATCH,
        ),
        (
            _proposal(
                [
                    {
                        "path": "README.md",
                        "operation": "create",
                        "patch": "--- /dev/null\n+++ b/README.md\n@@ -0,0 +1 @@\n+replacement\n",
                    }
                ]
            ),
            PatchViolationKind.TARGET_ALREADY_EXISTS,
        ),
    ]

    for proposal, kind in cases:
        result = _execute(git_repository, _ScratchRuntime(proposal, {}))
        assert result.failure_kind == CodexWriteFailureKind.UNAUTHORIZED_CHANGE
        assert result.delta_result.violations[0].kind == kind
        assert result.delta_result.violations[0].path == "README.md"
        assert (git_repository / "README.md").read_text(encoding="utf-8") == "# Test\n"


def test_safe_auxiliary_and_explicitly_authorized_test_remain_valid(git_repository: Path):
    (git_repository / "tests").mkdir()
    (git_repository / "tests/test_selected.py").write_text("assert True\n", encoding="utf-8")
    runtime = _ScratchRuntime(
        _proposal(
            [
                _create(".gitignore", ".scratch"),
                _modify("tests/test_selected.py", "assert True", "assert False"),
            ]
        ),
        {"README.md": b"# Scratch only\n"},
    )

    result = _execute(git_repository, runtime, ("tests/test_selected.py",))

    assert result.accepted
    assert result.task_changed_paths == ["tests/test_selected.py"]
    assert result.auxiliary_changed_paths == [".gitignore"]
    assert (git_repository / "README.md").read_text(encoding="utf-8") == "# Test\n"
