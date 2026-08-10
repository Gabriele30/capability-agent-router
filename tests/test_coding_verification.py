"""Offline verification-gated finalization tests for coding patch transactions."""

from pathlib import Path

from car.coding.models import (
    CodingFileContext,
    CodingProposal,
    CodingTaskContext,
    FileChangeOperation,
    ProposedFileChange,
)
from car.coding.verification import (
    CodingVerificationCoordinator,
    CodingVerificationFailureKind,
)
from car.execution.models import CommandResult, CommandSpec
from car.patching.apply import SafePatchApplier
from car.patching.validation import PatchValidator
from car.providers.models import RepositoryClassificationContext
from car.router.models import Route
from car.verification.engine import VerificationEngine
from car.verification.models import VerificationPlan


def context(*paths: str) -> CodingTaskContext:
    return CodingTaskContext(
        task="Verify a safe patch",
        route=Route.GEMINI,
        repository=RepositoryClassificationContext(
            name="example", branch="main", dirty=True, languages={"Python": 1}, systems=["Python"]
        ),
        files=[CodingFileContext(path=path, content="selected\n") for path in paths],
    )


def modify(path: str, old: str, new: str) -> ProposedFileChange:
    return ProposedFileChange(
        path=path,
        operation=FileChangeOperation.MODIFY,
        patch=f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{old}\n+{new}\n",
    )


def create(path: str) -> ProposedFileChange:
    return ProposedFileChange(
        path=path,
        operation=FileChangeOperation.CREATE,
        patch=f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+created\n",
    )


def apply(root: Path, *changes: ProposedFileChange, selected: tuple[str, ...] = ("a.py",)):
    proposal = CodingProposal(summary="A verified change", changes=list(changes))
    result = PatchValidator().validate(proposal, context(*selected), root)
    assert result.patch_set is not None
    return SafePatchApplier().apply(root, result.patch_set)


class Runner:
    def __init__(self, outcomes: list[CommandResult]) -> None:
        self.outcomes = outcomes
        self.calls: list[CommandSpec] = []

    def run(self, command: CommandSpec) -> CommandResult:
        self.calls.append(command)
        outcome = self.outcomes.pop(0)
        return outcome.model_copy(update={"command": command})


def command(root: Path) -> CommandSpec:
    return CommandSpec(args=["ruff", "check", "a.py"], cwd=str(root), timeout_seconds=10)


def passed() -> CommandResult:
    return CommandResult(command=command(Path.cwd()), exit_code=0)


def failed() -> CommandResult:
    return CommandResult(command=command(Path.cwd()), exit_code=1, stderr="bounded failure")


def coordinator(outcomes: list[CommandResult]) -> tuple[CodingVerificationCoordinator, Runner]:
    runner = Runner(outcomes)
    return CodingVerificationCoordinator(VerificationEngine(runner)), runner


