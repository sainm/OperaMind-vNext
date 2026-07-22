"""Repository readiness route."""

from typing import Annotated

from fastapi import APIRouter, Depends

from operamind.application.web_control_plane import WebControlPlaneService
from operamind.web.dependencies import get_service

router = APIRouter(prefix="/api/v1", tags=["readiness"])
Service = Annotated[WebControlPlaneService, Depends(get_service)]


@router.get("/readiness")
def get_readiness(service: Service) -> dict[str, object]:
    return service.readiness()
