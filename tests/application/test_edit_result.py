from __future__ import annotations

from pathlib import Path
from typing import Any

from operamind.application.edit_result import (
    EditResultRequest,
    EditResultService,
    EditValidationMode,
)
from operamind.infrastructure.code_graph.git import GitDiffEvidence, GitPathChange
from operamind.infrastructure.postgres.edit_result_repository import (
    EditResultPacketScope,
    EditResultRecord,
    EditResultWrite,
)


def test_working_source_change_also_gets_fail_closed_line_coverage(
    tmp_path: Path,
) -> None:
    repository = _Repository(tmp_path)
    service = object.__new__(EditResultService)
    service._repository = repository  # type: ignore[attr-defined]
    service._contracts = _Contracts()  # type: ignore[attr-defined]
    service._git = _Git(tmp_path)  # type: ignore[attr-defined]

    result = service.run(
        EditResultRequest(
            edit_result_id="edit-working-1",
            edit_packet_id="packet-1",
            approval_grant_id="grant-1",
            project_id="demo",
            analysis_case_id="case-1",
            workspace_root=tmp_path,
            mode=EditValidationMode.WORKING,
        )
    )

    assert result.changed_line_coverage["status"] == "missing"
    assert result.changed_line_coverage["changed_line_count"] == 1
    assert repository.write is not None
    assert repository.write.changed_line_coverage["status"] == "missing"


class _Repository:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.write: EditResultWrite | None = None

    def load_packet_scope(self, **_: object) -> EditResultPacketScope:
        return EditResultPacketScope(
            edit_packet_id="packet-1",
            approval_grant_id="grant-1",
            project_id="demo",
            analysis_case_id="case-1",
            repository_id="repository-1",
            base_repository_revision="base-sha",
            remote_url="https://example.invalid/repository.git",
            workspace_root=str(self.root),
            packet_status="active",
            writable_files=("src/service.py",),
            required_ui_scenario_refs=(),
        )

    def record(self, *, scope: EditResultPacketScope, write: EditResultWrite) -> EditResultRecord:
        assert scope.edit_packet_id == "packet-1"
        self.write = write
        return EditResultRecord(
            created=True,
            edit_result_id=write.edit_result_id,
            status=write.status,
            case_status="editing",
            command_evidence_status="not_applicable",
        )


class _Contracts:
    def validate_artifact(self, artifact: dict[str, Any]) -> None:
        assert artifact["artifact_type"] == "ChangedLineCoverageReport"


class _Git:
    def __init__(self, root: Path) -> None:
        self.root = root

    def common_repository_dir(self, _: Path) -> Path:
        return self.root

    def inspect_worktree(self, _: Path, *, base_sha: str) -> GitDiffEvidence:
        assert base_sha == "base-sha"
        return GitDiffEvidence(
            workspace_root=self.root,
            base_sha="base-sha",
            result_sha=None,
            remote_url="https://example.invalid/repository.git",
            changes=(GitPathChange("M", ("src/service.py",)),),
            changed_lines=(("src/service.py", (12,)),),
        )
