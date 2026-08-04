"""Token-protected loopback Bridge endpoints used by the VS Code extension."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from operamind.application.web_control_plane import WebControlPlaneService
from operamind.web.dependencies import get_service, local_bridge_auth
from operamind.web.models import (
    BridgeChangeCheckpointDecision,
    BridgeTaskAccept,
    BridgeTaskCancel,
)

router = APIRouter(
    prefix="/api/v1/local-bridge",
    tags=["local-bridge"],
    dependencies=[Depends(local_bridge_auth)],
)
Service = Annotated[WebControlPlaneService, Depends(get_service)]


@router.get("/confirmations/next")
def next_confirmation(
    workspace_root: Annotated[str, Query(min_length=1, max_length=4000)],
    service: Service,
    change_request_id: Annotated[str | None, Query(min_length=1, max_length=160)] = None,
) -> dict[str, object]:
    return service.next_change_confirmation(
        workspace_root=Path(workspace_root),
        change_request_id=change_request_id,
    )


@router.post("/change-requests/{request_id}/confirmations/{checkpoint}")
def decide_confirmation(
    request_id: str,
    checkpoint: str,
    body: BridgeChangeCheckpointDecision,
    service: Service,
) -> dict[str, object]:
    return service.execute_web_command(
        command_scope=f"change-confirmation:{request_id}:{checkpoint}",
        idempotency_key=body.idempotency_key,
        actor=body.actor,
        payload=body.model_dump(mode="json"),
        operation=lambda: service.decide_change_checkpoint(
            request_id=request_id,
            checkpoint=checkpoint,
            decision=body.decision,
            surface="vscode_copilot",
            actor=body.actor,
            idempotency_key=body.idempotency_key,
            note=body.note,
        ),
    )


@router.get("/tasks/next")
def claim_next_task(
    workspace_root: Annotated[str, Query(min_length=1, max_length=4000)],
    consumer_id: Annotated[str, Query(min_length=1, max_length=200)],
    service: Service,
    change_request_id: Annotated[str | None, Query(min_length=1, max_length=160)] = None,
) -> dict[str, object]:
    return service.claim_copilot_task(
        workspace_root=Path(workspace_root),
        consumer_id=consumer_id,
        change_request_id=change_request_id,
    )


@router.post("/tasks/{coding_task_id}/accept")
def accept_task(
    coding_task_id: str,
    body: BridgeTaskAccept,
    service: Service,
) -> dict[str, object]:
    return service.accept_copilot_task(
        coding_task_id=coding_task_id,
        workspace_root=Path(body.workspace_root),
        consumer_id=body.consumer_id,
        claim_token=body.claim_token,
        actor=body.accepted_by,
    )


@router.get("/tasks/{coding_task_id}/resume")
def resume_task(
    coding_task_id: str,
    workspace_root: Annotated[str, Query(min_length=1, max_length=4000)],
    consumer_id: Annotated[str, Query(min_length=1, max_length=200)],
    service: Service,
    claim_token: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
) -> dict[str, object]:
    return service.resume_copilot_task(
        coding_task_id=coding_task_id,
        workspace_root=Path(workspace_root),
        consumer_id=consumer_id,
        claim_token=claim_token,
    )


@router.post("/tasks/{coding_task_id}/cancel")
def cancel_task(
    coding_task_id: str,
    body: BridgeTaskCancel,
    service: Service,
) -> dict[str, object]:
    return service.cancel_copilot_task_from_bridge(
        coding_task_id=coding_task_id,
        workspace_root=Path(body.workspace_root),
        consumer_id=body.consumer_id,
        claim_token=body.claim_token,
        actor=body.cancelled_by,
        reason=body.reason,
    )
