from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from operamind.infrastructure.postgres.copilot_coding_task_repository import (
    CopilotCodingTaskRepository,
)


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
