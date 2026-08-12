"""Ephemeral stdio MCP broker for CAR-authorized Codex workspace edits."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from car.coding.models import CodingFileContext, CodingProposal, CodingTaskContext
from car.patching.apply import SafePatchApplier
from car.patching.models import PatchValidationPolicy
from car.patching.validation import PatchValidator
from car.providers.models import RepositoryClassificationContext
from car.router.models import Route


@dataclass
class BrokerMetrics:
    patch_requests: int = 0
    patch_applied_count: int = 0
    patch_denied_count: int = 0
    rejected_paths: set[str] = field(default_factory=set)

    def as_dict(self) -> dict[str, object]:
        return {
            "broker_patch_requests": self.patch_requests,
            "broker_patch_applied_count": self.patch_applied_count,
            "broker_patch_denied_count": self.patch_denied_count,
            "broker_rejected_paths": sorted(self.rejected_paths),
        }


class CarPatchBroker:
    """Apply only CAR-validated patches inside a single isolated workspace."""

    def __init__(
        self, workspace: Path, authorized_paths: tuple[str, ...], policy: PatchValidationPolicy
    ):
        self._workspace = workspace.resolve()
        self._authorized_paths = authorized_paths
        self._policy = policy
        self.metrics = BrokerMetrics()

    def apply_patch(self, proposal_data: object) -> dict[str, object]:
        self.metrics.patch_requests += 1
        try:
            proposal = CodingProposal.model_validate(proposal_data)
        except Exception:
            return self._deny("malformed_patch")
        context = CodingTaskContext(
            task="CAR-controlled Codex workspace edit",
            route=Route.CODEX,
            repository=RepositoryClassificationContext(
                name="isolated-workspace", branch="detached", dirty=False, languages={}, systems=[]
            ),
            files=[CodingFileContext(path=path, content="") for path in self._authorized_paths],
            safe_auxiliary_paths=self._policy.safe_auxiliary_paths,
        )
        validation = PatchValidator(self._policy).validate(proposal, context, self._workspace)
        if not validation.valid or validation.patch_set is None:
            return self._deny(
                "path_not_authorized",
                tuple(item.path for item in validation.violations if item.path is not None),
            )
        transaction = SafePatchApplier(self._policy).apply(self._workspace, validation.patch_set)
        if not transaction.result.succeeded:
            return self._deny("apply_failed", (transaction.result.failure_path,))
        transaction.finalize()
        self.metrics.patch_applied_count += 1
        return {
            "status": "applied",
            "task_changed_paths": validation.task_changed_paths,
            "auxiliary_changed_paths": validation.auxiliary_changed_paths,
        }

    def _deny(self, reason: str, paths: tuple[str | None, ...] = ()) -> dict[str, object]:
        rejected = tuple(path for path in paths if isinstance(path, str))
        self.metrics.patch_denied_count += 1
        self.metrics.rejected_paths.update(rejected)
        return {"status": "denied", "reason": reason, "rejected_paths": list(rejected)}


def serve(broker: CarPatchBroker) -> None:
    """Serve only initialize, tools/list, and car_apply_patch over stdio."""
    for line in sys.stdin:
        try:
            request = json.loads(line)
            method = request.get("method")
            params = request.get("params", {})
            if method == "initialize":
                result: object = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}}
            elif method == "tools/list":
                result = {
                    "tools": [
                        {
                            "name": "car_apply_patch",
                            "description": "Apply one CAR-authorized structured patch proposal.",
                            "inputSchema": {
                                "type": "object",
                                "required": ["proposal"],
                                "properties": {"proposal": {"type": "object"}},
                            },
                        }
                    ]
                }
            elif method == "tools/call" and params.get("name") == "car_apply_patch":
                arguments = params.get("arguments", {})
                value = broker.apply_patch(arguments.get("proposal"))
                result = {
                    "content": [{"type": "text", "text": json.dumps(value)}],
                    "isError": False,
                }
            elif method == "notifications/initialized":
                continue
            else:
                raise ValueError("unsupported request")
            if "id" in request:
                print(
                    json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}),
                    flush=True,
                )
        except Exception:
            if isinstance(locals().get("request"), dict) and "id" in request:
                print(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": request["id"],
                            "error": {"code": -32602, "message": "invalid request"},
                        }
                    ),
                    flush=True,
                )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--authorized-path", action="append", default=[])
    args = parser.parse_args()
    serve(
        CarPatchBroker(Path(args.workspace), tuple(args.authorized_path), PatchValidationPolicy())
    )


if __name__ == "__main__":
    main()
