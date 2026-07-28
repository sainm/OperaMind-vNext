from typing import Any, cast

import pytest

from operamind.infrastructure.postgres import OrchestrationTaskRepository


def _repository() -> OrchestrationTaskRepository:
    return OrchestrationTaskRepository(cast(Any, object()))


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (
            {
                "executor_kind": "human",
                "executor_id": "reviewer",
                "capabilities": ("review",),
                "project_id": None,
            },
            "human executors are not registered",
        ),
        (
            {
                "executor_kind": "agent",
                "executor_id": "worker",
                "capabilities": ("review",),
                "project_id": " ",
            },
            "project_id must be non-blank",
        ),
        (
            {
                "executor_kind": "agent",
                "executor_id": "worker",
                "capabilities": ("review",),
                "project_id": None,
                "max_concurrent_tasks": 0,
            },
            "max_concurrent_tasks must be between",
        ),
        (
            {
                "executor_kind": "agent",
                "executor_id": "worker",
                "capabilities": ("review",),
                "project_id": None,
                "lease_seconds": 1,
            },
            "lease_seconds must be between",
        ),
    ],
)
def test_worker_registration_rejects_invalid_scheduling_inputs(
    values: dict[str, object],
    expected: str,
) -> None:
    with pytest.raises(ValueError, match=expected):
        _repository().register_worker(**values)  # type: ignore[arg-type]


def test_worker_and_monitoring_commands_validate_bounds_before_database_access() -> None:
    repository = _repository()

    with pytest.raises(ValueError, match="lease_seconds must be between"):
        repository.heartbeat_worker(
            executor_kind="agent",
            executor_id="worker",
            worker_token="token",
            lease_seconds=1,
        )
    with pytest.raises(ValueError, match="status must be online"):
        repository.set_worker_status(
            executor_kind="agent",
            executor_id="worker",
            status="paused",
            actor="operator",
        )
    with pytest.raises(ValueError, match="max_concurrent_tasks must be between"):
        repository.update_worker_configuration(
            executor_kind="agent",
            executor_id="worker",
            capabilities=("review",),
            max_concurrent_tasks=0,
            actor="operator",
        )
    with pytest.raises(ValueError, match="event limit must be between"):
        repository.worker_events(
            executor_kind="agent",
            executor_id="worker",
            limit=0,
        )
    with pytest.raises(ValueError, match="window_hours must be between"):
        repository.runtime_monitoring(window_hours=0)
    with pytest.raises(ValueError, match="backlog_alert_threshold must be between"):
        repository.runtime_monitoring(backlog_alert_threshold=0)
    with pytest.raises(ValueError, match="queue_wait_alert_seconds must be between"):
        repository.runtime_monitoring(queue_wait_alert_seconds=1)
    with pytest.raises(ValueError, match="priority must be between"):
        repository.update_priority(task_id="task", priority=0, actor="operator")
