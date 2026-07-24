"""Project and case read routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse

from operamind.application.web_control_plane import WebControlPlaneService
from operamind.web.dependencies import command_actor, get_service, idempotency_key
from operamind.web.models import (
    ProfileActivationCreate,
    ProfileRebuildCreate,
    ProfileRebuildRequeue,
    UiKnowledgeReviewCreate,
)

router = APIRouter(prefix="/api/v1", tags=["projects"])
Service = Annotated[WebControlPlaneService, Depends(get_service)]
Actor = Annotated[str, Depends(command_actor)]
IdempotencyKey = Annotated[str, Depends(idempotency_key)]


@router.get("/projects")
def list_projects(service: Service) -> dict[str, object]:
    return service.list_projects()


@router.get("/projects/{project_id}/cases/{case_id}")
def get_case(project_id: str, case_id: str, service: Service) -> dict[str, object]:
    return service.case_detail(project_id=project_id, case_id=case_id)


@router.get("/projects/{project_id}/code-graphs/{snapshot_id}")
def get_code_graph(
    project_id: str,
    snapshot_id: str,
    service: Service,
    max_nodes: Annotated[int, Query(ge=1, le=500)] = 240,
    max_edges: Annotated[int, Query(ge=1, le=1000)] = 480,
) -> dict[str, object]:
    return service.code_graph_view(
        project_id=project_id,
        snapshot_id=snapshot_id,
        max_nodes=max_nodes,
        max_edges=max_edges,
    )


@router.get("/projects/{project_id}/profiles")
def get_profile_registry(project_id: str, service: Service) -> dict[str, object]:
    return service.profile_registry(project_id=project_id)


@router.post("/projects/{project_id}/profiles/activate")
def activate_profile(
    project_id: str,
    body: ProfileActivationCreate,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    return service.activate_profile(
        project_id=project_id,
        binding_key=body.binding_key,
        profile_version_id=body.profile_version_id,
        reason=body.reason,
        idempotency_key=key,
        actor=actor,
    )


@router.post("/projects/{project_id}/profiles/rebuild-requests", status_code=201)
def request_profile_rebuild(
    project_id: str,
    body: ProfileRebuildCreate,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    return service.request_profile_rebuild(
        project_id=project_id,
        drift_event_id=body.drift_event_id,
        artifact_type=body.artifact_type,
        artifact_id=body.artifact_id,
        idempotency_key=key,
        actor=actor,
    )


@router.post("/projects/{project_id}/profiles/rebuild-requests/{request_id}/requeue")
def requeue_profile_rebuild(
    project_id: str,
    request_id: str,
    body: ProfileRebuildRequeue,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    return service.requeue_profile_rebuild(
        project_id=project_id,
        rebuild_request_id=request_id,
        reason=body.reason,
        idempotency_key=key,
        actor=actor,
    )


@router.get("/projects/{project_id}/ui-knowledge/reviews")
def get_ui_knowledge_reviews(project_id: str, service: Service) -> dict[str, object]:
    return service.ui_knowledge_review_queue(project_id=project_id)


@router.get("/projects/{project_id}/unresolved-evidence")
def get_unresolved_evidence(
    project_id: str,
    service: Service,
    history_limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, object]:
    return service.unresolved_evidence_management(
        project_id=project_id,
        history_limit=history_limit,
    )


@router.post("/projects/{project_id}/ui-knowledge/reviews/{source_snapshot_id}")
def review_ui_knowledge(
    project_id: str,
    source_snapshot_id: str,
    body: UiKnowledgeReviewCreate,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    return service.review_ui_knowledge(
        project_id=project_id,
        source_snapshot_id=source_snapshot_id,
        result_snapshot_version=body.result_snapshot_version,
        decision=body.decision,
        reason=body.reason,
        activate=body.activate,
        idempotency_key=key,
        actor=actor,
    )


@router.get("/projects/{project_id}/ui-knowledge/reviews/{snapshot_id}/screenshots/{evidence_id}")
def get_ui_knowledge_review_screenshot(
    project_id: str,
    snapshot_id: str,
    evidence_id: str,
    service: Service,
) -> FileResponse:
    return FileResponse(
        service.ui_knowledge_screenshot_path(
            project_id=project_id,
            snapshot_id=snapshot_id,
            evidence_id=evidence_id,
        ),
        headers={"Cache-Control": "private, max-age=300"},
    )
