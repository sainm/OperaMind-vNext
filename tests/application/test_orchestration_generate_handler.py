from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from operamind.application.change_orchestration import ChangeOrchestrationBlockedError
from operamind.commands import orchestration_generate_handler as command


class ConnectionContext:
    def __enter__(self) -> object:
        return object()

    def __exit__(self, *args: object) -> None:
        return None


def test_generate_handler_writes_canonical_artifact_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    observed: dict[str, object] = {}

    class Service:
        def __init__(self, *, connection: object, repository_root: Path) -> None:
            observed["root"] = repository_root

        def orchestrate(self, *, change_request_id: str, actor: str) -> object:
            observed.update(change_request_id=change_request_id, actor=actor)
            return SimpleNamespace(
                created=True,
                orchestration={
                    "orchestration_id": "orchestration-1",
                    "artifact_refs": {"test_plan": "test-plan-1"},
                },
            )

    monkeypatch.setenv("OPERAMIND_DATABASE_URL", "postgresql:///unused")
    monkeypatch.setattr(command.psycopg, "connect", lambda _url: ConnectionContext())
    monkeypatch.setattr(command, "ChangeOrchestrationService", Service)
    monkeypatch.setattr(command.sys, "stdin", io.StringIO(json.dumps(_input())))

    assert command.main(["--root", str(tmp_path)]) == 0

    result = json.loads(capsys.readouterr().out)
    assert observed == {
        "root": tmp_path,
        "change_request_id": "request-1",
        "actor": "worker-1",
    }
    assert result == {
        "outcome": "completed",
        "summary": "Canonical change orchestration was generated.",
        "artifact_refs": ["orchestration-1", "test-plan-1"],
        "evidence": {
            "handler": "canonical_change_orchestration",
            "created": True,
            "artifact_count": 2,
        },
    }


def test_generate_handler_returns_blocked_result_for_canonical_blocker(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    class Service:
        def __init__(self, *, connection: object, repository_root: Path) -> None:
            pass

        def orchestrate(self, *, change_request_id: str, actor: str) -> object:
            raise ChangeOrchestrationBlockedError("Golden Dataset binding is missing")

    monkeypatch.setenv("OPERAMIND_DATABASE_URL", "postgresql:///unused")
    monkeypatch.setattr(command.psycopg, "connect", lambda _url: ConnectionContext())
    monkeypatch.setattr(command, "ChangeOrchestrationService", Service)
    monkeypatch.setattr(command.sys, "stdin", io.StringIO(json.dumps(_input())))

    assert command.main(["--root", str(tmp_path)]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "blocked"
    assert result["evidence"] == {"blocking_reason": "Golden Dataset binding is missing"}


def test_generate_handler_rejects_lease_token_in_child_input() -> None:
    value = _input()
    value["task"]["lease_token"] = "must-not-cross-process-boundary"

    with pytest.raises(ValueError, match="must not contain a lease token"):
        command._load_task(json.dumps(value))


def _input() -> dict[str, object]:
    return {
        "protocol_version": "orchestration_worker_handler_v1",
        "task": {
            "orchestration_task_id": "task-1",
            "change_request_id": "request-1",
            "action": "generate_orchestration",
            "claims": [
                {"status": "expired", "executor_id": "old-worker"},
                {"status": "active", "executor_id": "worker-1"},
            ],
        },
    }