def test_modify_pass_finalizes_and_retains_changes(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_bytes(b"value = 1\n")
    transaction = apply(tmp_path, modify("a.py", "value = 1", "value = 2"))
    service, _ = coordinator([passed()])

    result = service.verify(tmp_path, transaction, VerificationPlan(commands=[command(tmp_path)]))

    assert result.passed and result.finalized and not result.rolled_back
    assert target.read_bytes() == b"value = 2\n"


def test_create_pass_finalizes_and_retains_file(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    transaction = apply(tmp_path, create("tests/new.py"), selected=())
    service, _ = coordinator([passed()])

    result = service.verify(tmp_path, transaction, VerificationPlan(commands=[command(tmp_path)]))

    assert result.passed and result.finalized
    assert (tmp_path / "tests" / "new.py").read_bytes() == b"created\n"


def test_failed_modify_rolls_back_dirty_user_state(tmp_path: Path):
    target = tmp_path / "a.py"
    dirty = b"user state\r\n"
    target.write_bytes(dirty)
    transaction = apply(tmp_path, modify("a.py", "user state", "car state"))
    service, _ = coordinator([failed()])

    result = service.verify(tmp_path, transaction, VerificationPlan(commands=[command(tmp_path)]))

    assert result.failure_kind == CodingVerificationFailureKind.CHECK_FAILED
    assert result.rolled_back and target.read_bytes() == dirty


def test_failed_create_and_multifile_verification_roll_back(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    first, second, unrelated = tmp_path / "a.py", tmp_path / "b.py", tmp_path / "notes.txt"
    first.write_bytes(b"one\n")
    second.write_bytes(b"two\n")
    unrelated.write_bytes(b"notes\n")
    transaction = apply(
        tmp_path,
        modify("a.py", "one", "ONE"),
        modify("b.py", "two", "TWO"),
        create("tests/new.py"),
        selected=("a.py", "b.py"),
    )
    service, _ = coordinator([failed()])

    result = service.verify(tmp_path, transaction, VerificationPlan(commands=[command(tmp_path)]))

    assert result.rolled_back
    assert first.read_bytes() == b"one\n" and second.read_bytes() == b"two\n"
    assert not (tmp_path / "tests" / "new.py").exists()
    assert unrelated.read_bytes() == b"notes\n"


def test_empty_plan_rolls_back_without_running_checks(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_bytes(b"one\n")
    transaction = apply(tmp_path, modify("a.py", "one", "two"))
    service, runner = coordinator([])

    result = service.verify(tmp_path, transaction, VerificationPlan())

    assert result.failure_kind == CodingVerificationFailureKind.EMPTY_PLAN and result.rolled_back
    assert not runner.calls and target.read_bytes() == b"one\n"


def test_second_failure_stops_remaining_checks(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_bytes(b"one\n")
    transaction = apply(tmp_path, modify("a.py", "one", "two"))
    service, runner = coordinator([passed(), failed(), passed()])
    plan = VerificationPlan(commands=[command(tmp_path), command(tmp_path), command(tmp_path)])

    result = service.verify(tmp_path, transaction, plan)

    assert result.rolled_back and len(runner.calls) == 2


def test_timeout_and_execution_error_roll_back(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_bytes(b"one\n")
    timeout_transaction = apply(tmp_path, modify("a.py", "one", "two"))
    timeout, _ = coordinator([CommandResult(command=command(tmp_path), timed_out=True)])
    timeout_result = timeout.verify(
        tmp_path, timeout_transaction, VerificationPlan(commands=[command(tmp_path)])
    )
    assert timeout_result.failure_kind == CodingVerificationFailureKind.CHECK_TIMEOUT
    assert target.read_bytes() == b"one\n"

    execution_transaction = apply(tmp_path, modify("a.py", "one", "two"))
    execution, _ = coordinator(
        [CommandResult(command=command(tmp_path), executable_not_found=True)]
    )
    execution_result = execution.verify(
        tmp_path, execution_transaction, VerificationPlan(commands=[command(tmp_path)])
    )
    assert execution_result.failure_kind == CodingVerificationFailureKind.CHECK_EXECUTION_ERROR


def test_invalid_finalized_and_rolled_back_transactions_do_not_run_checks(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_bytes(b"one\n")
    transaction = apply(tmp_path, modify("a.py", "one", "two"))
    service, runner = coordinator([passed()])
    plan = VerificationPlan(commands=[command(tmp_path)])
    assert service.verify(tmp_path, transaction, plan).finalized
    assert (
        service.verify(tmp_path, transaction, plan).failure_kind
        == CodingVerificationFailureKind.INVALID_TRANSACTION_STATE
    )
    assert len(runner.calls) == 1

    rolled_back = apply(tmp_path, modify("a.py", "one", "two"))
    assert rolled_back.rollback()
    assert (
        service.verify(tmp_path, rolled_back, plan).failure_kind
        == CodingVerificationFailureKind.INVALID_TRANSACTION_STATE
    )


def test_unsafe_model_like_command_is_rejected_without_execution(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_bytes(b"one\n")
    transaction = apply(tmp_path, modify("a.py", "one", "two"))
    service, runner = coordinator([])
    unsafe = CommandSpec(args=["python", "danger.py"], cwd=str(tmp_path), timeout_seconds=10)

    result = service.verify(tmp_path, transaction, VerificationPlan(commands=[unsafe]))

    assert result.failure_kind == CodingVerificationFailureKind.UNSAFE_COMMAND
    assert result.rolled_back and not runner.calls and target.read_bytes() == b"one\n"


def test_rollback_and_finalize_failures_are_reported_safely(tmp_path: Path, monkeypatch):
    target = tmp_path / "a.py"
    target.write_bytes(b"one\n")
    rollback_transaction = apply(tmp_path, modify("a.py", "one", "two"))
    monkeypatch.setattr(rollback_transaction, "rollback", lambda: False)
    failing, _ = coordinator([failed()])
    rollback_result = failing.verify(
        tmp_path, rollback_transaction, VerificationPlan(commands=[command(tmp_path)])
    )
    assert rollback_result.failure_kind == CodingVerificationFailureKind.CHECK_FAILED
    assert rollback_result.rollback_failure == CodingVerificationFailureKind.ROLLBACK_FAILED

    finalize_transaction = apply(tmp_path, modify("a.py", "two", "three"))
    monkeypatch.setattr(finalize_transaction, "finalize", lambda: (_ for _ in ()).throw(OSError()))
    passing, _ = coordinator([passed()])
    finalize_result = passing.verify(
        tmp_path, finalize_transaction, VerificationPlan(commands=[command(tmp_path)])
    )
    assert finalize_result.checks_passed and not finalize_result.passed
    assert finalize_result.failure_kind == CodingVerificationFailureKind.FINALIZE_FAILED
