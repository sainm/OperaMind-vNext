from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from operamind.infrastructure.postgres.copilot_coding_task_repository import (
    CLAIM_LEASE_SECONDS,
    CopilotCodingTaskRecord,
    CopilotCodingTaskRepository,
)
from operamind.infrastructure.postgres.errors import PersistenceConflictError


def _record(tmp_path: Path, **changes: object) -> CopilotCodingTaskRecord:
    record = CopilotCodingTaskRecord(
        coding_task_id="task-1",
        project_id="project-1",
        change_request_id="change-1",
        analysis_case_id="case-1",
        repository_id="repository-1",
        edit_packet_id="packet-1",
        approval_grant_id="grant-1",
        base_repository_revision="a" * 40,
        execution_mode="copilot_change_task",
        provider_route="local_bridge",
        provider_id="github-copilot",
        workspace_root=str(tmp_path.resolve()),
        state="in_progress",
        claimed_by="vscode-1",
        claim_expires_at=datetime.now(UTC) + timedelta(minutes=1),
        accepted_by="developer",
        retry_of_coding_task_id=None,
        attempt_number=1,
        current_stage="compile_test",
    )
    return replace(record, **changes)


def _repository(cursor: Mock) -> CopilotCodingTaskRepository:
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    transaction = Mock()
    transaction.__enter__ = Mock(return_value=transaction)
    transaction.__exit__ = Mock(return_value=False)
    connection = Mock()
    connection.transaction.return_value = transaction
    connection.cursor.return_value = cursor
    repository = CopilotCodingTaskRepository.__new__(CopilotCodingTaskRepository)
    repository._connection = connection
    repository._artifacts = Mock()
    return repository


def test_test_planning_does_not_require_the_closed_code_edit_grant() -> None:
    cursor = Mock()
    record = SimpleNamespace(current_stage="test_planning")

    CopilotCodingTaskRepository._require_live_grant(cursor, record)

    cursor.execute.assert_not_called()


def test_compile_test_still_requires_a_live_code_edit_grant() -> None:
    cursor = Mock()
    cursor.fetchone.return_value = (True,)
    record = SimpleNamespace(
        current_stage="compile_test",
        analysis_case_id="case-1",
        repository_id="repository-1",
        edit_packet_id="packet-1",
        approval_grant_id="grant-1",
        base_repository_revision="a" * 40,
        execution_mode="copilot_change_task",
        project_id="project-1",
    )

    CopilotCodingTaskRepository._require_live_grant(cursor, record)

    cursor.execute.assert_called_once()


def test_latest_for_request_can_filter_out_plan_revision_tasks() -> None:
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    cursor.fetchone.return_value = ("task-1",)
    connection = Mock()
    connection.cursor.return_value = cursor
    repository = CopilotCodingTaskRepository.__new__(CopilotCodingTaskRepository)
    repository._connection = connection
    repository.view = Mock(return_value={"task": {"coding_task_id": "task-1"}})  # type: ignore[method-assign]

    result = repository.latest_for_request(
        "change-1",
        task_kind="change_delivery",
    )

    assert result == {"task": {"coding_task_id": "task-1"}}
    query, parameters = cursor.execute.call_args.args
    assert "artifact.payload ->> 'task_kind'" in query
    assert "CAST(%s AS TEXT) IS NULL" in query
    assert parameters == ("change-1", "change_delivery", "change_delivery")


def test_claim_next_prioritizes_consumers_active_task_before_newer_request(
    tmp_path: Path,
) -> None:
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    cursor.fetchone.return_value = ("task-active", "vscode-1", False, None)
    transaction = Mock()
    transaction.__enter__ = Mock(return_value=transaction)
    transaction.__exit__ = Mock(return_value=False)
    connection = Mock()
    connection.transaction.return_value = transaction
    connection.cursor.return_value = cursor
    repository = CopilotCodingTaskRepository.__new__(CopilotCodingTaskRepository)
    repository._connection = connection
    repository._renew_claim_locked = Mock()  # type: ignore[method-assign]
    repository._require_locked = Mock(return_value=SimpleNamespace())  # type: ignore[method-assign]
    repository.view = Mock(return_value={"task": {"coding_task_id": "task-active"}})  # type: ignore[method-assign]

    result = repository.claim_next(
        workspace_root=tmp_path,
        consumer_id="vscode-1",
        change_request_id="change-selected",
    )

    assert result == {"task": {"coding_task_id": "task-active"}}
    query = cursor.execute.call_args.args[0]
    claimed_position = query.index("CASE WHEN task.claimed_by")
    submitted_position = query.index("request.submitted_at DESC")
    assert claimed_position < submitted_position
    assert "task.change_request_id = %s" in query
    assert "held.coding_task_id <> task.coding_task_id" in query
    assert cursor.execute.call_args.args[1][1:3] == (
        "change-selected",
        "change-selected",
    )


