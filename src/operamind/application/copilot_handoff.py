"""Build a bounded local Copilot handoff from one active Packet and Grant."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg import Connection

from operamind.contracts import ContractCatalog
from operamind.infrastructure.code_graph import GitWorktreeDiffInspector
from operamind.infrastructure.postgres import (
    ApprovalGrantRepository,
    ArtifactRepository,
    EditResultRepository,
)


@dataclass(frozen=True, slots=True)
class CopilotHandoffRequest:
    project_id: str
    analysis_case_id: str
    edit_packet_id: str
    approval_grant_id: str
    workspace_root: Path

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (
                self.project_id,
                self.analysis_case_id,
                self.edit_packet_id,
                self.approval_grant_id,
            )
        ):
            raise ValueError("Copilot handoff request fields must not be blank")


class CopilotHandoffService:
    """Return only the approved editing context after revalidating local Git identity."""

    def __init__(self, *, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._artifacts = ArtifactRepository(connection, contracts)
        self._grants = ApprovalGrantRepository(connection, contracts)
        self._edit_results = EditResultRepository(connection, contracts)
        self._git = GitWorktreeDiffInspector()

    def get(self, request: CopilotHandoffRequest) -> dict[str, object]:
        scope = self._edit_results.load_packet_scope(
            project_id=request.project_id,
            analysis_case_id=request.analysis_case_id,
            edit_packet_id=request.edit_packet_id,
            approval_grant_id=request.approval_grant_id,
        )
        grant = self._grants.authorize_edit(
            grant_id=request.approval_grant_id,
            project_id=request.project_id,
            analysis_case_id=request.analysis_case_id,
            edit_packet_id=request.edit_packet_id,
            required_action="read",
        )
        registered_root = Path(scope.workspace_root).resolve(strict=True)
        requested_root = request.workspace_root.resolve(strict=True)
        registered_common_dir = self._git.common_repository_dir(registered_root)
        requested_common_dir = self._git.common_repository_dir(requested_root)
        if registered_common_dir != requested_common_dir:
            raise ValueError(
                "Copilot handoff Workspace is not a linked worktree of the registered Repository"
            )
        evidence = self._git.inspect_worktree(
            requested_root,
            base_sha=scope.base_repository_revision,
        )
        if evidence.remote_url != scope.remote_url:
            raise ValueError(
                "Copilot handoff Workspace origin does not match Repository registration"
            )
        packet = self._artifacts.get(request.edit_packet_id)
        if packet is None or packet.get("artifact_type") != "CopilotEditPacket":
            raise RuntimeError("Active Edit Packet has no immutable CopilotEditPacket Artifact")
        if packet.get("project_id") != request.project_id:
            raise ValueError("CopilotEditPacket Artifact is outside requested Project scope")
        return {
            "edit_packet": packet,
            "approval": {
                "approval_grant_id": grant.grant_id,
                "state": grant.state,
                "expires_at": grant.expires_at.isoformat(),
                "allowed_actions": list(grant.allowed_actions),
                "command_profile_version_id": grant.command_profile_version_id,
                "allowed_test_command_refs": list(grant.allowed_test_command_refs),
                "allowed_ui_scenarios": list(grant.allowed_ui_scenarios),
            },
            "workspace": {
                "root": str(evidence.workspace_root),
                "registered_root": str(registered_root),
                "isolated_worktree": requested_root != registered_root,
                "remote_url": evidence.remote_url,
                "head_revision": evidence.base_sha,
                "changed_paths": list(evidence.changed_paths),
            },
            "context_package_available": False,
        }
