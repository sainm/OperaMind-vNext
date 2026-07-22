"""Capability-based worker lifecycle for agent-neutral orchestration tasks."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from operamind.application.orchestration_task import validate_orchestration_result_evidence

WorkerOutcome = Literal["completed", "failed", "blocked"]
WorkerIterationStatus = Literal["idle", "submitted", "released", "lease_lost"]


class OrchestrationTaskQueue(Protocol):
    """Persistence operations required by a worker without binding it to PostgreSQL."""

    def list_ready(
        self,
        *,
        executor_kind: str,
        capabilities: tuple[str, ...],
        project_id: str | None = None,
    ) -> list[dict[str, object]]: ...

    def claim(
        self,
        *,
        task_id: str,
        executor_kind: str,
        executor_id: str,
        capabilities: tuple[str, ...],
        worker_token: str,
        project_id: str | None = None,
    ) -> dict[str, object]: ...

    def heartbeat(
        self, *, task_id: str, executor_id: str, lease_token: str
    ) -> dict[str, object]: ...

    def release(
        self, *, task_id: str, executor_id: str, lease_token: str, reason: str
    ) -> dict[str, object]: ...

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
    ) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class OrchestrationWorkerConfiguration:
    executor_kind: Literal["agent", "subagent"]
    executor_id: str
    capabilities: tuple[str, ...]
    worker_token: str
    project_id: str | None = None
    heartbeat_interval_seconds: float = 10.0
    idle_poll_seconds: float = 2.0

    def __post_init__(self) -> None:
        if not self.executor_id.strip():
            raise ValueError("worker executor_id must not be blank")
        if not self.capabilities or any(not value.strip() for value in self.capabilities):
            raise ValueError("worker requires non-blank capabilities")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("worker capabilities must be unique")
        if not self.worker_token.strip() or len(self.worker_token) > 500:
            raise ValueError("worker token must be non-blank and bounded")
        if not 0.01 <= self.heartbeat_interval_seconds <= 3600:
            raise ValueError("worker heartbeat interval is out of bounds")
        if not 0.01 <= self.idle_poll_seconds <= 3600:
            raise ValueError("worker idle poll interval is out of bounds")


@dataclass(frozen=True, slots=True)
class OrchestrationTaskExecutionResult:
    outcome: WorkerOutcome
    summary: str
    artifact_refs: tuple[str, ...]
    evidence: dict[str, object]

    def __post_init__(self) -> None:
        if self.outcome not in {"completed", "failed", "blocked"}:
            raise ValueError("worker result outcome is invalid")
        if not self.summary.strip() or len(self.summary) > 10_000:
            raise ValueError("worker result summary must be non-blank and bounded")
        if any(not value.strip() or len(value) > 2_000 for value in self.artifact_refs):
            raise ValueError("worker artifact references must be non-blank and bounded")
        if len(set(self.artifact_refs)) != len(self.artifact_refs):
            raise ValueError("worker artifact references must be unique")
        if self.outcome == "completed" and not self.artifact_refs:
            raise ValueError("completed worker result requires artifact references")
        if self.outcome == "completed" and not self.evidence:
            raise ValueError("completed worker result requires acceptance evidence")
        validate_orchestration_result_evidence(self.evidence)


@dataclass(frozen=True, slots=True)
class OrchestrationWorkerIteration:
    status: WorkerIterationStatus
    task_id: str | None = None
    action: str | None = None
    outcome: WorkerOutcome | None = None
    recovered_expired_lease: bool = False
    detail: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            key: value
            for key, value in {
                "status": self.status,
                "task_id": self.task_id,
                "action": self.action,
                "outcome": self.outcome,
                "recovered_expired_lease": self.recovered_expired_lease,
                "detail": self.detail,
            }.items()
            if value is not None
        }


@dataclass(frozen=True, slots=True)
class OrchestrationTaskExecutionContext:
    """Cooperative cancellation boundary for lease loss and worker shutdown."""

    _lease_lost: threading.Event
    _stop_requested: threading.Event

    @property
    def cancelled(self) -> bool:
        return self._lease_lost.is_set() or self._stop_requested.is_set()

    @property
    def lease_lost(self) -> bool:
        return self._lease_lost.is_set()

    def wait(self, timeout: float) -> bool:
        """Wait for cancellation, returning True when execution must stop."""
        deadline = time.monotonic() + max(0.0, timeout)
        while not self.cancelled:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self._lease_lost.wait(min(0.05, remaining))
        return True

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise OrchestrationTaskExecutionCancelled("task execution was cancelled")


class OrchestrationTaskHandler(Protocol):
    def execute(
        self,
        *,
        task: Mapping[str, object],
        context: OrchestrationTaskExecutionContext,
    ) -> OrchestrationTaskExecutionResult: ...


class OrchestrationTaskExecutionError(RuntimeError):
    """A bounded, safe-to-persist handler failure."""

    def __init__(self, message: str, *, error_kind: str = "handler_failed") -> None:
        super().__init__(message)
        self.error_kind = error_kind


class OrchestrationTaskExecutionCancelled(RuntimeError):
    """Raised by a cooperative handler after lease loss or shutdown."""


class OrchestrationTaskWorker:
    """Claim supported tasks, maintain their lease, and append exactly one result."""

    def __init__(
        self,
        *,
        queue: OrchestrationTaskQueue,
        handlers: Mapping[str, OrchestrationTaskHandler],
        configuration: OrchestrationWorkerConfiguration,
        stop_event: threading.Event | None = None,
    ) -> None:
        if not handlers:
            raise ValueError("worker requires at least one action handler")
        if any(not action.strip() for action in handlers):
            raise ValueError("worker handler actions must not be blank")
        self._queue = queue
        self._handlers = dict(handlers)
        self._configuration = configuration
        self._stop_event = stop_event or threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run_forever(self, observer: Callable[[OrchestrationWorkerIteration], None]) -> None:
        """Poll until stopped, reporting both executed and idle iterations."""
        while not self._stop_event.is_set():
            iteration = self.run_once()
            observer(iteration)
            if iteration.status == "idle":
                self._stop_event.wait(self._configuration.idle_poll_seconds)

    def run_once(self) -> OrchestrationWorkerIteration:
        if self._stop_event.is_set():
            return OrchestrationWorkerIteration(status="idle", detail="worker is stopping")
        claimed = self._claim_supported_task()
        if claimed is None:
            return OrchestrationWorkerIteration(status="idle")
        task_id = _required_task_text(claimed, "orchestration_task_id")
        action = _required_task_text(claimed, "action")
        lease_token = _required_task_text(claimed, "lease_token")
        handler = self._handlers[action]
        claim_values = claimed.get("claims")
        claims = claim_values if isinstance(claim_values, list) else []
        recovered = any(
            isinstance(claim, dict) and claim.get("status") == "expired" for claim in claims
        )
        lease_lost = threading.Event()
        heartbeat_stop = threading.Event()
        heartbeat_error: list[Exception] = []
        context = OrchestrationTaskExecutionContext(lease_lost, self._stop_event)

        try:
            self._heartbeat(task_id=task_id, lease_token=lease_token)
        except Exception as error:
            return self._lease_lost_iteration(task_id, action, recovered, error)

        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            kwargs={
                "task_id": task_id,
                "lease_token": lease_token,
                "task": claimed,
                "stop": heartbeat_stop,
                "lease_lost": lease_lost,
                "errors": heartbeat_error,
            },
            name=f"orchestration-heartbeat-{task_id}",
            daemon=True,
        )
        heartbeat_thread.start()
        execution_result: OrchestrationTaskExecutionResult | None = None
        execution_error: Exception | None = None
        try:
            safe_task = {key: value for key, value in claimed.items() if key != "lease_token"}
            execution_result = handler.execute(task=safe_task, context=context)
        except Exception as error:
            execution_error = error
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join()

        if heartbeat_error:
            return self._lease_lost_iteration(
                task_id, action, recovered, heartbeat_error[0]
            )
        if self._stop_event.is_set():
            return self._release_for_shutdown(task_id, action, lease_token, recovered)
        if execution_error is not None:
            if isinstance(execution_error, OrchestrationTaskExecutionCancelled):
                if lease_lost.is_set():
                    return self._lease_lost_iteration(
                        task_id, action, recovered, execution_error
                    )
                return self._release_claim(
                    task_id,
                    action,
                    lease_token,
                    recovered,
                    "handler cancelled task execution",
                )
            execution_result = _failed_execution_result(execution_error)
        if execution_result is None:
            execution_result = _failed_execution_result(
                RuntimeError("handler returned without a result")
            )

        try:
            self._heartbeat(task_id=task_id, lease_token=lease_token)
            self._queue.record_result(
                task_id=task_id,
                executor_id=self._configuration.executor_id,
                lease_token=lease_token,
                outcome=execution_result.outcome,
                summary=execution_result.summary,
                artifact_refs=execution_result.artifact_refs,
                evidence=execution_result.evidence,
            )
        except Exception as error:
            return self._lease_lost_iteration(task_id, action, recovered, error)
        return OrchestrationWorkerIteration(
            status="submitted",
            task_id=task_id,
            action=action,
            outcome=execution_result.outcome,
            recovered_expired_lease=recovered,
        )

    def _claim_supported_task(self) -> dict[str, object] | None:
        ready = self._queue.list_ready(
            executor_kind=self._configuration.executor_kind,
            capabilities=self._configuration.capabilities,
            project_id=self._configuration.project_id,
        )
        for task in ready:
            action = task.get("action")
            task_id = task.get("orchestration_task_id")
            if not isinstance(action, str) or action not in self._handlers:
                continue
            if not isinstance(task_id, str) or not task_id:
                continue
            try:
                return self._queue.claim(
                    task_id=task_id,
                    executor_kind=self._configuration.executor_kind,
                    executor_id=self._configuration.executor_id,
                    capabilities=self._configuration.capabilities,
                    worker_token=self._configuration.worker_token,
                    project_id=self._configuration.project_id,
                )
            except ValueError as error:
                recoverable = (
                    "is not ready or executor capabilities do not match",
                    "is not accepting new Tasks",
                    "concurrency limit is exhausted",
                )
                if not any(value in str(error) for value in recoverable):
                    raise
        return None

    def _heartbeat_loop(
        self,
        *,
        task_id: str,
        lease_token: str,
        task: Mapping[str, object],
        stop: threading.Event,
        lease_lost: threading.Event,
        errors: list[Exception],
    ) -> None:
        interval = self._heartbeat_interval(task)
        while not stop.wait(interval):
            try:
                self._heartbeat(task_id=task_id, lease_token=lease_token)
            except Exception as error:
                errors.append(error)
                lease_lost.set()
                return

    def _heartbeat_interval(self, task: Mapping[str, object]) -> float:
        lease_seconds = task.get("lease_seconds")
        if isinstance(lease_seconds, (int, float)) and lease_seconds > 0:
            return min(
                self._configuration.heartbeat_interval_seconds,
                max(0.01, float(lease_seconds) / 3),
            )
        return self._configuration.heartbeat_interval_seconds

    def _heartbeat(self, *, task_id: str, lease_token: str) -> None:
        self._queue.heartbeat(
            task_id=task_id,
            executor_id=self._configuration.executor_id,
            lease_token=lease_token,
        )

    def _release_for_shutdown(
        self, task_id: str, action: str, lease_token: str, recovered: bool
    ) -> OrchestrationWorkerIteration:
        return self._release_claim(
            task_id,
            action,
            lease_token,
            recovered,
            "worker shutdown before task completion",
        )

    def _release_claim(
        self,
        task_id: str,
        action: str,
        lease_token: str,
        recovered: bool,
        reason: str,
    ) -> OrchestrationWorkerIteration:
        try:
            self._queue.release(
                task_id=task_id,
                executor_id=self._configuration.executor_id,
                lease_token=lease_token,
                reason=reason,
            )
        except Exception as error:
            return self._lease_lost_iteration(task_id, action, recovered, error)
        return OrchestrationWorkerIteration(
            status="released",
            task_id=task_id,
            action=action,
            recovered_expired_lease=recovered,
            detail=reason,
        )

    @staticmethod
    def _lease_lost_iteration(
        task_id: str, action: str, recovered: bool, error: Exception
    ) -> OrchestrationWorkerIteration:
        return OrchestrationWorkerIteration(
            status="lease_lost",
            task_id=task_id,
            action=action,
            recovered_expired_lease=recovered,
            detail=f"{type(error).__name__}: lease is no longer owned by this worker",
        )


def _required_task_text(task: Mapping[str, object], key: str) -> str:
    value = task.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"claimed task is missing {key}")
    return value


def _failed_execution_result(error: Exception) -> OrchestrationTaskExecutionResult:
    if isinstance(error, OrchestrationTaskExecutionError):
        summary = str(error).strip() or "Task handler failed"
        error_kind = error.error_kind
    else:
        summary = f"Task handler failed with {type(error).__name__}"
        error_kind = "handler_exception"
    return OrchestrationTaskExecutionResult(
        outcome="failed",
        summary=summary[:10_000],
        artifact_refs=(),
        evidence={"error_kind": error_kind},
    )
