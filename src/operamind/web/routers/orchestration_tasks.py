"""Agent-neutral task discovery, claim, lease, and result routes."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query

from operamind.application.web_control_plane import WebControlPlaneService
from operamind.web.dependencies import command_actor, get_service
from operamind.web.models import (
    OrchestrationTaskClaim,
    OrchestrationTaskLease,
    OrchestrationTaskPriorityUpdate,
    OrchestrationTaskRelease,
    OrchestrationTaskRequeue,
    OrchestrationTaskResult,
    OrchestrationWorkerConfigurationUpdate,
)

router = APIRouter(prefix="/api/v1/orchestration-tasks", tags=["orchestration-tasks"])
Service = Annotated[WebControlPlaneService, Depends(get_service)]
Actor = Annotated[str, Depends(command_actor)]
WorkerToken = Annotated[
    str | None,
    Header(alias="X-OperaMind-Worker-Token", min_length=1, max_length=500),
]
TaskState = Literal[
    "ready",
    "claimed",
    "running",
    "submitted",
    "completed",
    "failed",
    "blocked",
    "cancelled",
    "superseded",
]


@router.get("")
def list_tasks(run_id: Annotated[str, Query(min_length=1)], service: Service) -> dict[str, object]:
    return service.orchestration_tasks(run_id)


@router.get("/management")
def task_management(
    service: Service,
    project_id: Annotated[str | None, Query(min_length=1, max_length=160)] = None,
    state: Annotated[list[TaskState] | None, Query()] = None,
    capability: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    blocking_reason: Annotated[str | None, Query(min_length=1, max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict[str, object]:
    return service.orchestration_task_management(
        project_id=project_id,
        states=tuple(state or ()),
        capability=capability,
        blocking_reason=blocking_reason,
        limit=limit,
    )


@router.get("/graph")
def task_dependency_graph(
    service: Service,
    project_id: Annotated[str | None, Query(min_length=1, max_length=160)] = None,
    run_id: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> dict[str, object]:
    return service.orchestration_task_dependency_graph(
        project_id=project_id,
        automation_run_id=run_id,
        limit=limit,
    )


@router.get("/monitoring")
def task_runtime_monitoring(
    service: Service,
    project_id: Annotated[str | None, Query(min_length=1, max_length=160)] = None,
    window_hours: Annotated[int, Query(ge=1, le=2160)] = 24,
) -> dict[str, object]:
    return service.orchestration_task_runtime_monitoring(
        project_id=project_id,
        window_hours=window_hours,
    )


@router.get("/workers")
def list_workers(
    service: Service,
    project_id: Annotated[str | None, Query(min_length=1, max_length=160)] = None,
) -> dict[str, object]:
    return service.orchestration_workers(project_id=project_id)


@router.patch("/workers/{executor_kind}/{executor_id}")
def update_worker_configuration(
    executor_kind: Literal["agent", "subagent"],
    executor_id: str,
    body: OrchestrationWorkerConfigurationUpdate,
    service: Service,
    actor: Actor,
) -> dict[str, object]:
    return service.update_orchestration_worker_configuration(
        executor_kind=executor_kind,
        executor_id=executor_id,
        capabilities=tuple(body.capabilities),
        max_concurrent_tasks=body.max_concurrent_tasks,
        actor=actor,
    )


@router.post("/workers/{executor_kind}/{executor_id}/{operation}")
def operate_worker(
    executor_kind: Literal["agent", "subagent"],
    executor_id: str,
    operation: Literal["enable", "disable", "drain"],
    service: Service,
    actor: Actor,
) -> dict[str, object]:
    return service.operate_orchestration_worker(
        executor_kind=executor_kind,
        executor_id=executor_id,
        operation=operation,
        actor=actor,
    )


@router.get("/ready")
def list_ready_tasks(
    service: Service,
    executor_kind: Literal["agent", "subagent", "human"],
    capability: Annotated[list[str], Query(min_length=1)],
    project_id: Annotated[str | None, Query(min_length=1)] = None,
) -> dict[str, object]:
    return service.ready_orchestration_tasks(
        executor_kind=executor_kind,
        capabilities=tuple(capability),
        project_id=project_id,
    )


@router.get("/{task_id}")
def get_task(task_id: str, service: Service) -> dict[str, object]:
    return service.orchestration_task(task_id)


@router.post("/claim")
def claim_task(
    body: OrchestrationTaskClaim,
    service: Service,
    actor: Actor,
    worker_token: WorkerToken = None,
) -> dict[str, object]:
    return service.claim_orchestration_task(
        executor_kind=body.executor_kind,
        executor_id=actor,
        capabilities=tuple(body.capabilities),
        project_id=body.project_id,
        worker_token=worker_token,
    )


@router.post("/{task_id}/claim")
def claim_selected_task(
    task_id: str,
    body: OrchestrationTaskClaim,
    service: Service,
    actor: Actor,
    worker_token: WorkerToken = None,
) -> dict[str, object]:
    return service.claim_selected_orchestration_task(
        task_id=task_id,
        executor_kind=body.executor_kind,
        executor_id=actor,
        capabilities=tuple(body.capabilities),
        project_id=body.project_id,
        worker_token=worker_token,
    )


@router.post("/{task_id}/heartbeat")
def heartbeat_task(
    task_id: str, body: OrchestrationTaskLease, service: Service, actor: Actor
) -> dict[str, object]:
    return service.heartbeat_orchestration_task(
        task_id=task_id, executor_id=actor, lease_token=body.lease_token
    )


@router.post("/{task_id}/release")
def release_task(
    task_id: str, body: OrchestrationTaskRelease, service: Service, actor: Actor
) -> dict[str, object]:
    return service.release_orchestration_task(
        task_id=task_id,
        executor_id=actor,
        lease_token=body.lease_token,
        reason=body.reason,
    )


@router.post("/{task_id}/result")
def record_task_result(
    task_id: str, body: OrchestrationTaskResult, service: Service, actor: Actor
) -> dict[str, object]:
    return service.complete_orchestration_task(
        task_id=task_id,
        executor_id=actor,
        lease_token=body.lease_token,
        outcome=body.outcome,
        summary=body.summary,
        artifact_refs=tuple(body.artifact_refs),
        evidence=dict(body.evidence),
    )


@router.post("/{task_id}/requeue")
def requeue_task(
    task_id: str, body: OrchestrationTaskRequeue, service: Service, actor: Actor
) -> dict[str, object]:
    return service.requeue_orchestration_task(
        task_id=task_id, actor=actor, reason=body.reason
    )


@router.patch("/{task_id}/priority")
def update_task_priority(
    task_id: str,
    body: OrchestrationTaskPriorityUpdate,
    service: Service,
    actor: Actor,
) -> dict[str, object]:
    return service.update_orchestration_task_priority(
        task_id=task_id, priority=body.priority, actor=actor
    )
