from __future__ import annotations

import threading
from collections.abc import Mapping

from operamind.application.orchestration_worker import (
    OrchestrationTaskExecutionCancelled,
    OrchestrationTaskExecutionContext,
    OrchestrationTaskExecutionResult,
    OrchestrationTaskWorker,
    OrchestrationWorkerConfiguration,
)


class FakeQueue:
    def __init__(self) -> None:
        self.tasks = [
            _task("task-unhandled", "manual_review"),
            _task("task-supported", "generate_plan"),
        ]
        self.list_calls: list[dict[str, object]] = []
        self.claimed: list[str] = []
        self.heartbeats: list[tuple[str, str]] = []
        self.results: list[dict[str, object]] = []
        self.releases: list[dict[str, str]] = []

    def list_ready(
        self,
        *,
        executor_kind: str,
        capabilities: tuple[str, ...],
        project_id: str | None = None,
    ) -> list[dict[str, object]]:
        self.list_calls.append(
            {
                "executor_kind": executor_kind,
                "capabilities": capabilities,
                "project_id": project_id,
            }
        )
        return list(self.tasks)

    def claim(
        self,
        *,
        task_id: str,
        executor_kind: str,
        executor_id: str,
        capabilities: tuple[str, ...],
        worker_token: str,
        project_id: str | None = None,
    ) -> dict[str, object]:
        self.claimed.append(task_id)
        task = next(value for value in self.tasks if value["orchestration_task_id"] == task_id)
        return {**task, "lease_token": "token-1", "claims": []}

    def heartbeat(self, *, task_id: str, executor_id: str, lease_token: str) -> dict[str, object]:
        self.heartbeats.append((task_id, lease_token))
        return {"orchestration_task_id": task_id, "state": "running"}

    def release(
        self, *, task_id: str, executor_id: str, lease_token: str, reason: str
    ) -> dict[str, object]:
        self.releases.append({"task_id": task_id, "reason": reason})
        return {"orchestration_task_id": task_id, "state": "ready"}

    def record_result(
        self,
        *,
        task_id: str,
        executor_id: str,
        lease_token: str,
        outcome: str,
        summary: str,
        artifact_refs: tuple[str, ...],
        evidence: dict[str, object],
    ) -> dict[str, object]:
        self.results.append(
            {
                "task_id": task_id,
                "executor_id": executor_id,
                "lease_token": lease_token,
                "outcome": outcome,
                "summary": summary,
                "artifact_refs": artifact_refs,
                "evidence": evidence,
            }
        )
        return {"orchestration_task_id": task_id, "state": "submitted"}


class SuccessfulHandler:
    def __init__(self, *, duration: float = 0.0) -> None:
        self.duration = duration
        self.tasks: list[Mapping[str, object]] = []

    def execute(
        self,
        *,
        task: Mapping[str, object],
        context: OrchestrationTaskExecutionContext,
    ) -> OrchestrationTaskExecutionResult:
        self.tasks.append(task)
        if self.duration:
            context.wait(self.duration)
            context.raise_if_cancelled()
        return OrchestrationTaskExecutionResult(
            outcome="completed",
            summary="Canonical plan generated",
            artifact_refs=("plan-1",),
            evidence={"accepted": True},
        )


class LeaseAwareHandler:
    def execute(
        self,
        *,
        task: Mapping[str, object],
        context: OrchestrationTaskExecutionContext,
    ) -> OrchestrationTaskExecutionResult:
        while not context.wait(0.005):
            pass
        raise OrchestrationTaskExecutionCancelled("lease lost")


class LeaseRecoveryQueue(FakeQueue):
    def __init__(self) -> None:
        super().__init__()
        self.tasks = [_task("task-recovered", "generate_plan")]
        self.claim_count = 0
        self.token_heartbeats: dict[str, int] = {}

    def claim(
        self,
        *,
        task_id: str,
        executor_kind: str,
        executor_id: str,
        capabilities: tuple[str, ...],
        worker_token: str,
        project_id: str | None = None,
    ) -> dict[str, object]:
        self.claim_count += 1
        token = f"token-{self.claim_count}"
        self.claimed.append(task_id)
        return {
            **self.tasks[0],
            "lease_token": token,
            "claims": [{"status": "expired", "executor_id": "previous-worker"}],
        }

    def heartbeat(self, *, task_id: str, executor_id: str, lease_token: str) -> dict[str, object]:
        count = self.token_heartbeats.get(lease_token, 0) + 1
        self.token_heartbeats[lease_token] = count
        self.heartbeats.append((task_id, lease_token))
        if lease_token == "token-1" and count >= 2:
            raise ValueError("Orchestration Task lease is no longer active")
        return {"orchestration_task_id": task_id, "state": "running"}


