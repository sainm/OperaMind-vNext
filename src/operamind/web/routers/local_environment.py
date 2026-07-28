"""Sanitized local-environment diagnostics for Web and the loopback Bridge."""

from typing import cast

from fastapi import APIRouter, Depends, Request

from operamind.application.local_environment_diagnostics import (
    ExtensionDiagnosticReport,
    LocalEnvironmentDiagnosticsService,
)
from operamind.web.dependencies import local_bridge_auth
from operamind.web.models import LocalEnvironmentExtensionDiagnostic

bridge_router = APIRouter(
    prefix="/api/v1/local-bridge",
    tags=["local-bridge"],
    dependencies=[Depends(local_bridge_auth)],
)


@bridge_router.post("/diagnostics")
def record_extension_diagnostics(
    body: LocalEnvironmentExtensionDiagnostic,
    request: Request,
) -> dict[str, object]:
    report = ExtensionDiagnosticReport(
        consumer_id=body.consumer_id,
        observed_at=body.observed_at,
        workspace_fingerprint=body.workspace_fingerprint,
        vsix_version=body.vsix_version,
        bridge_url_loopback=body.bridge_url_loopback,
        bridge_token_configured=body.bridge_token_configured,
        workspace_trusted=body.workspace_trusted,
        linked_worktree=body.linked_worktree,
        mcp_tool_names=tuple(body.mcp_tool_names),
        copilot_extension_installed=body.copilot_extension_installed,
        copilot_extension_active=body.copilot_extension_active,
        copilot_extension_version=body.copilot_extension_version,
        copilot_model_api_available=body.copilot_model_api_available,
        copilot_model_count=body.copilot_model_count,
    )
    service = cast(
        LocalEnvironmentDiagnosticsService,
        request.app.state.local_environment_diagnostics,
    )
    return service.record_extension_report(report)
