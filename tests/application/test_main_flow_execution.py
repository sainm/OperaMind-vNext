from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from operamind.application import main_flow_execution as execution_module
from operamind.application.main_flow_execution import (
    _publish_background_failure,
    default_test_data_executor_factory,
    execute_reserved_test_data_run,
)
from operamind.application.test_data_execution import (
    TestDataExecutionProgress as ExecutionProgress,
)
from operamind.infrastructure.postgres.test_data_execution_repository import (
    TestDataExecutionRecord as ExecutionRecord,
)


class _ConnectionContext:
    def __enter__(self) -> object:
        return object()

    def __exit__(self, *_args: object) -> None:
        return None


class _Repository:
    def __init__(self, record: ExecutionRecord | None) -> None:
        self.record = record
        self.events: list[object] = []

    def get_record(self, run_id: str) -> ExecutionRecord | None:
        assert run_id == "run-1"
        return self.record

    def append_event(self, event: object) -> None:
        self.events.append(event)


def _record(*, status: str = "running") -> ExecutionRecord:
    return ExecutionRecord(
        created=True,
        run_id="run-1",
        execution_result_id="result-1",
        orchestration_id="orchestration-1",
        test_data_plan_id="plan-1",
        approval_grant_id="grant-1",
        project_id="project-1",
        analysis_case_id="case-1",
        status=status,
        started_at=datetime(2026, 7, 28, tzinfo=UTC),
        completed_at=None,
    )


def _patch_common(
    monkeypatch: pytest.MonkeyPatch,
    repository: _Repository,
) -> None:
    monkeypatch.setattr(
        execution_module.psycopg,
        "connect",
        lambda *_args, **_kwargs: _ConnectionContext(),
    )
    monkeypatch.setattr(
        execution_module.ContractCatalog,
        "load",
        lambda _path: object(),
    )
    monkeypatch.setattr(
        execution_module,
        "TestDataExecutionRepository",
        lambda *_args, **_kwargs: repository,
    )
    monkeypatch.setattr(
        execution_module,
        "WebControlPlaneRepository",
        lambda *_args, **_kwargs: SimpleNamespace(
            project_test_base_url=lambda _project_id: "http://127.0.0.1:8080"
        ),
    )


def test_reserved_run_publishes_progress_ui_and_closure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = _Repository(_record())
    requests: list[object] = []
    ui_publications: list[dict[str, object]] = []
    closure_calls: list[dict[str, object]] = []
    _patch_common(monkeypatch, repository)

    class ExecutionService:
        def __init__(self, **values: Any) -> None:
            self.progress = values["progress_sink"]

        def execute_reserved(self, request: object) -> object:
            requests.append(request)
            self.progress(
                ExecutionProgress(
                    event_type="step_passed",
                    flow_id="flow-1",
                    phase="setup",
                    step_id="step-1",
                    status="passed",
                    message="完了",
                )
            )
            return SimpleNamespace(artifact={"status": "passed"})

    class UiService:
        def __init__(self, *_args: object) -> None:
            pass

        def publish(self, **values: object) -> None:
            ui_publications.append(values)

    class ClosureService:
        def __init__(self, *_args: object) -> None:
            pass

        def close(self, **values: object) -> object:
            closure_calls.append(values)
            return SimpleNamespace(record=SimpleNamespace(closure_result_id="closure-1"))

    monkeypatch.setattr(execution_module, "TestDataExecutionService", ExecutionService)
    monkeypatch.setattr(execution_module, "TestDataUiVerificationService", UiService)
    monkeypatch.setattr(execution_module, "ChangeClosureService", ClosureService)

    execute_reserved_test_data_run(
        database_url="postgresql:///unused",
        repository_root=tmp_path,
        run_id="run-1",
        executor_factory=lambda _root: {},
    )

    request = requests[0]
    assert request.actor == "main-flow-worker"
    assert request.base_url == "http://127.0.0.1:8080"
    assert ui_publications == [
        {
            "orchestration_id": "orchestration-1",
            "execution_result": {"status": "passed"},
        }
    ]
    assert closure_calls == [
        {
            "orchestration_id": "orchestration-1",
            "actor": "main-flow-worker",
        }
    ]
    assert [event.event_type for event in repository.events] == [
        "step_passed",
        "closure_generated",
    ]


