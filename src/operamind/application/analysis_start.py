"""Start one Canonical Analysis Case from exact local Git evidence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from operamind.infrastructure.code_graph import GitWorkspaceInspector
from operamind.infrastructure.postgres import AnalysisRegistration, AnalysisRepository


@dataclass(frozen=True, slots=True)
class AnalysisStartRequest:
    project_id: str
    project_name: str
    repository_id: str
    repository_revision_id: str
    analysis_case_id: str
    workspace_root: Path
    expected_base_revision: str

    def __post_init__(self) -> None:
        values = (
            self.project_id,
            self.project_name,
            self.repository_id,
            self.repository_revision_id,
            self.analysis_case_id,
            self.expected_base_revision,
        )
        if any(not value.strip() for value in values):
            raise ValueError("Analysis start fields must not be blank")


class AnalysisStartService:
    def __init__(
        self,
        *,
        repository: AnalysisRepository,
        git: GitWorkspaceInspector | None = None,
    ) -> None:
        self._repository = repository
        self._git = git or GitWorkspaceInspector()

    def run(self, request: AnalysisStartRequest) -> AnalysisRegistration:
        evidence = self._git.inspect(request.workspace_root)
        if evidence.head_sha != request.expected_base_revision:
            raise ValueError("Analysis Base Revision does not match clean Git HEAD")
        return self._repository.start(
            project_id=request.project_id,
            project_name=request.project_name,
            repository_id=request.repository_id,
            remote_url=evidence.remote_url,
            workspace_root=str(evidence.workspace_root),
            repository_revision_id=request.repository_revision_id,
            commit_sha=evidence.head_sha,
            analysis_case_id=request.analysis_case_id,
        )
