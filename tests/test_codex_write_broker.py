"""Offline tests for the constrained, CAR-owned Codex edit broker."""

from pathlib import Path

from car.codex_write.broker import CarPatchBroker
from car.patching.models import PatchValidationPolicy


def _proposal(*changes: tuple[str, str]) -> dict[str, object]:
    return {
        "summary": "Synthetic change",
        "changes": [
            {"path": path, "operation": "modify", "patch": patch} for path, patch in changes
        ],
    }


def _patch(path: str, before: str, after: str) -> str:
    return f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{before}\n+{after}\n"


def test_authorized_patch_applies_and_safe_auxiliary_is_allowed(tmp_path: Path):
    (tmp_path / "double.py").write_text("return value\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".cache\n", encoding="utf-8")
    broker = CarPatchBroker(tmp_path, ("double.py",), PatchValidationPolicy())

    code = broker.apply_patch(
        _proposal(("double.py", _patch("double.py", "return value", "return value * 2")))
    )
    auxiliary = broker.apply_patch(
        _proposal((".gitignore", _patch(".gitignore", ".cache", ".tmp")))
    )

    assert code["status"] == auxiliary["status"] == "applied"
    assert (tmp_path / "double.py").read_text(encoding="utf-8") == "return value * 2\n"
    assert broker.metrics.as_dict()["broker_patch_applied_count"] == 2


def test_unauthorized_mixed_patch_is_atomic_and_a_second_request_can_succeed(tmp_path: Path):
    (tmp_path / "double.py").write_text("return value\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_double.py").write_text("assert double(2) == 4\n", encoding="utf-8")
    broker = CarPatchBroker(tmp_path, ("double.py",), PatchValidationPolicy())

    denied = broker.apply_patch(
        _proposal(
            ("double.py", _patch("double.py", "return value", "return value * 2")),
            (
                "tests/test_double.py",
                _patch("tests/test_double.py", "assert double(2) == 4", "assert double(2) == 5"),
            ),
        )
    )
    assert denied == {
        "status": "denied",
        "reason": "path_not_authorized",
        "rejected_paths": ["tests/test_double.py"],
    }
    assert (tmp_path / "double.py").read_text(encoding="utf-8") == "return value\n"
    applied = broker.apply_patch(
        _proposal(("double.py", _patch("double.py", "return value", "return value * 2")))
    )

    assert applied["status"] == "applied"
    assert broker.metrics.as_dict() == {
        "broker_patch_requests": 2,
        "broker_patch_applied_count": 1,
        "broker_patch_denied_count": 1,
        "broker_rejected_paths": ["tests/test_double.py"],
    }


def test_broker_preserves_existing_path_and_protected_path_defenses(tmp_path: Path):
    (tmp_path / "double.py").write_text("return value\n", encoding="utf-8")
    broker = CarPatchBroker(
        tmp_path, ("double.py", "tests/test_double.py", ".git/config"), PatchValidationPolicy()
    )

    absolute = broker.apply_patch(_proposal(("C:/outside.py", _patch("C:/outside.py", "x", "y"))))
    traversal = broker.apply_patch(_proposal(("../outside.py", _patch("../outside.py", "x", "y"))))
    protected = broker.apply_patch(_proposal((".git/config", _patch(".git/config", "x", "y"))))

    assert absolute["status"] == traversal["status"] == protected["status"] == "denied"
    assert (tmp_path / "double.py").read_text(encoding="utf-8") == "return value\n"
