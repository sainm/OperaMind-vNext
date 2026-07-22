"""Scope read-only Control Plane queries to exact local Git and Project identities."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from psycopg import Connection

from operamind.contracts import ContractCatalog
from operamind.infrastructure.code_graph import GitWorkspaceInspector
from operamind.infrastructure.postgres import ControlPlaneQueryRepository


class ControlPlaneQueryService:
    def __init__(self, *, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._repository = ControlPlaneQueryRepository(connection, contracts)
        self._git = GitWorkspaceInspector()

    def list_ready_cases(self, *, workspace_root: Path, limit: int = 20) -> dict[str, object]:
        if not 1 <= limit <= 50:
            raise ValueError("Ready Case limit must be between 1 and 50")
        evidence = self._git.inspect(workspace_root)
        linked_roots = self._git.linked_worktree_roots(evidence.workspace_root)
        cases = self._repository.list_ready_cases(
            workspace_roots=tuple(str(value) for value in linked_roots),
            remote_url=evidence.remote_url,
            head_revision=evidence.head_sha,
            limit=limit,
        )
        return {
            "workspace_root": str(evidence.workspace_root),
            "remote_url": evidence.remote_url,
            "head_revision": evidence.head_sha,
            "cases": list(cases),
        }

    def get_impact_report(
        self, *, project_id: str, analysis_case_id: str, impact_report_id: str
    ) -> dict[str, object]:
        return self._repository.get_impact_report(
            project_id=project_id,
            analysis_case_id=analysis_case_id,
            impact_report_id=impact_report_id,
        )

    def get_ui_plan(self, *, project_id: str, plan_id: str) -> dict[str, object]:
        return self._repository.get_ui_plan(project_id=project_id, plan_id=plan_id)

    def get_validation_result(
        self, *, project_id: str, verification_result_id: str
    ) -> dict[str, object]:
        return self._repository.get_validation_result(
            project_id=project_id,
            verification_result_id=verification_result_id,
        )