def test_claim_next_returns_none_when_no_task_is_available(tmp_path: Path) -> None:
    cursor = Mock()
    cursor.fetchone.return_value = None
    repository = _repository(cursor)

    assert (
        repository.claim_next(workspace_root=tmp_path, consumer_id="vscode-1")
        is None
    )


def test_resume_recovers_an_expired_claim(tmp_path: Path) -> None:
    cursor = Mock()
    expired_at = datetime.now(UTC) - timedelta(seconds=1)
    cursor.fetchone.return_value = ("old-vscode", True, expired_at)
    repository = _repository(cursor)
    record = _record(tmp_path)
    repository._require_locked = Mock(return_value=record)  # type: ignore[method-assign]
    repository._require_live_grant = Mock()  # type: ignore[method-assign]
    repository._renew_claim_locked = Mock()  # type: ignore[method-assign]
    repository.view = Mock(return_value={"state": "in_progress"})  # type: ignore[method-assign]

    result = repository.resume(
        coding_task_id="task-1", workspace_root=tmp_path, consumer_id="vscode-2"
    )

    assert result == {"state": "in_progress"}
    repository._renew_claim_locked.assert_called_once_with(  # type: ignore[attr-defined]
        cursor,
        record=record,
        consumer_id="vscode-2",
        previous_consumer="old-vscode",
        lease_expired=True,
        previous_expiry=expired_at,
    )


def test_cancel_releases_an_active_claim(tmp_path: Path) -> None:
    cursor = Mock()
    repository = _repository(cursor)
    record = _record(tmp_path)
    repository._require_locked = Mock(return_value=record)  # type: ignore[method-assign]
    repository._require_current_lease = Mock()  # type: ignore[method-assign]
    repository._append_event = Mock()  # type: ignore[method-assign]
    repository.view = Mock(return_value={"state": "cancelled"})  # type: ignore[method-assign]

    result = repository.cancel(
        coding_task_id="task-1",
        actor="developer",
        reason="user cancelled",
        idempotency_key="cancel-1",
        consumer_id="vscode-1",
    )

    assert result == {"state": "cancelled"}
    assert "claimed_by = NULL" in cursor.execute.call_args.args[0]
    repository._append_event.assert_called_once()  # type: ignore[attr-defined]


def test_accept_and_begin_mcp_follow_the_confirmed_lifecycle(tmp_path: Path) -> None:
    cursor = Mock()
    repository = _repository(cursor)
    pending = _record(tmp_path, state="pending_confirmation", accepted_by=None)
    accepted = replace(pending, state="accepted", accepted_by="developer")
    running = replace(accepted, state="in_progress")
    repository._require_locked = Mock(side_effect=[pending, accepted, accepted, running])  # type: ignore[method-assign]
    repository._require_current_lease = Mock()  # type: ignore[method-assign]
    repository._require_live_grant = Mock()  # type: ignore[method-assign]
    repository._append_event = Mock()  # type: ignore[method-assign]
    repository.view = Mock(return_value={"state": "accepted"})  # type: ignore[method-assign]

    accepted_view = repository.accept(
        coding_task_id="task-1",
        workspace_root=tmp_path,
        consumer_id="vscode-1",
        actor="developer",
    )
    running_record = repository.begin_mcp(
        coding_task_id="task-1", workspace_root=tmp_path, actor="github-copilot"
    )

    assert accepted_view == {"state": "accepted"}
    assert running_record == running
    assert repository._append_event.call_count == 2  # type: ignore[attr-defined]


def test_bind_command_records_only_matching_execution_scope(tmp_path: Path) -> None:
    cursor = Mock()
    cursor.fetchone.return_value = ("grant-1", "project-1", "case-1", "packet-1")
    repository = _repository(cursor)
    repository._require_locked = Mock(return_value=_record(tmp_path))  # type: ignore[method-assign]
    repository._append_event = Mock()  # type: ignore[method-assign]

    repository.bind_command(
        coding_task_id="task-1",
        command_execution_id="command-1",
        actor="github-copilot",
        result={
            "command_ref": "compile",
            "status": "passed",
            "exit_code": 0,
            "stdout_digest": "b" * 64,
            "stderr_digest": "c" * 64,
            "tested_content_digest": "d" * 64,
            "coverage_report": {"line_rate": 0.9},
        },
    )

    event = repository._append_event.call_args.kwargs  # type: ignore[attr-defined]
    assert event["event_type"] == "command_recorded"
    assert event["payload"]["coverage_report"] == {"line_rate": 0.9}


