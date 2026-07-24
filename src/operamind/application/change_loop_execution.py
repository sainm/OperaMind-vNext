"""Fail-closed compatibility boundary for the retired monolithic P6 executor."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from operamind.application.change_loop import ChangeLoopBlockedError, ChangeLoopPlan
from operamind.contracts import ContractCatalog
from operamind.infrastructure.code_graph import SafeWorkspaceEditor, WorkspaceEditResult


@dataclass(frozen=True, slots=True)
class ChangeLoopExecutionRequest:
    """Legacy request retained only so old callers receive a fail-closed migration error."""

    output_root: Path
    browser_channel: str | None = "msedge"
    headless: bool = True
    application_port: int | None = None
    command_timeout_seconds: int = 240
    startup_timeout_seconds: int = 120
    edit_origin: str = "github_copilot_vscode"

    def __post_init__(self) -> None:
        if self.edit_origin != "github_copilot_vscode":
            raise ValueError("P6 execution only accepts VS Code GitHub Copilot edits")


@dataclass(frozen=True, slots=True)
class CanonicalExecutionBinding:
    """Immutable identities revalidated from the Canonical control plane."""

    project_id: str
    analysis_case_id: str
    context_package_id: str
    code_graph_snapshot_id: str
    impact_report_id: str
    confirmation_id: str
    edit_packet_id: str
    approval_grant_id: str
    base_revision: str

    def __post_init__(self) -> None:
        identities = (
            self.project_id,
            self.analysis_case_id,
            self.context_package_id,
            self.code_graph_snapshot_id,
            self.impact_report_id,
            self.confirmation_id,
            self.edit_packet_id,
            self.approval_grant_id,
        )
        if any(not value.strip() for value in identities):
            raise ValueError("Canonical execution identities must not be blank")
        if re.fullmatch(r"[0-9a-f]{40}", self.base_revision) is None:
            raise ValueError("Canonical execution requires a full base revision SHA")


class CanonicalExecutionAuthorizer(Protocol):
    """Re-read and authorize the complete RAG/Impact/Grant chain."""

    def authorize(self, *, plan: ChangeLoopPlan) -> CanonicalExecutionBinding: ...


@dataclass(frozen=True, slots=True)
class ChangeLoopExecutionResult:
    """Legacy result type; no new monolithic result can be produced."""

    edit_result: WorkspaceEditResult
    closure_result: dict[str, Any]
    artifact_paths: tuple[Path, ...]
    closure_path: Path

    @property
    def successful(self) -> bool:
        return bool(self.closure_result["status"] == "passed")


class ChangeLoopExecutor:
    """Reject the retired direct runtime and point callers to Canonical stage services."""

    def __init__(
        self,
        *,
        repository_root: Path,
        contracts: ContractCatalog | None = None,
        editor: SafeWorkspaceEditor | None = None,
        canonical_authorizer: CanonicalExecutionAuthorizer | None = None,
    ) -> None:
        repository_root.resolve(strict=True)
        self._authorizer = canonical_authorizer

    def execute(
        self,
        plan: ChangeLoopPlan,
        request: ChangeLoopExecutionRequest,
    ) -> ChangeLoopExecutionResult:
        if self._authorizer is None:
            raise ChangeLoopBlockedError(
                "P6 execution requires Canonical RAG/Impact/Grant authorization"
            )
        self._authorizer.authorize(plan=plan)
        raise ChangeLoopBlockedError(
            "Monolithic P6 runtime is retired: use Grant-bound approved commands, "
            "committed Edit Result, Deployment-bound UI Plan, and operamind-ui"
        )
