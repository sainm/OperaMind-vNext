"""Project initialization and selection for the single-flow Web application."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends

from operamind.application.web_control_plane import (
    ProjectInitializationInput,
    WebControlPlaneService,
)
from operamind.web.dependencies import command_actor, get_service, idempotency_key
from operamind.web.models import ProjectCreate

router = APIRouter(prefix="/api/v1", tags=["projects"])
Service = Annotated[WebControlPlaneService, Depends(get_service)]
Actor = Annotated[str, Depends(command_actor)]
IdempotencyKey = Annotated[str, Depends(idempotency_key)]


@router.post("/projects", status_code=201)
def create_project(
    body: ProjectCreate,
    service: Service,
    actor: Actor,
    key: IdempotencyKey,
) -> dict[str, object]:
    return service.execute_web_command(
        command_scope=f"project:initialize:{body.project_id}",
        idempotency_key=key,
        actor=actor,
        payload=body.model_dump(mode="json"),
        operation=lambda: service.initialize_project(
            ProjectInitializationInput(
                project_id=body.project_id,
                name=body.name,
                workspace_root=Path(body.workspace_root),
                document_roots=tuple(Path(root) for root in body.document_roots),
                configured_by=actor,
                test_base_url=body.test_base_url,
            )
        ),
    )


@router.get("/projects")
def list_projects(service: Service) -> dict[str, object]:
    result = service.list_projects()
    raw_projects = result.get("projects")
    projects = raw_projects if isinstance(raw_projects, list | tuple) else []
    public_projects = [
        {
            "project_id": item.get("project_id"),
            "name": item.get("name"),
            "workspace_root": item.get("workspace_root"),
            "document_roots": item.get("document_roots"),
            "source_control_kind": item.get("source_control_kind"),
            "source_git_baselines": item.get("source_git_baselines") or [],
            "test_base_url": item.get("test_base_url"),
            **(
                {"target_project": item["target_project"]}
                if isinstance(item.get("target_project"), dict)
                else {}
            ),
        }
        for item in projects
        if isinstance(item, dict) and isinstance(item.get("project_id"), str)
    ]
    return {"projects": public_projects, "count": len(public_projects)}