def test_bind_document_discovery_records_ready_rag_evidence(tmp_path: Path) -> None:
    cursor = Mock()
    repository = _repository(cursor)
    repository._require_locked = Mock(return_value=_record(tmp_path))  # type: ignore[method-assign]
    repository._append_event = Mock()  # type: ignore[method-assign]
    discovery = {"status": "ready", "documents": [{"document_id": "design-1"}]}

    repository.bind_document_discovery(
        coding_task_id="task-1",
        automation_run_id="run-1",
        subject_digest="e" * 64,
        discovery=discovery,
        actor="github-copilot",
    )

    event = repository._append_event.call_args.kwargs  # type: ignore[attr-defined]
    assert event["event_type"] == "document_discovery_bound"
    assert event["payload"]["discovery"] == discovery


@pytest.mark.parametrize(
    ("committed", "scope", "expected_state", "expected_event"),
    [
        (
            True,
            (
                "packet-1",
                "grant-1",
                "project-1",
                "case-1",
                "committed",
                "in_scope",
                True,
                "verified",
                "passed",
                False,
            ),
            "in_progress",
            "result_recorded",
        ),
        (
            False,
            (
                "packet-1",
                "grant-1",
                "project-1",
                "case-1",
                "working",
                "out_of_scope",
                None,
                "not_required",
                "not_required",
                False,
            ),
            "reanalysis_required",
            "reanalysis_required",
        ),
    ],
)
def test_bind_edit_result_advances_from_persisted_validation(
    tmp_path: Path,
    committed: bool,
    scope: tuple[object, ...],
    expected_state: str,
    expected_event: str,
) -> None:
    cursor = Mock()
    cursor.fetchone.return_value = scope
    repository = _repository(cursor)
    repository._require_locked = Mock(return_value=_record(tmp_path))  # type: ignore[method-assign]
    repository._append_event = Mock()  # type: ignore[method-assign]

    repository.bind_edit_result(
        coding_task_id="task-1",
        edit_result_id="result-1",
        actor="github-copilot",
        result={"status": scope[5], "changed_paths": ["src/a.py"]},
        committed=committed,
    )

    update = next(
        call
        for call in cursor.execute.call_args_list
        if "UPDATE copilot_coding_tasks" in call.args[0]
    )
    assert update.args[1][0] == expected_state
    assert repository._append_event.call_args.kwargs["event_type"] == expected_event  # type: ignore[attr-defined]


@pytest.mark.parametrize("complete", [False, True])
def test_record_change_outputs_advances_or_completes_stage(tmp_path: Path, complete: bool) -> None:
    cursor = Mock()
    repository = _repository(cursor)
    repository._require_locked = Mock(return_value=_record(tmp_path, current_stage="test_planning"))  # type: ignore[method-assign]
    repository._append_event = Mock()  # type: ignore[method-assign]

    repository.record_change_outputs(
        coding_task_id="task-1",
        actor="github-copilot",
        output_stage="test_planning",
        expected_stage="test_planning",
        next_stage="completed",
        output_refs={"test_plan_id": "plan-1"},
        complete=complete,
        revision_identity="revision-1",
    )

    assert repository._append_event.call_args.kwargs["payload"] == {  # type: ignore[attr-defined]
        "output_stage": "test_planning",
        "test_plan_id": "plan-1",
    }
    update_query = cursor.execute.call_args.args[0]
    assert ("state = 'completed'" in update_query) is complete


def test_bind_execution_scope_binds_an_unbound_task_after_code_scope(tmp_path: Path) -> None:
    cursor = Mock()
    cursor.fetchone.return_value = (1,)
    repository = _repository(cursor)
    unbound = _record(
        tmp_path,
        analysis_case_id=None,
        repository_id=None,
        edit_packet_id=None,
        approval_grant_id=None,
        base_repository_revision=None,
        current_stage="code_scope",
    )
    bound = _record(tmp_path)
    repository._require_locked = Mock(side_effect=[unbound, bound])  # type: ignore[method-assign]
    repository._append_event = Mock()  # type: ignore[method-assign]

    result = repository.bind_execution_scope(
        coding_task_id="task-1",
        analysis_case_id="case-1",
        repository_id="repository-1",
        edit_packet_id="packet-1",
        approval_grant_id="grant-1",
        base_repository_revision="a" * 40,
        actor="github-copilot",
    )

    assert result == bound
    assert repository._append_event.call_args.kwargs["event_type"] == "scope_bound"  # type: ignore[attr-defined]


