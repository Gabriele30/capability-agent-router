"""Offline controlled-write Codex runtime confined to CAR-owned B2 workspaces."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from car.authorization import render_agent_write_scope
from car.coding.models import CodingProposal
from car.escalation.handoff import render_codex_handoff_markdown
from car.telemetry.models import TokenUsage, UsageSource

from .models import CodexWriteAuthorization, CodexWriteFailureKind, CodexWritePolicy
from .projection import ProjectedIsolatedWorkspace
from .runtime_models import (
    ControlledCodexHealthStatus,
    ControlledCodexProcessResult,
    ControlledCodexWriteHealth,
    ControlledCodexWriteRequest,
    ControlledCodexWriteResult,
)
from .workspace import IsolatedWorkspaceManager

CONTROLLED_WRITE_INSTRUCTION = (
    "Work only in the current CAR-provided isolated scratch workspace. You may inspect and "
    "experiment there, but CAR will discard its filesystem state. Do not access or modify the "
    "source repository. Your FINAL agent message must be JSON matching CAR's CodingProposal "
    "schema. Only that final proposal will be considered: it must contain only permitted paths, "
    "and CAR will apply it to a pristine baseline and verify it independently. "
    "Do not stage files, commit, create branches, modify Git metadata, delete or rename "
    "files, enable network access, or install dependencies. Do not add prose before or after "
    "the final JSON proposal. For each final ProposedFileChange, use operation 'modify' only "
    "for an existing file. Use operation 'create' only for a new file. Do not invent other "
    "operations. "
    "Each patch must be exactly one CAR-supported unified diff. A modify patch must start with "
    "'--- <path>' and '+++ <path>', contain at least one valid '@@' hunk with coherent counts, "
    "and use header paths that identify the same repository-relative file as proposal.path. "
    "Headers may use matching 'a/' and 'b/' prefixes. Do not use /dev/null for modify, and do "
    "not include rename, delete, binary, mode-only, or multi-file diffs. Scratch edits are "
    "irrelevant: do not derive the final proposal automatically from a scratch filesystem diff."
)
TRUNCATION_MARKER = "\n[truncated by CAR]"
_ENVIRONMENT_KEYS = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "CODEX_HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
)


class ControlledCodexProcessRunner(Protocol):
    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        stdin: str,
        environment: dict[str, str],
        timeout_seconds: float,
    ) -> ControlledCodexProcessResult: ...


class SubprocessControlledCodexRunner:
    """Injectable structured subprocess boundary; it never invokes a shell."""

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        stdin: str,
        environment: dict[str, str],
        timeout_seconds: float,
    ) -> ControlledCodexProcessResult:
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                input=stdin,
                env=environment,
                capture_output=True,
                check=False,
                shell=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
            return ControlledCodexProcessResult(
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        except FileNotFoundError:
            return ControlledCodexProcessResult(executable_not_found=True)
        except subprocess.TimeoutExpired as error:
            return ControlledCodexProcessResult(
                stdout=_text(error.stdout), stderr=_text(error.stderr), timed_out=True
            )
        except OSError:
            return ControlledCodexProcessResult(stderr="controlled Codex process could not start")


class ControlledCodexWriteRuntime:
    """Run Codex in CAR-owned disposable scratch; only final JSON is considered later."""

    def __init__(
        self,
        *,
        workspace_manager: IsolatedWorkspaceManager,
        runner: ControlledCodexProcessRunner | None = None,
        which: Callable[[str], str | None] | None = None,
        policy: CodexWritePolicy | None = None,
        is_windows: bool | None = None,
    ) -> None:
        self._workspace_manager = workspace_manager
        self._runner = runner or SubprocessControlledCodexRunner()
        self._which = which or shutil.which
        self._policy = policy or CodexWritePolicy()
        self._is_windows = os.name == "nt" if is_windows is None else is_windows

    def health(self) -> ControlledCodexWriteHealth:
        """Check only the resolved local CLI login, and only when explicitly enabled."""
        if not self._policy.enabled:
            return ControlledCodexWriteHealth(status=ControlledCodexHealthStatus.DISABLED)
        return self._health(Path.cwd())

    def execute(
        self,
        request: ControlledCodexWriteRequest,
        authorization: CodexWriteAuthorization,
    ) -> ControlledCodexWriteResult:
        if not self._policy.enabled:
            return _result(CodexWriteFailureKind.DISABLED)
        if not authorization.authorized:
            return _result(CodexWriteFailureKind.NOT_AUTHORIZED)
        workspace = request.workspace
        if not self._valid_workspace(workspace):
            return _result(CodexWriteFailureKind.INVALID_WORKSPACE)
        health = self._health(workspace.workspace.path)
        if health.status != ControlledCodexHealthStatus.READY:
            return _result(_health_failure(health.status), workspace)
        if health.executable is None:
            return _result(CodexWriteFailureKind.CODEX_NOT_READY, workspace)
        try:
            with _proposal_output_files(workspace.workspace.parent) as output:
                process = self._runner.run(
                    _execution_argv(
                        health.executable,
                        workspace_path=workspace.workspace.path,
                        schema_path=output.schema_path,
                        proposal_path=output.proposal_path,
                        is_windows=self._is_windows,
                        model=request.model,
                        reasoning_effort=request.reasoning_effort,
                    ),
                    cwd=workspace.workspace.path,
                    stdin=_stdin(request),
                    environment=controlled_child_environment(),
                    timeout_seconds=min(
                        request.timeout_seconds or self._policy.codex_write_timeout_seconds,
                        self._policy.codex_write_timeout_seconds,
                    ),
                )
                return self._process_result(process, workspace, request, output.proposal_path)
        except OSError:
            return _result(CodexWriteFailureKind.CODEX_PROCESS_ERROR, workspace)

    def _process_result(
        self,
        process: ControlledCodexProcessResult,
        workspace: ProjectedIsolatedWorkspace,
        request: ControlledCodexWriteRequest,
        proposal_path: Path,
    ) -> ControlledCodexWriteResult:
        stdout = _truncate(process.stdout, self._policy.codex_max_stdout_chars)
        stderr = _truncate(process.stderr, self._policy.codex_max_stderr_chars)
        parsed = _parse_jsonl_output(process.stdout)
        usage = parsed[1] if parsed is not None else None
        if process.executable_not_found:
            return _result(
                CodexWriteFailureKind.CODEX_CLI_NOT_FOUND,
                workspace,
                stdout,
                stderr,
                True,
                usage=usage,
            )
        if process.timed_out:
            return _result(
                CodexWriteFailureKind.CODEX_TIMEOUT,
                workspace,
                stdout,
                stderr,
                True,
                timed_out=True,
                usage=usage,
            )
        if process.exit_code is None:
            return _result(
                CodexWriteFailureKind.CODEX_PROCESS_ERROR,
                workspace,
                stdout,
                stderr,
                True,
                usage=usage,
            )
        if process.exit_code != 0:
            return _result(
                CodexWriteFailureKind.CODEX_NONZERO_EXIT,
                workspace,
                stdout,
                stderr,
                True,
                exit_code=process.exit_code,
                usage=usage,
            )
        if parsed is None:
            return _result(
                CodexWriteFailureKind.CODEX_INVALID_OUTPUT,
                workspace,
                stdout,
                stderr,
                True,
                exit_code=0,
            )
        final_message = _read_coding_proposal(proposal_path)
        if final_message is None:
            return _result(
                CodexWriteFailureKind.CODEX_INVALID_OUTPUT,
                workspace,
                stdout,
                stderr,
                True,
                exit_code=0,
                usage=usage,
            )
        return ControlledCodexWriteResult(
            attempted=True,
            process_succeeded=True,
            final_message=final_message,
            exit_code=0,
            stdout=stdout,
            stderr=stderr,
            usage=usage,
            model=request.model,
            baseline_digest=workspace.baseline_digest,
            baseline_head_oid=workspace.baseline_head_oid,
        )

    def _health(self, cwd: Path) -> ControlledCodexWriteHealth:
        executable = self._which("codex")
        if executable is None:
            return ControlledCodexWriteHealth(status=ControlledCodexHealthStatus.CLI_NOT_FOUND)
        process = self._runner.run(
            [executable, "login", "status"],
            cwd=cwd,
            stdin="",
            environment=controlled_child_environment(),
            timeout_seconds=self._policy.codex_login_timeout_seconds,
        )
        if process.executable_not_found:
            return ControlledCodexWriteHealth(status=ControlledCodexHealthStatus.CLI_NOT_FOUND)
        if process.timed_out:
            return ControlledCodexWriteHealth(
                status=ControlledCodexHealthStatus.UNKNOWN,
                executable=executable,
                detail="local login status check timed out",
            )
        if process.exit_code == 0:
            return ControlledCodexWriteHealth(
                status=ControlledCodexHealthStatus.READY, executable=executable
            )
        return ControlledCodexWriteHealth(
            status=ControlledCodexHealthStatus.NOT_AUTHENTICATED,
            executable=executable,
            detail="local Codex login status is not ready",
        )

    def _valid_workspace(self, projected: ProjectedIsolatedWorkspace) -> bool:
        workspace = projected.workspace
        if not projected.baseline_digest or not projected.baseline_head_oid:
            return False
        if workspace.revision != projected.baseline_head_oid:
            return False
        if workspace.path == workspace.source_root or not workspace.path.is_dir():
            return False
        return self._workspace_manager.owns(workspace)


def _execution_argv(
    executable: str,
    *,
    workspace_path: Path,
    schema_path: Path,
    proposal_path: Path,
    is_windows: bool,
    model: str | None = None,
    reasoning_effort=None,
) -> list[str]:
    """Build the fixed command from the CAR-owned projected workspace only."""
    argv = [executable]
    if is_windows:
        argv.extend(["-c", 'windows.sandbox="unelevated"'])
    if reasoning_effort is not None:
        argv.extend(["-c", f'model_reasoning_effort="{reasoning_effort.value}"'])
    if model:
        argv.extend(["-m", model])
    argv.extend(
        [
            "--ask-for-approval",
            "never",
            "exec",
            "--ephemeral",
            "--sandbox",
            "workspace-write",
            "--ignore-user-config",
            "--json",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(proposal_path),
            "--cd",
            str(workspace_path),
            CONTROLLED_WRITE_INSTRUCTION,
        ]
    )
    return argv


def controlled_child_environment(parent: dict[str, str] | None = None) -> dict[str, str]:
    """Positive allowlist for the child; credentials and arbitrary parent values are omitted."""
    source = os.environ if parent is None else parent
    by_casefold = {key.casefold(): value for key, value in source.items()}
    environment = {
        key: value
        for key in _ENVIRONMENT_KEYS
        if (value := by_casefold.get(key.casefold())) is not None
    }
    # CAR-owned hygiene: agent-invoked Python must not contaminate the reviewed delta.
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def _stdin(request: ControlledCodexWriteRequest) -> str:
    sections = [
        "# CAR Controlled Codex Write Request",
        "\nTask:\n" + request.task,
        "\n"
        + render_agent_write_scope(
            request.authorized_paths,
            safe_auxiliary_paths=request.safe_auxiliary_paths,
        ),
        "\nSCRATCH WORKSPACE\n"
        + (
            "This workspace is untrusted scratch and will be discarded. "
            "You may inspect or experiment here, including reading tests, "
            "but only your final JSON CodingProposal is authoritative. "
            "Do not include tests or verification files in that final proposal "
            "unless they are explicitly task-authorized. CAR will validate the "
            "complete proposal, apply it to a pristine baseline, and run final "
            "verification there."
        ),
        "\nThe filesystem delta remains untrusted and is not accepted by this runtime.",
    ]
    if request.handoff is not None:
        sections.append(
            "\n## Existing structured failure evidence\n"
            + render_codex_handoff_markdown(request.handoff)
        )
    return "\n".join(sections)


def _result(
    failure_kind: CodexWriteFailureKind,
    workspace: ProjectedIsolatedWorkspace | None = None,
    stdout: str = "",
    stderr: str = "",
    attempted: bool = False,
    *,
    timed_out: bool = False,
    exit_code: int | None = None,
    usage: TokenUsage | None = None,
) -> ControlledCodexWriteResult:
    return ControlledCodexWriteResult(
        attempted=attempted,
        process_succeeded=False,
        exit_code=exit_code,
        failure_kind=failure_kind,
        timed_out=timed_out,
        stdout=stdout,
        stderr=stderr,
        usage=usage,
        baseline_digest=workspace.baseline_digest if workspace is not None else None,
        baseline_head_oid=workspace.baseline_head_oid if workspace is not None else None,
    )


def _health_failure(status: ControlledCodexHealthStatus) -> CodexWriteFailureKind:
    if status == ControlledCodexHealthStatus.CLI_NOT_FOUND:
        return CodexWriteFailureKind.CODEX_CLI_NOT_FOUND
    if status == ControlledCodexHealthStatus.NOT_AUTHENTICATED:
        return CodexWriteFailureKind.CODEX_NOT_AUTHENTICATED
    return CodexWriteFailureKind.CODEX_NOT_READY


def _truncate(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value
    if maximum <= len(TRUNCATION_MARKER):
        return TRUNCATION_MARKER[:maximum]
    return value[: maximum - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER


def _text(value: str | bytes | None) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""


class _ProposalOutputFiles:
    def __init__(self, schema_path: Path, proposal_path: Path) -> None:
        self.schema_path = schema_path
        self.proposal_path = proposal_path


@contextmanager
def _proposal_output_files(parent: Path):
    """Keep native Codex structured-output files outside source and for one invocation only."""
    with tempfile.TemporaryDirectory(prefix="car-codex-output-", dir=parent) as directory:
        root = Path(directory)
        schema_path = root / "coding-proposal.schema.json"
        proposal_path = root / "coding-proposal.json"
        schema_path.write_text(json.dumps(_codex_proposal_schema()), encoding="utf-8")
        yield _ProposalOutputFiles(schema_path, proposal_path)


def _codex_proposal_schema() -> dict[str, object]:
    """Derive Codex's strict-output schema from CAR's authoritative proposal model."""
    schema = CodingProposal.model_json_schema()
    _make_schema_strict(schema)
    return schema


def _make_schema_strict(node: object) -> None:
    """Close every object and require its declared properties for Codex outputs."""
    if isinstance(node, dict):
        if node.get("type") == "object":
            node["additionalProperties"] = False
            properties = node.get("properties")
            if isinstance(properties, dict):
                node["required"] = list(properties)
        for value in node.values():
            _make_schema_strict(value)
    elif isinstance(node, list):
        for value in node:
            _make_schema_strict(value)


def _read_coding_proposal(path: Path) -> str | None:
    """Accept only native structured output that the authoritative model validates."""
    try:
        value = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not value.strip():
        return None
    try:
        CodingProposal.model_validate_json(value)
    except (ValueError, json.JSONDecodeError):
        return None
    return value


def _parse_jsonl_output(value: str) -> tuple[str | None, TokenUsage | None] | None:
    """Read only official Codex JSONL events; never parse human terminal output."""
    final_message: str | None = None
    usage: TokenUsage | None = None
    saw_event = False
    for line in value.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            return None
        saw_event = True
        if event["type"] == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    final_message = text.strip()
        elif event["type"] == "turn.completed":
            raw_usage = event.get("usage")
            if raw_usage is not None:
                if not isinstance(raw_usage, dict):
                    return None
                usage = _usage_from_turn_completed(raw_usage)
    return (final_message, usage) if saw_event else None


def _usage_from_turn_completed(value: dict[str, object]) -> TokenUsage:
    """Map provider-reported token dimensions without pricing cache writes."""

    def token(name: str) -> int | None:
        candidate = value.get(name)
        return (
            candidate
            if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate >= 0
            else None
        )

    return TokenUsage(
        input_tokens=token("input_tokens"),
        cached_input_tokens=token("cached_input_tokens"),
        output_tokens=token("output_tokens"),
        reasoning_tokens=token("reasoning_output_tokens"),
        cache_write_input_tokens=token("cache_write_input_tokens"),
        total_tokens=None,
        reasoning_tokens_included_in_output=True,
        source=UsageSource.PROVIDER_REPORTED,
    )