class CanonicalRejectionQueue(FakeQueue):
    def record_result(self, **values: object) -> dict[str, object]:
        self.results.append(dict(values))
        return {"orchestration_task_id": values["task_id"], "status": "blocked"}


def test_worker_claims_only_supported_capability_task_and_maintains_lease() -> None:
    queue = FakeQueue()
    handler = SuccessfulHandler(duration=0.04)
    worker = OrchestrationTaskWorker(
        queue=queue,
        handlers={"generate_plan": handler},
        configuration=_configuration(),
    )

    iteration = worker.run_once()

    assert iteration.to_dict() == {
        "status": "submitted",
        "task_id": "task-supported",
        "action": "generate_plan",
        "outcome": "completed",
        "recovered_expired_lease": False,
    }
    assert queue.list_calls == [
        {
            "executor_kind": "agent",
            "capabilities": ("change_planning", "state_observation"),
            "project_id": "project-1",
        }
    ]
    assert queue.claimed == ["task-supported"]
    assert len(queue.heartbeats) >= 3
    assert queue.results[0]["artifact_refs"] == ("plan-1",)
    assert "lease_token" not in handler.tasks[0]


def test_worker_reports_persisted_canonical_rejection() -> None:
    worker = OrchestrationTaskWorker(
        queue=CanonicalRejectionQueue(),
        handlers={"generate_plan": SuccessfulHandler()},
        configuration=_configuration(),
    )

    iteration = worker.run_once()

    assert iteration.outcome == "blocked"


def test_worker_does_not_submit_stale_result_and_next_worker_recovers() -> None:
    queue = LeaseRecoveryQueue()
    first = OrchestrationTaskWorker(
        queue=queue,
        handlers={"generate_plan": LeaseAwareHandler()},
        configuration=_configuration(),
    )

    lost = first.run_once()

    assert lost.status == "lease_lost"
    assert lost.recovered_expired_lease is True
    assert queue.results == []

    second = OrchestrationTaskWorker(
        queue=queue,
        handlers={"generate_plan": SuccessfulHandler()},
        configuration=_configuration(),
    )
    recovered = second.run_once()

    assert recovered.status == "submitted"
    assert recovered.recovered_expired_lease is True
    assert queue.claim_count == 2
    assert [result["lease_token"] for result in queue.results] == ["token-2"]


def test_worker_releases_live_lease_during_shutdown() -> None:
    queue = FakeQueue()
    stop = threading.Event()

    class StopHandler:
        def execute(
            self,
            *,
            task: Mapping[str, object],
            context: OrchestrationTaskExecutionContext,
        ) -> OrchestrationTaskExecutionResult:
            stop.set()
            raise OrchestrationTaskExecutionCancelled("worker stopping")

    worker = OrchestrationTaskWorker(
        queue=queue,
        handlers={"generate_plan": StopHandler()},
        configuration=_configuration(),
        stop_event=stop,
    )

    iteration = worker.run_once()

    assert iteration.status == "released"
    assert queue.results == []
    assert queue.releases == [
        {
            "task_id": "task-supported",
            "reason": "worker shutdown before task completion",
        }
    ]


def test_worker_records_bounded_failure_without_exception_text() -> None:
    queue = FakeQueue()

    class FailingHandler:
        def execute(
            self,
            *,
            task: Mapping[str, object],
            context: OrchestrationTaskExecutionContext,
        ) -> OrchestrationTaskExecutionResult:
            raise RuntimeError("password=must-not-be-persisted")

    worker = OrchestrationTaskWorker(
        queue=queue,
        handlers={"generate_plan": FailingHandler()},
        configuration=_configuration(),
    )

    iteration = worker.run_once()

    assert iteration.outcome == "failed"
    assert queue.results[0]["summary"] == "Task handler failed with RuntimeError"
    assert queue.results[0]["evidence"] == {"error_kind": "handler_exception"}
    assert "password" not in str(queue.results[0])


def _configuration() -> OrchestrationWorkerConfiguration:
    return OrchestrationWorkerConfiguration(
        executor_kind="agent",
        executor_id="worker-1",
        capabilities=("change_planning", "state_observation"),
        worker_token="worker-test-token",
        project_id="project-1",
        heartbeat_interval_seconds=0.01,
        idle_poll_seconds=0.01,
    )


def _task(task_id: str, action: str) -> dict[str, object]:
    return {
        "orchestration_task_id": task_id,
        "project_id": "project-1",
        "action": action,
        "required_capabilities": ["change_planning"],
        "lease_seconds": 30,
        "claims": [],
    }