def test_view_renders_commands_results_and_events(tmp_path: Path) -> None:
    cursor = Mock()
    now = datetime.now(UTC)
    cursor.fetchall.side_effect = [
        [("accepted", "developer", {"state": "accepted"}, now)],
        [("command-1", "compile", "passed", 0, "b" * 64, "c" * 64, now)],
        [
            (
                "result-1",
                "committed",
                "in_scope",
                ["src/a.py"],
                [],
                ["command-1"],
                True,
                "verified",
                "b" * 40,
                "passed",
                now,
            )
        ],
    ]
    repository = _repository(cursor)
    record = _record(tmp_path)
    repository.get = Mock(return_value=record)  # type: ignore[method-assign]
    repository._artifacts.get.return_value = {
        "artifact_type": "CopilotCodingTask",
        "coding_task_id": "task-1",
    }

    result = repository.view("task-1")

    assert result["execution_scope"]["bound"] is True  # type: ignore[index]
    assert result["commands"][0]["status"] == "passed"  # type: ignore[index]
    assert result["edit_results"][0]["changed_paths"] == ["src/a.py"]  # type: ignore[index]
    assert result["events"][0]["event_type"] == "accepted"  # type: ignore[index]


def test_low_level_record_and_event_guards(tmp_path: Path) -> None:
    cursor = Mock()
    expiry = datetime.now(UTC) + timedelta(seconds=CLAIM_LEASE_SECONDS)
    row = (
        "task-1",
        "project-1",
        "change-1",
        "case-1",
        "repository-1",
        "packet-1",
        "grant-1",
        "a" * 40,
        "copilot_change_task",
        "local_bridge",
        "github-copilot",
        str(tmp_path),
        "in_progress",
        "vscode-1",
        expiry,
        "developer",
        None,
        1,
        "compile_test",
    )
    cursor.fetchone.return_value = row

    record = CopilotCodingTaskRepository._get_locked(cursor, "task-1", lock=False)

    assert record is not None
    assert record.claim_expires_at == expiry
    assert "FOR UPDATE" not in cursor.execute.call_args.args[0]

    cursor.fetchone.return_value = (True,)
    CopilotCodingTaskRepository._require_current_lease(cursor, record, "vscode-1")
    cursor.fetchone.return_value = (True,)
    CopilotCodingTaskRepository._require_live_grant(cursor, record)

    payload = {"state": "in_progress", "labels": ["日本語"]}
    cursor.fetchone.return_value = ("heartbeat", "vscode-1", payload)
    CopilotCodingTaskRepository._append_event(
        cursor,
        record=record,
        event_type="heartbeat",
        actor="vscode-1",
        idempotency_key="heartbeat-1",
        payload=payload,
    )


def test_low_level_guards_fail_closed(tmp_path: Path) -> None:
    cursor = Mock()
    cursor.fetchone.return_value = None
    with pytest.raises(ValueError, match="does not exist"):
        CopilotCodingTaskRepository._require_locked(cursor, "missing")
    with pytest.raises(RuntimeError, match="payload digest disappeared"):
        CopilotCodingTaskRepository._payload_digest(cursor, "missing")

    record = _record(tmp_path)
    cursor.fetchone.return_value = (False,)
    with pytest.raises(ValueError, match="lease is not held"):
        CopilotCodingTaskRepository._require_current_lease(cursor, record, "vscode-2")

    incomplete = replace(record, approval_grant_id=None)
    with pytest.raises(PersistenceConflictError, match="scope is incomplete"):
        CopilotCodingTaskRepository._require_live_grant(cursor, incomplete)

    cursor.fetchone.return_value = None
    with pytest.raises(ValueError, match="no longer active"):
        CopilotCodingTaskRepository._require_live_grant(cursor, record)

    cursor.fetchone.return_value = ("different", "actor", {})
    with pytest.raises(PersistenceConflictError, match="payload differs"):
        CopilotCodingTaskRepository._append_event(
            cursor,
            record=record,
            event_type="event",
            actor="actor",
            idempotency_key="key",
            payload={"state": "expected"},
        )


@pytest.mark.parametrize(
    ("previous_consumer", "lease_expired", "expected_event"),
    [
        (None, False, "claimed"),
        ("old-vscode", True, "claim_recovered"),
    ],
)
def test_renew_claim_records_initial_and_recovery_events(
    tmp_path: Path,
    previous_consumer: str | None,
    lease_expired: bool,
    expected_event: str,
) -> None:
    cursor = Mock()
    record = _record(tmp_path)
    previous_expiry = datetime.now(UTC) - timedelta(seconds=1)

    with (
        patch.object(CopilotCodingTaskRepository, "_require_locked", return_value=record),
        patch.object(CopilotCodingTaskRepository, "_append_event") as append_event,
    ):
        CopilotCodingTaskRepository._renew_claim_locked(
            cursor,
            record=record,
            consumer_id="vscode-2",
            previous_consumer=previous_consumer,
            lease_expired=lease_expired,
            previous_expiry=previous_expiry,
        )

    assert cursor.execute.call_args.args[1] == (
        "vscode-2",
        "vscode-2",
        CLAIM_LEASE_SECONDS,
        "task-1",
    )
    assert append_event.call_args.kwargs["event_type"] == expected_event
