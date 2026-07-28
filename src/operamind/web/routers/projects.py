"""Read-only project selection for the single-flow Web application."""

from typing import Annotated

from fastapi import APIRouter, Depends

from operamind.application.web_control_plane import WebControlPlaneService
from operamind.web.dependencies import get_service

router = APIRouter(prefix="/api/v1", tags=["projects"])
Service = Annotated[WebControlPlaneService, Depends(get_service)]


@router.get("/projects")
def list_projects(service: Service) -> dict[str, object]:
    result = service.list_projects()
    raw_projects = result.get("projects")
    projects = raw_projects if isinstance(raw_projects, list | tuple) else []
    public_projects = [
        {
            "project_id": item.get("project_id"),
            "name": item.get("name"),
        }
        for item in projects
        if isinstance(item, dict) and isinstance(item.get("project_id"), str)
    ]
    return {"projects": public_projects, "count": len(public_projects)}
