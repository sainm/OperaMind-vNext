from __future__ import annotations

from typing import Any

import pytest

from operamind.infrastructure.postgres.profile_rebuild_repository import (
    ProfileRebuildTaskQueue,
)


@pytest.fixture
def queue() -> ProfileRebuildTaskQueue:
    return ProfileRebuildTaskQueue(object())  # type: ignore[arg-type]


@pytest.mark.parametrize("reason", ["", " " * 2, "x" * 10_001])
def test_release_rejects_unbounded_reason(
    queue: ProfileRebuildTaskQueue, reason: str
) -> None:
    with pytest.raises(ValueError, match="release reason"):
        queue.release(
            task_id="rebuild-1",
            executor_id="worker-1",
            lease_token="lease-token",
            reason=reason,
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"outcome": "unknown"}, "outcome"),
        ({"summary": ""}, "summary"),
        ({"artifact_refs": ("artifact-1", "artifact-2")}, "one bounded"),
        ({"artifact_refs": ("",)}, "one bounded"),
    ],
)
def test_record_result_rejects_invalid_values(
    queue: ProfileRebuildTaskQueue,
    overrides: dict[str, Any],
    message: str,
) -> None:
    values: dict[str, Any] = {
        "task_id": "rebuild-1",
        "executor_id": "worker-1",
        "lease_token": "lease-token",
        "outcome": "failed",
        "summary": "generation failed",
        "artifact_refs": (),
        "evidence": {"error_kind": "generation_failed"},
    }
    values.update(overrides)

    with pytest.raises(ValueError, match=message):
        queue.record_result(**values)


def test_requeue_requires_actor_and_reason(queue: ProfileRebuildTaskQueue) -> None:
    with pytest.raises(ValueError, match="actor and reason"):
        queue.requeue(
            task_id="rebuild-1",
            project_id="project-1",
            actor="",
            reason="retry",
        )
