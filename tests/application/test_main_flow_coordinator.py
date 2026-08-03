from __future__ import annotations

from pathlib import Path
from threading import Event
from typing import Any

import pytest

from operamind.application import main_flow_coordinator as coordinator_module
from operamind.application.main_flow_coordinator import MainFlowCoordinator
from operamind.application.orchestration_task import OrchestrationSchedulingPolicy


class _ConnectionContext:
    def __enter__(self) -> object:
        return object()

    def __exit__(self, *_args: object) -> None:
        return None


class _Service:
    def __init__(self, calls: list[tuple[str, object]]) -> None:
        self._calls = calls
        self._resume_counts: dict[str, int] = {}

    def list_projects(self) -> dict[str, object]:
        return {"projects": [{"project_id": "project-1"}, {"name": "invalid"}]}

    def list_change_requests(self, *, project_id: str) -> dict[str, object]:
        assert project_id == "project-1"
        return {
            "change_requests": [
                {"change_request_id": "ready-request"},
                {"change_request_id": "copilot-request"},
                {"invalid": True},
            ]
        }

    def resume_pending_change_automation(self, *, request_id: str, actor: str) -> dict[str, object]:
        self._calls.append(("resume", {"request_id": request_id, "actor": actor}))
        count = self._resume_counts.get(request_id, 0)
        self._resume_counts[request_id] = count + 1
        if request_id == "ready-request" and count == 0:
            return {
                "created": False,
                "run": {
                    "status": "waiting",
                    "next_action": "start_test_data_execution",
                },
            }
        return {
            "created": False,
            "run": {
                "status": "waiting",
                "next_action": "apply_code_change_with_copilot",
            },
        }

    def start_test_data_run(self, **values: object) -> dict[str, object]:
        self._calls.append(("reserve", values))
        return {
            "run_id": "run-1",
            "status": "running",
            "background_required": True,
        }


def test_coordinator_advances_and_executes_without_a_web_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, object]] = []
    service = _Service(calls)
    monkeypatch.setattr(
        coordinator_module.psycopg,
        "connect",
        lambda *_args, **_kwargs: _ConnectionContext(),
    )
    monkeypatch.setattr(
        coordinator_module,
        "WebControlPlaneService",
        lambda **_kwargs: service,
    )

    def execute(**values: Any) -> None:
        calls.append(("execute", values))

    monkeypatch.setattr(coordinator_module, "execute_reserved_test_data_run", execute)
    coordinator = MainFlowCoordinator(
        database_url="postgresql:///unused",
        repository_root=tmp_path,
        executor_factory=lambda _root: {},
        scheduling_policy=OrchestrationSchedulingPolicy(),
    )

    result = coordinator.run_once()

    assert result.observed_requests == 2
    assert result.reserved_runs == 1
    assert result.executed_runs == 1
    assert result.failed_requests == 0
    assert [name for name, _value in calls] == [
        "resume",
        "reserve",
        "resume",
        "execute",
        "resume",
    ]
    assert calls[1][1] == {
        "request_id": "ready-request",
        "idempotency_key": "automatic-main-flow",
        "actor": "automation:operamind",
    }


def test_coordinator_retries_missing_downstream_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, object]] = []

    class Service:
        def list_projects(self) -> dict[str, object]:
            return {"projects": [{"project_id": "project-1"}]}

        def list_change_requests(self, *, project_id: str) -> dict[str, object]:
            assert project_id == "project-1"
            return {"change_requests": [{"change_request_id": "request-1"}]}

        def resume_pending_change_automation(self, **values: object) -> dict[str, object]:
            calls.append(("resume", values))
            return {
                "run": {
                    "status": "waiting",
                    "next_action": "run_ui_verification",
                }
            }

        def execution_management(self, request_id: str) -> dict[str, object]:
            assert request_id == "request-1"
            return {
                "test_data_execution": {"run_id": "run-1", "status": "passed"},
                "change_closure": None,
            }

    monkeypatch.setattr(
        coordinator_module.psycopg,
        "connect",
        lambda *_args, **_kwargs: _ConnectionContext(),
    )
    monkeypatch.setattr(
        coordinator_module,
        "WebControlPlaneService",
        lambda **_kwargs: Service(),
    )
    monkeypatch.setattr(
        coordinator_module,
        "execute_reserved_test_data_run",
        lambda **values: calls.append(("execute", values)),
    )

    result = MainFlowCoordinator(
        database_url="postgresql:///unused",
        repository_root=tmp_path,
        executor_factory=lambda _root: {},
        scheduling_policy=OrchestrationSchedulingPolicy(),
    ).run_once()

    assert result.reserved_runs == 0
    assert result.executed_runs == 1
    assert [name for name, _value in calls] == ["resume", "execute", "resume"]


def test_coordinator_rejects_an_invalid_poll_interval(tmp_path: Path) -> None:
    coordinator = MainFlowCoordinator(
        database_url="postgresql:///unused",
        repository_root=tmp_path,
        executor_factory=lambda _root: {},
        scheduling_policy=OrchestrationSchedulingPolicy(),
    )

    with pytest.raises(ValueError, match="poll_seconds"):
        coordinator.run_forever(stop_event=Event(), poll_seconds=0)
