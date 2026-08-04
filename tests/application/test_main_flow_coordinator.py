from __future__ import annotations

from pathlib import Path
from threading import Event
from typing import Any

import pytest

from operamind.application import main_flow_coordinator as coordinator_module
from operamind.application.main_flow_coordinator import MainFlowCoordinator
from operamind.application.orchestration_task import OrchestrationSchedulingPolicy
from operamind.infrastructure.postgres.change_automation_repository import (
    ChangeAutomationCoordinatorCandidate,
)


class _ConnectionContext:
    def __enter__(self) -> object:
        return object()

    def __exit__(self, *_args: object) -> None:
        return None


class _Service:
    def __init__(self, calls: list[tuple[str, object]]) -> None:
        self._calls = calls
        self._resume_counts: dict[str, int] = {}

    def resume_pending_change_automation(self, *, request_id: str, actor: str) -> dict[str, object]:
        self._calls.append(("resume", {"request_id": request_id, "actor": actor}))
        count = self._resume_counts.get(request_id, 0)
        self._resume_counts[request_id] = count + 1
        if request_id == "ready-request" and count == 0:
            return {
                "created": False,
                "run": {
                    "current_stage": "test_data_execution",
                    "status": "waiting",
                    "next_action": "start_test_data_execution",
                },
            }
        return {
            "created": False,
            "run": {
                "current_stage": "code_change",
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
    candidates = (
        _candidate("ready-request", reason="automatic_action"),
        _candidate("copilot-request", reason="running_recovery"),
    )
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
    monkeypatch.setattr(
        coordinator_module,
        "ChangeAutomationRepository",
        lambda _connection: _CandidateRepository(candidates),
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
        def resume_pending_change_automation(self, **values: object) -> dict[str, object]:
            calls.append(("resume", values))
            return {
                "run": {
                    "current_stage": "ui_verification",
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
        "ChangeAutomationRepository",
        lambda _connection: _CandidateRepository(
            (_candidate("request-1", reason="automatic_action"),)
        ),
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


def test_coordinator_recovers_running_run_and_counts_fail_closed_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, object]] = []
    resume_counts: dict[str, int] = {}

    class Service:
        def resume_pending_change_automation(
            self, *, request_id: str, actor: str
        ) -> dict[str, object]:
            calls.append(("resume", {"request_id": request_id, "actor": actor}))
            count = resume_counts.get(request_id, 0)
            resume_counts[request_id] = count + 1
            if request_id == "invalid-request":
                raise ValueError("invalid Canonical state")
            if count:
                raise ValueError("completion refresh failed")
            return {
                "run": {
                    "current_stage": "test_data_execution",
                    "status": "running",
                    "next_action": "refresh",
                }
            }

        def execution_management(self, request_id: str) -> dict[str, object]:
            assert request_id == "running-request"
            return {
                "test_data_execution": {"run_id": "run-running", "status": "running"}
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
        "ChangeAutomationRepository",
        lambda _connection: _CandidateRepository(
            (
                _candidate("running-request", reason="running_recovery"),
                _candidate("invalid-request", reason="automatic_action"),
            )
        ),
    )
    monkeypatch.setattr(
        coordinator_module,
        "execute_reserved_test_data_run",
        lambda **values: calls.append(("execute", values)) or "failed",
    )

    result = MainFlowCoordinator(
        database_url="postgresql:///unused",
        repository_root=tmp_path,
        executor_factory=lambda _root: {},
        scheduling_policy=OrchestrationSchedulingPolicy(),
    ).run_once()

    assert result.observed_requests == 2
    assert result.reserved_runs == 0
    assert result.executed_runs == 0
    assert result.failed_requests == 3
    assert [name for name, _value in calls].count("execute") == 1


def test_coordinator_run_forever_recovers_an_iteration_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stop = Event()
    iterations: list[str] = []

    def fail_once(_self: MainFlowCoordinator) -> None:
        iterations.append("attempted")
        stop.set()
        raise RuntimeError("transient failure")

    monkeypatch.setattr(MainFlowCoordinator, "run_once", fail_once)
    coordinator = MainFlowCoordinator(
        database_url="postgresql:///unused",
        repository_root=tmp_path,
        executor_factory=lambda _root: {},
        scheduling_policy=OrchestrationSchedulingPolicy(),
    )

    coordinator.run_forever(stop_event=stop, poll_seconds=0.1)

    assert iterations == ["attempted"]


def test_coordinator_rejects_an_invalid_poll_interval(tmp_path: Path) -> None:
    coordinator = MainFlowCoordinator(
        database_url="postgresql:///unused",
        repository_root=tmp_path,
        executor_factory=lambda _root: {},
        scheduling_policy=OrchestrationSchedulingPolicy(),
    )

    with pytest.raises(ValueError, match="poll_seconds"):
        coordinator.run_forever(stop_event=Event(), poll_seconds=0)


def test_coordinator_uses_a_bounded_candidate_query_instead_of_project_scans(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed_limits: list[int] = []

    class Repository:
        def list_coordinator_candidates(
            self, *, limit: int
        ) -> tuple[ChangeAutomationCoordinatorCandidate, ...]:
            observed_limits.append(limit)
            return ()

    class Service:
        def list_projects(self) -> dict[str, object]:
            raise AssertionError("Coordinator must not scan all Projects")

        def list_change_requests(self, *, project_id: str) -> dict[str, object]:
            raise AssertionError(f"Coordinator must not scan Project {project_id}")

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
        "ChangeAutomationRepository",
        lambda _connection: Repository(),
    )

    result = MainFlowCoordinator(
        database_url="postgresql:///unused",
        repository_root=tmp_path,
        executor_factory=lambda _root: {},
        scheduling_policy=OrchestrationSchedulingPolicy(),
        candidate_limit=37,
    ).run_once()

    assert result.observed_requests == 0
    assert result.executed_runs == 0
    assert observed_limits == [37]


class _CandidateRepository:
    def __init__(self, candidates: tuple[ChangeAutomationCoordinatorCandidate, ...]) -> None:
        self._candidates = candidates

    def list_coordinator_candidates(
        self, *, limit: int
    ) -> tuple[ChangeAutomationCoordinatorCandidate, ...]:
        assert limit == 200
        return self._candidates


def _candidate(request_id: str, *, reason: str) -> ChangeAutomationCoordinatorCandidate:
    return ChangeAutomationCoordinatorCandidate(
        automation_run_id=f"automation-{request_id}",
        change_request_id=request_id,
        project_id="project-1",
        status="waiting" if reason == "automatic_action" else "running",
        current_stage="test_data_execution",
        next_action="start_test_data_execution" if reason == "automatic_action" else "refresh",
        selection_reason=reason,
    )