def test_execution_transaction_finishes_before_ui_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = _Repository(_record())
    connection_exits = 0
    publication_exit_counts: list[int] = []

    class ConnectionContext:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *_args: object) -> None:
            nonlocal connection_exits
            connection_exits += 1

    monkeypatch.setattr(
        execution_module.psycopg,
        "connect",
        lambda *_args, **_kwargs: ConnectionContext(),
    )
    monkeypatch.setattr(execution_module.ContractCatalog, "load", lambda _path: object())
    monkeypatch.setattr(
        execution_module,
        "TestDataExecutionRepository",
        lambda *_args, **_kwargs: repository,
    )
    monkeypatch.setattr(
        execution_module,
        "WebControlPlaneRepository",
        lambda *_args, **_kwargs: SimpleNamespace(
            project_test_base_url=lambda _project_id: "http://127.0.0.1:8080"
        ),
    )

    class ExecutionService:
        def __init__(self, **_values: Any) -> None:
            pass

        def execute_reserved(self, _request: object) -> object:
            return SimpleNamespace(artifact={"status": "passed"})

    class UiService:
        def __init__(self, *_args: object) -> None:
            pass

        def publish(self, **_values: object) -> None:
            publication_exit_counts.append(connection_exits)

    class ClosureService:
        def __init__(self, *_args: object) -> None:
            pass

        def close(self, **_values: object) -> object:
            return SimpleNamespace(record=SimpleNamespace(closure_result_id="closure-1"))

    monkeypatch.setattr(execution_module, "TestDataExecutionService", ExecutionService)
    monkeypatch.setattr(execution_module, "TestDataUiVerificationService", UiService)
    monkeypatch.setattr(execution_module, "ChangeClosureService", ClosureService)

    execute_reserved_test_data_run(
        database_url="postgresql:///unused",
        repository_root=tmp_path,
        run_id="run-1",
        executor_factory=lambda _root: {},
    )

    assert publication_exit_counts == [1]
    assert connection_exits == 2


def test_outer_execution_failure_is_forwarded_to_failure_publisher(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    failure: dict[str, object] = {}

    def fail_connect(*_args: object, **_kwargs: object) -> object:
        raise OSError("database unavailable")

    def publish(**values: object) -> None:
        failure.update(values)

    monkeypatch.setattr(execution_module.psycopg, "connect", fail_connect)
    monkeypatch.setattr(execution_module, "_publish_background_failure", publish)

    execute_reserved_test_data_run(
        database_url="postgresql:///unused",
        repository_root=tmp_path,
        run_id="run-1",
    )

    assert failure["run_id"] == "run-1"
    assert isinstance(failure["error"], OSError)


def test_failure_publisher_marks_running_reservation_and_generates_closure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = _Repository(_record())
    failures: list[tuple[object, str]] = []
    _patch_common(monkeypatch, repository)

    class ExecutionService:
        def __init__(self, **_values: Any) -> None:
            pass

        def fail_reserved(self, request: object, *, reason: str) -> None:
            failures.append((request, reason))

    class ClosureService:
        def __init__(self, *_args: object) -> None:
            pass

        def close(self, **_values: object) -> object:
            return SimpleNamespace(record=SimpleNamespace(closure_result_id="closure-failed"))

    monkeypatch.setattr(execution_module, "TestDataExecutionService", ExecutionService)
    monkeypatch.setattr(execution_module, "ChangeClosureService", ClosureService)

    _publish_background_failure(
        database_url="postgresql:///unused",
        repository_root=tmp_path,
        run_id="run-1",
        error=RuntimeError("secret detail"),
    )

    request, reason = failures[0]
    assert request.actor == "main-flow-worker"
    assert reason == "Background TestDataPlan worker failed before completion (RuntimeError)"
    assert "secret detail" not in reason
    assert [event.event_type for event in repository.events] == [
        "background_failed",
        "closure_generated",
    ]


def test_failure_publisher_marks_terminal_run_for_downstream_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = _Repository(_record(status="passed"))
    _patch_common(monkeypatch, repository)

    _publish_background_failure(
        database_url="postgresql:///unused",
        repository_root=tmp_path,
        run_id="run-1",
        error=RuntimeError("publication failed"),
    )

    assert [event.event_type for event in repository.events] == [
        "downstream_publication_failed"
    ]
    assert repository.events[0].status == "passed"


def test_default_factory_uses_only_restricted_unbound_adapters(tmp_path: Path) -> None:
    executors = default_test_data_executor_factory(tmp_path)

    assert set(executors) == {"http", "ui"}
