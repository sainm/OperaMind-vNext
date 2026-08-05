from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
    TestDataExecutionClaim as ExecutionClaim,
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
    def __init__(self, record: ExecutionRecord | None, *, claim_outcome: str = "claimed") -> None:
        self.record = record
        self.claim_outcome = claim_outcome
        self.events: list[object] = []

    def get_record(self, run_id: str) -> ExecutionRecord | None:
        assert run_id == "run-1"
        return self.record

    def claim_execution(self, **values: object) -> ExecutionClaim:
        assert values["run_id"] == "run-1"
        assert self.record is not None
        return ExecutionClaim(self.claim_outcome, self.record)

    def heartbeat_execution(self, **_values: object) -> bool:
        return True

    def append_event(self, event: object) -> None:
        self.events.append(event)


def _record(*, status: str = "running", **changes: object) -> ExecutionRecord:
    values: dict[str, object] = {
        "created": True,
        "run_id": "run-1",
        "execution_result_id": "result-1",
        "orchestration_id": "orchestration-1",
        "test_data_plan_id": "plan-1",
        "approval_grant_id": "grant-1",
        "project_id": "project-1",
        "analysis_case_id": "case-1",
        "status": status,
        "started_at": datetime(2026, 7, 28, tzinfo=UTC),
        "completed_at": None,
    }
    values.update(changes)
    return ExecutionRecord(**values)  # type: ignore[arg-type]


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
    monkeypatch.setattr(
        execution_module,
        "_identity_providers_for_project",
        lambda *_args: execution_module.default_data_identity_providers(),
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

    outcome = execute_reserved_test_data_run(
        database_url="postgresql:///unused",
        repository_root=tmp_path,
        run_id="run-1",
        executor_factory=lambda _root: {},
    )

    assert outcome == "executed"
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
        "_identity_providers_for_project",
        lambda *_args: execution_module.default_data_identity_providers(),
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

    outcome = execute_reserved_test_data_run(
        database_url="postgresql:///unused",
        repository_root=tmp_path,
        run_id="run-1",
        executor_factory=lambda _root: {},
    )

    assert outcome == "executed"
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

    outcome = execute_reserved_test_data_run(
        database_url="postgresql:///unused",
        repository_root=tmp_path,
        run_id="run-1",
    )

    assert outcome == "failed"
    assert failure["run_id"] == "run-1"
    assert isinstance(failure["error"], OSError)


def test_live_execution_claim_is_not_run_twice(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = _Repository(_record(), claim_outcome="busy")
    _patch_common(monkeypatch, repository)
    monkeypatch.setattr(
        execution_module,
        "TestDataExecutionService",
        lambda **_kwargs: pytest.fail("busy Claim must not create an executor"),
    )

    outcome = execute_reserved_test_data_run(
        database_url="postgresql:///unused",
        repository_root=tmp_path,
        run_id="run-1",
    )

    assert outcome == "busy"
    assert repository.events == []


def test_expired_execution_claim_is_recovered_without_replaying_test_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    expired_at = datetime.now(UTC) - timedelta(seconds=1)
    repository = _Repository(
        _record(execution_owner="dead-worker", lease_expires_at=expired_at),
        claim_outcome="stale",
    )
    recovered: list[object] = []
    _patch_common(monkeypatch, repository)

    class ExecutionService:
        def __init__(self, **values: object) -> None:
            assert values["executors"] == {}

        def recover(self, request: object) -> object:
            recovered.append(request)
            return SimpleNamespace(artifact={"status": "interrupted"})

    monkeypatch.setattr(execution_module, "TestDataExecutionService", ExecutionService)
    monkeypatch.setattr(
        execution_module,
        "TestDataUiVerificationService",
        lambda *_args: SimpleNamespace(publish=lambda **_kwargs: None),
    )
    monkeypatch.setattr(
        execution_module,
        "ChangeClosureService",
        lambda *_args: SimpleNamespace(
            close=lambda **_kwargs: SimpleNamespace(
                record=SimpleNamespace(closure_result_id="closure-interrupted")
            )
        ),
    )

    outcome = execute_reserved_test_data_run(
        database_url="postgresql:///unused",
        repository_root=tmp_path,
        run_id="run-1",
        executor_factory=lambda _root: pytest.fail("stale Run must not replay executors"),
    )

    assert outcome == "recovered"
    assert len(recovered) == 1
    assert recovered[0].stale_before == expired_at


def test_heartbeat_ownership_loss_sets_the_execution_cancellation_signal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = _Repository(_record())
    repository.heartbeat_execution = lambda **_values: False  # type: ignore[method-assign]
    _patch_common(monkeypatch, repository)
    monkeypatch.setattr(execution_module, "_EXECUTION_HEARTBEAT_SECONDS", 0)
    lease_lost = execution_module.Event()

    execution_module._heartbeat_execution_lease(
        database_url="postgresql:///unused",
        repository_root=tmp_path,
        run_id="run-1",
        executor_id="worker-1",
        stop_event=execution_module.Event(),
        lease_lost_event=lease_lost,
    )

    assert lease_lost.is_set()


def test_lost_lease_aborts_execution_before_result_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = _Repository(_record())
    failures: list[dict[str, object]] = []
    _patch_common(monkeypatch, repository)

    class LeaseLosingThread:
        def __init__(self, **values: object) -> None:
            self.kwargs = values["kwargs"]

        def start(self) -> None:
            assert isinstance(self.kwargs, dict)
            self.kwargs["lease_lost_event"].set()

        def join(self, *, timeout: float) -> None:
            assert timeout == 5

    class ExecutionService:
        def __init__(self, **values: object) -> None:
            self.progress = values["progress_sink"]

        def execute_reserved(self, _request: object) -> object:
            self.progress(ExecutionProgress(event_type="run_started", status="running"))
            pytest.fail("a lost lease must abort before an execution result is returned")

    monkeypatch.setattr(execution_module, "Thread", LeaseLosingThread)
    monkeypatch.setattr(execution_module, "TestDataExecutionService", ExecutionService)
    monkeypatch.setattr(
        execution_module,
        "_publish_background_failure",
        lambda **values: failures.append(values),
    )

    outcome = execute_reserved_test_data_run(
        database_url="postgresql:///unused",
        repository_root=tmp_path,
        run_id="run-1",
        executor_factory=lambda _root: {},
    )

    assert outcome == "failed"
    assert len(failures) == 1
    assert isinstance(failures[0]["error"], RuntimeError)
    assert failures[0]["executor_id"].startswith("main-flow:")


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


def test_failure_publisher_does_not_overwrite_a_new_execution_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = _Repository(_record(execution_owner="new-worker"))
    _patch_common(monkeypatch, repository)
    monkeypatch.setattr(
        execution_module,
        "TestDataExecutionService",
        lambda **_values: pytest.fail("an old executor must not publish a failure"),
    )

    _publish_background_failure(
        database_url="postgresql:///unused",
        repository_root=tmp_path,
        run_id="run-1",
        executor_id="old-worker",
        error=RuntimeError("lost lease"),
    )

    assert repository.events == []


def test_default_factory_uses_only_restricted_unbound_adapters(tmp_path: Path) -> None:
    executors = default_test_data_executor_factory(tmp_path)

    assert set(executors) == {"http", "ui"}


def test_structured_pre_action_block_is_returned_to_the_same_copilot_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[dict[str, object]] = []

    class CopilotRepository:
        def __init__(self, *_args: object) -> None:
            pass

        def record_ui_locator_blocked_feedback(self, **values: object) -> str:
            recorded.append(values)
            return "task-1"

    monkeypatch.setattr(
        execution_module,
        "CopilotCodingTaskRepository",
        CopilotRepository,
    )
    queued: list[dict[str, object]] = []
    monkeypatch.setattr(
        execution_module,
        "_publish_automatic_locator_revision",
        lambda **values: queued.append(values),
    )
    artifact = {
        "flow_results": [
            {
                "flow_id": "approval-flow",
                "step_results": [
                    {
                        "step_id": "approve-expense",
                        "phase": "setup",
                        "channel": "ui",
                        "status": "blocked",
                        "failure_stage": "pre_action_state_validation",
                        "failure_reason": "Playwright pre-action observation did not match",
                        "evidence_refs": ["evidence/blocked.png"],
                        "test_data_binding_refs": ["binding-expense-1"],
                        "locator_type": "role",
                        "record_scope_match_count": 1,
                        "action_locator_match_count": 0,
                        "observed_screen_identity_values": [
                            {"name": "expense_number", "value": "EXP-041"},
                            {"name": "password", "value": "must-not-leak"},
                        ],
                    }
                ],
                "cleanup_results": [],
            }
        ]
    }

    execution_module._publish_locator_failure_feedback(
        connection=object(),  # type: ignore[arg-type]
        contracts=object(),  # type: ignore[arg-type]
        repository_root=Path("/workspace"),
        record=_record(),
        artifact=artifact,
    )

    assert len(recorded) == 1
    assert recorded[0]["orchestration_id"] == "orchestration-1"
    feedback = recorded[0]["feedback"]
    assert isinstance(feedback, dict)
    assert feedback["failures"][0]["failure_stage"] == (  # type: ignore[index]
        "pre_action_state_validation"
    )
    assert feedback["failures"][0]["record_scope_match_count"] == 1  # type: ignore[index]
    assert feedback["failures"][0]["action_locator_match_count"] == 0  # type: ignore[index]
    assert feedback["failures"][0]["observed_screen_identity_values"] == [  # type: ignore[index]
        {"name": "expense_number", "value": "EXP-041"}
    ]
    assert feedback["next_action"] == (
        "create_new_ui_test_plan_revision_and_require_confirmation"
    )
    assert queued[0]["feedback"]["source_coding_task_id"] == "task-1"  # type: ignore[index]


def test_real_locator_failure_queues_a_deterministic_read_only_revision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    published: list[object] = []

    monkeypatch.setattr(
        execution_module,
        "ChangeOrchestrationRepository",
        lambda *_args: SimpleNamespace(
            get=lambda _orchestration_id: {"change_request_id": "change-1"}
        ),
    )

    class RevisionService:
        def __init__(self, **_values: object) -> None:
            pass

        def propose(self, **_values: object) -> dict[str, object]:
            return {
                "proposal": {
                    "proposal_id": "proposal-locator-1",
                    "analysis_status": "deterministic",
                    "source_orchestration_id": "orchestration-1",
                    "source_test_plan_id": "test-plan-1",
                }
            }

        def prepare_ai_regeneration(self, **_values: object) -> dict[str, object]:
            return {
                "operations": [{"field": "plan_structure", "operation": "regenerate"}],
                "selections": {},
            }

    monkeypatch.setattr(execution_module, "TestCaseRevisionService", RevisionService)
    monkeypatch.setattr(
        execution_module,
        "WebControlPlaneRepository",
        lambda *_args: SimpleNamespace(
            project_workspace_root=lambda _project_id: str(tmp_path)
        ),
    )

    class TaskService:
        def __init__(self, **_values: object) -> None:
            pass

        def publish(self, request: object) -> dict[str, object]:
            published.append(request)
            return {}

    monkeypatch.setattr(execution_module, "CopilotCodingTaskService", TaskService)

    execution_module._publish_automatic_locator_revision(
        connection=object(),  # type: ignore[arg-type]
        contracts=object(),  # type: ignore[arg-type]
        repository_root=tmp_path,
        record=_record(),
        feedback={
            "failure_stage": "formal_ui_run_pre_action_validation",
            "failures": [{"step_id": "approve-expense", "action_locator_match_count": 0}],
        },
    )

    assert len(published) == 1
    request = published[0]
    assert request.task_kind == "ui_test_plan_revision"  # type: ignore[attr-defined]
    assert request.initial_stage == "ui_test_revision"  # type: ignore[attr-defined]
    assert request.plan_revision_context["source_orchestration_id"] == (  # type: ignore[attr-defined]
        "orchestration-1"
    )
    assert "action_locator_match_count" in request.plan_revision_context[  # type: ignore[attr-defined]
        "locator_failure_evidence_json"
    ]
