"""Offline contract tests for the opt-in SWE-bench live execution bridge."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from car.benchmark.context import (
    MAX_PROVIDER_CONTEXT_BYTES,
    MAX_PROVIDER_CONTEXT_FILES,
    build_execution_context,
)
from car.benchmark.models import BenchmarkCase, BenchmarkStrategy
from car.benchmark.swebench.evaluator import (
    SWEbenchEvaluationRequest,
    SWEbenchEvaluationStatus,
    parse_native_report,
    windows_to_wsl_path,
)
from car.benchmark.swebench.models import SWEbenchInstance
from car.benchmark.swebench.runtime import extract_candidate_patch, write_prediction
from car.coding.models import CodingProposal, FileChangeOperation, ProposedFileChange
from car.patching.validation import PatchValidator
from car.repository.git import run_git


def test_extract_candidate_patch_is_deterministic_for_accepted_tracked_change(
    tmp_path: Path,
) -> None:
    instance = _repository(tmp_path)
    target = tmp_path / "module.py"
    target.write_text("value = 2\n", encoding="utf-8")

    first = extract_candidate_patch(instance, tmp_path, ("module.py",))
    second = extract_candidate_patch(instance, tmp_path, ("module.py",))

    assert first == second
    assert "module.py" in first
    assert "value = 2" in first


def test_extract_candidate_patch_rejects_unaccepted_and_protected_changes(tmp_path: Path) -> None:
    instance = _repository(tmp_path)
    (tmp_path / "module.py").write_text("value = 2\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("scratch\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outside the accepted proposal"):
        extract_candidate_patch(instance, tmp_path, ("module.py",))


def test_extract_candidate_patch_rejects_candidate_baseline_mismatch(tmp_path: Path) -> None:
    instance = _repository(tmp_path)
    _git(tmp_path, "commit", "--allow-empty", "-m", "different baseline")

    with pytest.raises(ValueError, match="pinned base"):
        extract_candidate_patch(instance, tmp_path, ())


def test_empty_candidate_has_no_patch_and_prediction_rejects_empty(tmp_path: Path) -> None:
    instance = _repository(tmp_path)

    assert extract_candidate_patch(instance, tmp_path, ()) == ""
    with pytest.raises(ValueError, match="empty"):
        write_prediction(
            tmp_path / "prediction.jsonl", instance.instance_id, BenchmarkStrategy.CAR, ""
        )


def test_prediction_contains_only_official_prediction_fields(tmp_path: Path) -> None:
    path = tmp_path / "prediction.jsonl"
    write_prediction(path, "org__task-1", BenchmarkStrategy.GEMINI_ONLY, "diff --git a/x b/x\n")

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "instance_id": "org__task-1",
        "model_name_or_path": "car-gemini_only",
        "model_patch": "diff --git a/x b/x\n",
    }


def test_wsl_translation_and_native_report_parsing_are_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "prediction.jsonl"
    assert windows_to_wsl_path(
        path, command_runner=lambda _: (0, "/mnt/c/prediction.jsonl\n", "")
    ) == ("/mnt/c/prediction.jsonl")
    with pytest.raises(ValueError, match="not accessible"):
        windows_to_wsl_path(path, command_runner=lambda _: (1, "", "no"))

    request = SWEbenchEvaluationRequest(
        evaluator_directory=tmp_path,
        predictions_path=path,
        run_id="run",
        instance_ids=("org__task-1",),
    )
    report = tmp_path / "logs" / "org__task-1" / "report.json"
    report.parent.mkdir(parents=True)
    report.write_text(
        json.dumps({"instance_id": "org__task-1", "resolved": True}), encoding="utf-8"
    )
    assert parse_native_report(request, tmp_path).status == SWEbenchEvaluationStatus.RESOLVED
    report.write_text(json.dumps({"instance_id": "other", "resolved": True}), encoding="utf-8")
    assert (
        parse_native_report(request, tmp_path).status
        == SWEbenchEvaluationStatus.INFRASTRUCTURE_FAILURE
    )


def test_binary_and_non_utf8_files_are_authorized_but_not_provider_context(tmp_path: Path) -> None:
    instance = _repository(tmp_path)
    (tmp_path / "asset.bin").write_bytes(b"\x00RAW_BINARY_SENTINEL")
    (tmp_path / "legacy.dat").write_bytes(b"\xff\xfe")
    _git(tmp_path, "add", "asset.bin", "legacy.dat")
    _git(tmp_path, "commit", "-m", "binary files")
    case = BenchmarkCase(
        id=instance.instance_id,
        category="test",
        task="Read only public files",
        fixture="fixture",
        authorized_paths=("module.py", "asset.bin", "legacy.dat"),
        verification=("pytest",),
    )

    context = build_execution_context(case, tmp_path, BenchmarkStrategy.GEMINI_ONLY)

    assert case.authorized_paths == ("module.py", "asset.bin", "legacy.dat")
    assert [item.path for item in context.coding.files] == ["module.py"]
    assert "RAW_BINARY_SENTINEL" not in context.coding.model_dump_json()


def test_large_repository_authorization_is_exact_but_provider_context_is_bounded(
    tmp_path: Path,
) -> None:
    instance = _repository(tmp_path)
    for number in range(MAX_PROVIDER_CONTEXT_FILES + 15):
        (tmp_path / f"module_{number:02}.py").write_text(
            f"def helper_{number}():\n    return {number}\n", encoding="utf-8"
        )
    (tmp_path / "relevant_double.py").write_text(
        "def double(value):\n    return value\n", encoding="utf-8"
    )
    (tmp_path / "binary.bin").write_bytes(b"\0BINARY_CONTEXT_SENTINEL")
    (tmp_path / "legacy.dat").write_bytes(b"\xff\xfe")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "large repository")
    authorized = tuple(sorted(path.name for path in tmp_path.iterdir() if path.is_file()))
    case = BenchmarkCase(
        id=instance.instance_id,
        category="test",
        task="Make double return twice its input",
        fixture="fixture",
        authorized_paths=authorized,
        verification=("pytest",),
    )

    first = build_execution_context(case, tmp_path, BenchmarkStrategy.GEMINI_ONLY)
    second = build_execution_context(case, tmp_path, BenchmarkStrategy.CODEX_ONLY)

    assert first.coding.authorized_paths == authorized
    assert len(first.coding.files) <= MAX_PROVIDER_CONTEXT_FILES
    assert (
        sum(len(item.content.encode("utf-8")) for item in first.coding.files)
        <= MAX_PROVIDER_CONTEXT_BYTES
    )
    assert "binary.bin" not in [item.path for item in first.coding.files]
    assert "legacy.dat" not in [item.path for item in first.coding.files]
    assert first.coding.files[0].path == "relevant_double.py"
    assert first.coding.files == second.coding.files
    assert first.coding.authorized_paths == second.coding.authorized_paths
    assert first.coding.authorization_summary == second.coding.authorization_summary
    assert "BINARY_CONTEXT_SENTINEL" not in first.coding.model_dump_json()
    omitted = "module_34.py"
    assert omitted not in [item.path for item in first.coding.files]
    validation = PatchValidator().validate(
        CodingProposal(
            summary="authorized omitted-context change",
            changes=[
                ProposedFileChange(
                    path=omitted,
                    operation=FileChangeOperation.MODIFY,
                    patch=(
                        f"--- a/{omitted}\n+++ b/{omitted}\n@@ -1,2 +1,2 @@\n"
                        "-def helper_34():\n-    return 34\n"
                        "+def helper_34():\n+    return 35\n"
                    ),
                )
            ],
        ),
        first.coding,
        tmp_path,
    )
    assert validation.valid


def test_swebench_workspace_cleanup_retries_read_only_owned_file(tmp_path: Path) -> None:
    from car.benchmark.swebench.runtime import SWEbenchWorkspace

    workspace = SWEbenchWorkspace(_repository(tmp_path))
    packed = workspace.root / ".git" / "objects" / "pack" / "file.idx"
    packed.parent.mkdir(parents=True)
    packed.write_text("owned", encoding="utf-8")
    packed.chmod(0o444)

    workspace.cleanup()

    assert not workspace.root.exists()


def _repository(path: Path) -> SWEbenchInstance:
    _git(path, "init")
    _git(path, "config", "user.email", "tests@example.invalid")
    _git(path, "config", "user.name", "CAR tests")
    (path / "module.py").write_text("value = 1\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "baseline")
    head = _output(path, "rev-parse", "HEAD")
    return SWEbenchInstance(
        instance_id="org__task-1",
        repo="org/repo",
        base_commit=head,
        problem_statement="Fix the public task",
        difficulty="<15 min fix",
    )


def _git(path: Path, *args: str) -> None:
    result = run_git(path, *args)
    assert result is not None and result.returncode == 0


def _output(path: Path, *args: str) -> str:
    result = run_git(path, *args)
    assert result is not None and result.returncode == 0
    return result.stdout.strip()
