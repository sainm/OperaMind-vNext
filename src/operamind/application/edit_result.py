"""Validate path-only Git changes against an active Edit Packet allowlist."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from psycopg import Connection

from operamind.application.change_coverage import (
    ChangedLineCoverageEvidence,
    evaluate_changed_line_coverage,
)
from operamind.contracts import ContractCatalog
from operamind.infrastructure.code_graph import GitWorktreeDiffInspector
from operamind.infrastructure.postgres import (
    EditResultRecord,
    EditResultRepository,
    EditResultWrite,
)


class EditValidationMode(StrEnum):
    WORKING = "working"
    COMMITTED = "committed"


@dataclass(frozen=True, slots=True)
class EditResultRequest:
    edit_result_id: str
    edit_packet_id: str
    approval_grant_id: str
    project_id: str
    analysis_case_id: str
    workspace_root: Path
    mode: EditValidationMode
    test_result_refs: tuple[str, ...] = ()
    tests_passed: bool | None = None
    changed_line_coverage: ChangedLineCoverageEvidence | None = None

    def __post_init__(self) -> None:
        required = (
            self.edit_result_id,
            self.edit_packet_id,
            self.approval_grant_id,
            self.project_id,
            self.analysis_case_id,
        )
        if any(not value.strip() for value in required):
            raise ValueError("Edit Result request fields must not be blank")
        if len(self.test_result_refs) != len(set(self.test_result_refs)) or any(
            not value.strip() for value in self.test_result_refs
        ):
            raise ValueError("Edit Result test refs must be unique and non-blank")
        if self.mode is EditValidationMode.WORKING:
            if (
                self.tests_passed is not None
                or self.test_result_refs
                or self.changed_line_coverage is not None
            ):
                raise ValueError("Working validation cannot claim test results")
        elif self.tests_passed is None or not self.test_result_refs:
            raise ValueError("Committed Edit Result requires test outcome evidence")
        if self.changed_line_coverage is not None and not set(
            self.changed_line_coverage.evidence_refs
        ).issubset(self.test_result_refs):
            raise ValueError(
                "Changed-line coverage evidence must reference approved test command results"
            )


@dataclass(frozen=True, slots=True)
class EditResultServiceResult:
    record: EditResultRecord
    changed_paths: tuple[str, ...]
    out_of_scope_files: tuple[str, ...]
    result_repository_revision: str | None
    changed_line_coverage: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "edit_result_id": self.record.edit_result_id,
            "created": self.record.created,
            "status": self.record.status,
            "case_status": self.record.case_status,
            "command_evidence_status": self.record.command_evidence_status,
            "changed_paths": list(self.changed_paths),
            "out_of_scope_files": list(self.out_of_scope_files),
            "result_repository_revision": self.result_repository_revision,
            "changed_line_coverage": self.changed_line_coverage,
        }


class EditResultService:
    """Compare actual Git paths to Packet writable files and persist only evidence."""

    def __init__(self, *, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._repository = EditResultRepository(connection, contracts)
        self._contracts = contracts
        self._git = GitWorktreeDiffInspector()

    def run(self, request: EditResultRequest) -> EditResultServiceResult:
        scope = self._repository.load_packet_scope(
            project_id=request.project_id,
            analysis_case_id=request.analysis_case_id,
            edit_packet_id=request.edit_packet_id,
            approval_grant_id=request.approval_grant_id,
        )
        registered_root = Path(scope.workspace_root).resolve(strict=True)
        requested_root = request.workspace_root.resolve(strict=True)
        if self._git.common_repository_dir(registered_root) != self._git.common_repository_dir(
            requested_root
        ):
            raise ValueError("Edit Result Workspace is not linked to the registered Repository")
        evidence = (
            self._git.inspect_worktree(
                requested_root,
                base_sha=scope.base_repository_revision,
            )
            if request.mode is EditValidationMode.WORKING
            else self._git.inspect_committed(
                requested_root,
                base_sha=scope.base_repository_revision,
                allow_unchanged_head=not scope.writable_files,
            )
        )
        if evidence.remote_url != scope.remote_url:
            raise ValueError("Edit Result Workspace origin does not match Repository registration")
        out_of_scope = tuple(sorted(set(evidence.changed_paths) - set(scope.writable_files)))
        status = (
            "out_of_scope"
            if out_of_scope
            else "in_scope"
            if evidence.changed_paths
            else "no_changes"
        )
        coverage = evaluate_changed_line_coverage(
            edit_result_id=request.edit_result_id,
            project_id=request.project_id,
            base_repository_revision=scope.base_repository_revision,
            result_repository_revision=evidence.result_sha or scope.base_repository_revision,
            changed_lines=evidence.changed_lines,
            changed_paths=evidence.changed_paths,
            evidence=request.changed_line_coverage,
        )
        self._contracts.validate_artifact(coverage)
        write = EditResultWrite(
            edit_result_id=request.edit_result_id,
            validation_mode=request.mode.value,
            status=status,
            result_repository_revision=evidence.result_sha,
            path_changes=tuple((change.status, change.paths) for change in evidence.changes),
            changed_paths=evidence.changed_paths,
            out_of_scope_files=out_of_scope,
            test_result_refs=request.test_result_refs,
            tests_passed=request.tests_passed,
            changed_line_coverage=coverage,
        )
        return EditResultServiceResult(
            record=self._repository.record(scope=scope, write=write),
            changed_paths=evidence.changed_paths,
            out_of_scope_files=out_of_scope,
            result_repository_revision=evidence.result_sha,
            changed_line_coverage=coverage,
        )
