import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from car.coding.base import CodingProviderFailure
from car.coding.models import (
    CodingExecutionPolicy,
    CodingFileContext,
    CodingProposal,
    CodingTaskContext,
    FileChangeOperation,
    ProposedFileChange,
)
from car.coding.orchestration import attempt_coding
from car.providers.models import (
    ProviderCapabilities,
    ProviderError,
    ProviderErrorKind,
    ProviderHealth,
    ProviderStatus,
    RepositoryClassificationContext,
)
from car.router.models import Route


class FakeCodingProvider:
    def __init__(
        self,
        proposal: CodingProposal | None = None,
        status: ProviderStatus = ProviderStatus.CONFIGURED,
        error: CodingProviderFailure | None = None,
        model: str | None = None,
    ) -> None:
        self.name = "fake-coding"
        self.proposal = proposal or make_proposal()
        self.status = status
        self.error = error
        self.model = model
        self.call_count = 0
        self.last_context: CodingTaskContext | None = None

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(supports_code_changes=True)

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            status=self.status, configured=self.status == ProviderStatus.CONFIGURED
        )

    def propose(self, context: CodingTaskContext) -> CodingProposal:
        self.call_count += 1
        self.last_context = context
        if self.error:
            raise self.error
        return self.proposal


def make_context() -> CodingTaskContext:
    return CodingTaskContext(
        task="Update the greeting",
        route=Route.GEMINI,
        repository=RepositoryClassificationContext(
            name="example", branch="main", dirty=False, languages={"Python": 1}, systems=["Python"]
        ),
        files=[CodingFileContext(path="src/app.py", content="print('hello')\n")],
        constraints=["Keep changes focused."],
    )


def make_change(
    path: str = "src/app.py", operation: FileChangeOperation = FileChangeOperation.MODIFY
) -> ProposedFileChange:
    return ProposedFileChange(
        path=path,
        operation=operation,
        patch="@@ -1 +1 @@\n-print('hello')\n+print('hi')\n",
    )


def make_proposal(changes: list[ProposedFileChange] | None = None) -> CodingProposal:
    return CodingProposal(
        summary="Update greeting", changes=changes or [make_change()], reasons=["Requested update"]
    )


def test_valid_proposal_is_returned_once_without_workspace_mutation(git_repository: Path):
    target = git_repository / "sample.py"
    target.write_bytes(b"before\n")
    provider = FakeCodingProvider()

    result = attempt_coding(make_context(), provider)

    assert result.succeeded and result.proposal == provider.proposal
    assert provider.call_count == 1
    assert target.read_bytes() == b"before\n"


def test_configured_provider_model_is_preserved_in_attempt_result():
    provider = FakeCodingProvider(model="gemini-3.6-flash")

    result = attempt_coding(make_context(), provider)

    assert result.succeeded is True
    assert result.model == "gemini-3.6-flash"


@pytest.mark.parametrize(
    "status",
    [ProviderStatus.DISABLED, ProviderStatus.NOT_CONFIGURED, ProviderStatus.MISSING_CREDENTIALS],
)
def test_unavailable_provider_is_not_called(status: ProviderStatus):
    provider = FakeCodingProvider(status=status)

    result = attempt_coding(make_context(), provider)

    assert not result.attempted and not result.succeeded
    assert result.proposal is None and result.error_kind is None
    assert provider.call_count == 0


def test_provider_timeout_is_normalized():
    provider = FakeCodingProvider(
        error=CodingProviderFailure(
            ProviderError(kind=ProviderErrorKind.TIMEOUT, message="timed out")
        )
    )

    result = attempt_coding(make_context(), provider)

    assert result.attempted and not result.succeeded
    assert result.error_kind == ProviderErrorKind.TIMEOUT
    assert result.proposal is None and provider.call_count == 1


@pytest.mark.parametrize(
    "path", ["C:\\Users\\example.py", "/etc/passwd", "../file.py", "../../file.py"]
)
def test_absolute_and_escaping_paths_are_rejected(path: str):
    with pytest.raises(ValidationError):
        CodingFileContext(path=path, content="content")
    with pytest.raises(ValidationError):
        make_change(path=path)


def test_relative_path_is_accepted_and_normalized():
    context = CodingFileContext(path="src\\app.py", content="content")

    assert context.path == "src/app.py"


def test_duplicate_and_empty_proposals_are_rejected():
    with pytest.raises(ValidationError, match="only once"):
        make_proposal([make_change(), make_change()])
    with pytest.raises(ValidationError):
        CodingProposal(summary="No changes", changes=[])


def test_scope_limit_rejects_proposal_without_writing(git_repository: Path):
    original = (git_repository / "README.md").read_bytes()
    proposal = make_proposal(
        [
            make_change("src/one.py"),
            make_change("src/two.py"),
            make_change("src/three.py"),
        ]
    )

    result = attempt_coding(
        make_context(),
        FakeCodingProvider(proposal),
        CodingExecutionPolicy(max_files_per_proposal=2),
    )

    assert not result.succeeded and result.error_kind == ProviderErrorKind.INVALID_REQUEST
    assert (git_repository / "README.md").read_bytes() == original


@pytest.mark.parametrize(
    ("operation", "policy", "succeeds"),
    [
        (FileChangeOperation.CREATE, CodingExecutionPolicy(), True),
        (FileChangeOperation.CREATE, CodingExecutionPolicy(allow_create_files=False), False),
        (FileChangeOperation.MODIFY, CodingExecutionPolicy(), True),
        (FileChangeOperation.MODIFY, CodingExecutionPolicy(allow_modify_files=False), False),
    ],
)
def test_create_and_modify_policy(
    operation: FileChangeOperation, policy: CodingExecutionPolicy, succeeds: bool
):
    proposal = make_proposal([make_change("src/new.py", operation)])

    result = attempt_coding(make_context(), FakeCodingProvider(proposal), policy)

    assert result.succeeded is succeeds
    assert result.error_kind == (None if succeeds else ProviderErrorKind.INVALID_REQUEST)


def test_orchestrator_never_runs_commands(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("coding orchestration must not run subprocess commands")

    monkeypatch.setattr(subprocess, "run", fail_if_called)

    result = attempt_coding(make_context(), FakeCodingProvider())

    assert result.succeeded
