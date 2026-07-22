"""Build an immutable Copilot Edit Packet after exact Workspace validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from pathspec import GitIgnoreSpec, PathSpec
from pathspec.pattern import Pattern
from psycopg import Connection

from operamind.contracts import ContractCatalog
from operamind.infrastructure.code_graph import GitWorkspaceInspector
from operamind.infrastructure.postgres import (
    EditPacketPublishResult,
    EditPacketRepository,
)


@dataclass(frozen=True, slots=True)
class EditPacketRequest:
    edit_packet_id: str
    project_id: str
    analysis_case_id: str
    impact_report_id: str
    confirmation_id: str
    workspace_root: Path
    forbidden_globs: tuple[str, ...]
    implementation_constraints: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def __post_init__(self) -> None:
        required = (
            self.edit_packet_id,
            self.project_id,
            self.analysis_case_id,
            self.impact_report_id,
            self.confirmation_id,
        )
        if any(not value.strip() for value in required):
            raise ValueError("Edit Packet request fields must not be blank")
        if not self.forbidden_globs or any(not value.strip() for value in self.forbidden_globs):
            raise ValueError("Edit Packet requires explicit non-blank forbidden globs")
        if len(self.forbidden_globs) != len(set(self.forbidden_globs)):
            raise ValueError("Edit Packet forbidden globs must be unique")
        constraint_ids = [item_id for item_id, _ in self.implementation_constraints]
        if len(constraint_ids) != len(set(constraint_ids)):
            raise ValueError("Edit Packet constraint Item IDs must be unique")


@dataclass(frozen=True, slots=True)
class EditPacketResult:
    artifact: dict[str, Any]
    publication: EditPacketPublishResult


class EditPacketService:
    """Derive all permissions from one confirmed report; callers cannot add files."""

    def __init__(self, *, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._contracts = contracts
        self._repository = EditPacketRepository(connection, contracts)
        self._git = GitWorkspaceInspector()

    def run(self, request: EditPacketRequest) -> EditPacketResult:
        source = self._repository.load_source(
            project_id=request.project_id,
            analysis_case_id=request.analysis_case_id,
            impact_report_id=request.impact_report_id,
            confirmation_id=request.confirmation_id,
        )
        registered_root = Path(source.workspace_root).resolve(strict=True)
        requested_root = request.workspace_root.resolve(strict=True)
        if requested_root != registered_root:
            raise ValueError("Edit Packet Workspace root does not match Repository registration")
        evidence = self._git.inspect(requested_root)
        if evidence.head_sha != source.commit_sha or evidence.remote_url != source.remote_url:
            raise ValueError("Edit Packet Workspace Git HEAD or origin does not match")
        approved = set(source.approved_item_ids)
        constraints = dict(request.implementation_constraints)
        unknown_constraints = sorted(set(constraints) - approved)
        if unknown_constraints:
            raise ValueError(
                f"Implementation constraints reference unapproved Items: {unknown_constraints}"
            )
        approved_items = tuple(item for item in source.items if item.impact_item_id in approved)
        actionable = tuple(
            item
            for item in approved_items
            if item.recommended_action in {"modify", "add", "delete"}
        )
        test_files = tuple(sorted({path for item in actionable for path in item.test_file_refs}))
        editable_files = tuple(
            sorted({item.target_path for item in actionable if item.target_path not in test_files})
        )
        read_only_files = tuple(
            sorted(
                {
                    item.target_path
                    for item in approved_items
                    if item.recommended_action == "review_only"
                }
            )
        )
        if not editable_files:
            raise ValueError("Confirmed Impact contains no approved editable file")
        all_paths = (*editable_files, *read_only_files, *test_files)
        if len(all_paths) != len(set(all_paths)):
            raise ValueError("Edit Packet file classifications must not overlap")
        for path in all_paths:
            _validate_relative_path(path)
        spec = _forbidden_spec(request.forbidden_globs)
        forbidden_matches = sorted(path for path in all_paths if spec.match_file(path))
        if forbidden_matches:
            raise ValueError(f"Edit Packet paths match forbidden globs: {forbidden_matches}")
        add_paths = {item.target_path for item in actionable if item.recommended_action == "add"}
        existing_add_paths = sorted(add_paths & evidence.tracked_paths)
        if existing_add_paths:
            raise ValueError(
                f"Edit Packet add paths already exist in the bound Revision: {existing_add_paths}"
            )
        missing_tracked = sorted(
            path
            for path in all_paths
            if path not in evidence.tracked_paths and path not in add_paths
        )
        if missing_tracked:
            raise ValueError(
                f"Edit Packet paths are absent from bound Git Revision: {missing_tracked}"
            )
        allowed_items = [
            {
                "impact_item_id": item.impact_item_id,
                "target_path": item.target_path,
                "target_symbols": list(item.target_symbols),
                "allowed_actions": [item.recommended_action],
                "business_summary": source.business_summary,
                "implementation_constraints": list(constraints.get(item.impact_item_id, ())),
            }
            for item in actionable
        ]
        artifact: dict[str, Any] = {
            "artifact_type": "CopilotEditPacket",
            "schema_version": "v1",
            "edit_packet_id": request.edit_packet_id,
            "impact_report_id": source.impact_report_id,
            "confirmation_id": source.confirmation_id,
            "project_id": source.project_id,
            "repository_id": source.repository_id,
            "base_repository_revision": source.commit_sha,
            "editable_files": list(editable_files),
            "read_only_files": list(read_only_files),
            "test_files": list(test_files),
            "forbidden_globs": list(request.forbidden_globs),
            "allowed_items": allowed_items,
            "required_ui_scenario_refs": list(source.required_ui_scenario_refs),
            "out_of_scope_policy": "stop_and_reanalyze",
            "must_not_fetch_context_package": True,
        }
        self._contracts.validate_artifact(artifact)
        return EditPacketResult(
            artifact=artifact,
            publication=self._repository.publish(artifact=artifact, source=source),
        )


def _validate_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value or not value.strip():
        raise ValueError(f"Edit Packet path is not safe and workspace-relative: {value}")


def _forbidden_spec(patterns: tuple[str, ...]) -> PathSpec[Pattern]:
    for value in patterns:
        path = PurePosixPath(value)
        if value.startswith("!") or path.is_absolute() or ".." in path.parts or "\\" in value:
            raise ValueError(f"Forbidden glob has unsafe workspace semantics: {value}")
    return GitIgnoreSpec.from_lines(patterns)
