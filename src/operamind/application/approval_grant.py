"""Issue and manage one bounded Approval Grant for an active Edit Packet."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from psycopg import Connection

from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres import (
    ApprovalGrantRecord,
    ApprovalGrantRepository,
    ProfileRepository,
)
from operamind.profiles import ProfileCatalog


@dataclass(frozen=True, slots=True)
class ApprovalGrantRequest:
    grant_id: str
    project_id: str
    analysis_case_id: str
    edit_packet_id: str
    approved_by: str
    expires_at: datetime
    command_profile_binding_key: str
    allowed_test_command_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (
                self.grant_id,
                self.project_id,
                self.analysis_case_id,
                self.edit_packet_id,
                self.approved_by,
                self.command_profile_binding_key,
            )
        ):
            raise ValueError("Approval Grant request fields must not be blank")
        if self.expires_at.tzinfo is None:
            raise ValueError("Approval Grant expires_at must include a timezone")
        if self.expires_at <= datetime.now(UTC):
            raise ValueError("Approval Grant expires_at must be in the future")
        if len(self.allowed_test_command_refs) != len(set(self.allowed_test_command_refs)) or any(
            not value.strip() for value in self.allowed_test_command_refs
        ):
            raise ValueError("Approval Grant test command refs must be unique and non-blank")


@dataclass(frozen=True, slots=True)
class ApprovalGrantResult:
    artifact: dict[str, Any]
    record: ApprovalGrantRecord


class ApprovalGrantService:
    def __init__(
        self,
        *,
        connection: Connection[Any],
        contracts: ContractCatalog,
        profiles: ProfileCatalog,
    ) -> None:
        self._contracts = contracts
        self._repository = ApprovalGrantRepository(connection, contracts)
        self._profiles = ProfileRepository(connection, profiles)

    def issue(self, request: ApprovalGrantRequest) -> ApprovalGrantResult:
        existing = self._repository.load_artifact(request.grant_id)
        if existing is not None:
            source = self._repository.load_replay_source(
                project_id=request.project_id,
                analysis_case_id=request.analysis_case_id,
                edit_packet_id=request.edit_packet_id,
            )
            expected_expiry = request.expires_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
            replay_identity = (
                existing.get("project_id"),
                existing.get("analysis_case_id"),
                existing.get("edit_packet_id"),
                existing.get("approved_by"),
                existing.get("expires_at"),
                existing.get("allowed_test_command_refs"),
            )
            requested_identity = (
                request.project_id,
                request.analysis_case_id,
                request.edit_packet_id,
                request.approved_by,
                expected_expiry,
                list(request.allowed_test_command_refs),
            )
            if replay_identity != requested_identity:
                raise ValueError("Approval Grant replay request differs from immutable Artifact")
            return ApprovalGrantResult(
                artifact=existing,
                record=self._repository.issue(artifact=existing, source=source),
            )
        source = self._repository.load_source(
            project_id=request.project_id,
            analysis_case_id=request.analysis_case_id,
            edit_packet_id=request.edit_packet_id,
        )
        command_binding = self._profiles.get_active(
            project_id=request.project_id,
            binding_key=request.command_profile_binding_key,
        )
        if (
            command_binding is None
            or command_binding.profile.get("profile_type") != "CommandExecutionProfile"
        ):
            raise ValueError("Approval Grant requires an active Command Execution Profile")
        templates = command_binding.profile.get("templates")
        if not isinstance(templates, list):
            raise RuntimeError("Validated Command Execution Profile lost its templates")
        available_refs = {
            str(template["command_ref"])
            for template in templates
            if isinstance(template, dict) and isinstance(template.get("command_ref"), str)
        }
        unknown_refs = sorted(set(request.allowed_test_command_refs) - available_refs)
        if unknown_refs:
            raise ValueError(f"Approval Grant references unknown command templates: {unknown_refs}")
        actions = ["read"]
        if source.editable_files:
            actions.append("modify")
        actions.append("record_result")
        if source.test_files:
            if source.editable_files:
                actions.append("add_test")
            actions.append("run_test")
        if source.required_ui_scenario_refs:
            actions.extend(("execute_ui", "record_evidence"))
        artifact: dict[str, Any] = {
            "artifact_type": "ApprovalGrant",
            "schema_version": "v1",
            "approval_grant_id": request.grant_id,
            "change_session_id": source.analysis_case_id,
            "analysis_case_id": source.analysis_case_id,
            "edit_packet_id": source.edit_packet_id,
            "impact_report_id": source.impact_report_id,
            "confirmation_id": source.confirmation_id,
            "project_id": source.project_id,
            "repository_id": source.repository_id,
            "base_repository_revision": source.base_repository_revision,
            "editable_files": list(source.editable_files),
            "read_only_files": list(source.read_only_files),
            "test_files": list(source.test_files),
            "allowed_actions": actions,
            "command_profile_version_id": command_binding.profile_version_id,
            "allowed_test_command_refs": list(request.allowed_test_command_refs),
            "allowed_ui_scenarios": list(source.required_ui_scenario_refs),
            "forbidden_globs": list(source.forbidden_globs),
            "approved_by": request.approved_by,
            "expires_at": request.expires_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "out_of_scope_policy": "collect_and_request_once",
        }
        self._contracts.validate_artifact(artifact)
        return ApprovalGrantResult(
            artifact=artifact,
            record=self._repository.issue(artifact=artifact, source=source),
        )

    def revoke(
        self,
        *,
        event_id: str,
        grant_id: str,
        project_id: str,
        revoked_by: str,
        reason: str,
    ) -> bool:
        return self._repository.append_event(
            event_id=event_id,
            grant_id=grant_id,
            project_id=project_id,
            event_type="revoked",
            actor=revoked_by,
            reason=reason,
        )
