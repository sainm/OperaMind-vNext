"""Immutable Approval Grant persistence and append-only lifecycle authorization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from psycopg import Connection, Cursor

from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres.artifact_repository import ArtifactRepository
from operamind.infrastructure.postgres.edit_packet_repository import (
    EditPacketRecord,
    EditPacketRepository,
)
from operamind.infrastructure.postgres.errors import PersistenceConflictError


@dataclass(frozen=True, slots=True)
class ApprovalGrantSource:
    project_id: str
    analysis_case_id: str
    edit_packet_id: str
    impact_report_id: str
    confirmation_id: str
    repository_id: str
    base_repository_revision: str
    editable_files: tuple[str, ...]
    read_only_files: tuple[str, ...]
    test_files: tuple[str, ...]
    forbidden_globs: tuple[str, ...]
    required_ui_scenario_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ApprovalGrantRecord:
    created: bool
    grant_id: str
    state: str


@dataclass(frozen=True, slots=True)
class ApprovalGrantAuthorization:
    grant_id: str
    project_id: str
    analysis_case_id: str
    edit_packet_id: str
    impact_report_id: str
    confirmation_id: str
    repository_id: str
    base_repository_revision: str
    allowed_actions: tuple[str, ...]
    command_profile_version_id: str
    allowed_test_command_refs: tuple[str, ...]
    allowed_ui_scenarios: tuple[str, ...]
    expires_at: datetime
    state: str


class ApprovalGrantRepository:
    def __init__(self, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._connection = connection
        self._contracts = contracts
        self._artifacts = ArtifactRepository(connection, contracts)
        self._packets = EditPacketRepository(connection, contracts)

    def load_artifact(self, grant_id: str) -> dict[str, Any] | None:
        """Return the immutable Grant payload for identity-safe application replay."""

        artifact = self._artifacts.get(grant_id)
        if artifact is not None and artifact.get("artifact_type") != "ApprovalGrant":
            raise PersistenceConflictError(
                f"Artifact identity is not an Approval Grant: {grant_id}"
            )
        return artifact

    def load_source(
        self,
        *,
        project_id: str,
        analysis_case_id: str,
        edit_packet_id: str,
    ) -> ApprovalGrantSource:
        packet = self._packets.get(edit_packet_id)
        if packet is None:
            raise ValueError("Approval Grant Edit Packet does not exist in requested scope")
        if (packet.project_id, packet.analysis_case_id) != (
            project_id,
            analysis_case_id,
        ):
            raise ValueError("Approval Grant Edit Packet does not exist in requested scope")
        if packet.status != "active":
            raise ValueError("Approval Grant requires an active Edit Packet")
        if packet.impact_report_status != "confirmed":
            raise ValueError("Approval Grant requires a confirmed Impact Report")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status
                FROM analysis_cases
                WHERE project_id = %s AND analysis_case_id = %s
                """,
                (project_id, analysis_case_id),
            )
            row = cursor.fetchone()
        if row is None or str(row[0]) != "editing":
            raise ValueError("Approval Grant requires the Case to be in editing state")
        return _packet_source(packet)

    def load_replay_source(
        self,
        *,
        project_id: str,
        analysis_case_id: str,
        edit_packet_id: str,
    ) -> ApprovalGrantSource:
        """Load immutable Packet scope for an existing Grant without reviving authority."""

        packet = self._packets.get(edit_packet_id)
        if packet is None or (packet.project_id, packet.analysis_case_id) != (
            project_id,
            analysis_case_id,
        ):
            raise ValueError("Approval Grant Edit Packet does not exist in requested scope")
        return _packet_source(packet)

    def issue(
        self,
        *,
        artifact: dict[str, Any],
        source: ApprovalGrantSource,
    ) -> ApprovalGrantRecord:
        self._contracts.validate_artifact(artifact)
        expected = (
            source.project_id,
            source.analysis_case_id,
            source.edit_packet_id,
            source.impact_report_id,
            source.confirmation_id,
            source.repository_id,
            source.base_repository_revision,
            list(source.editable_files),
            list(source.read_only_files),
            list(source.test_files),
            list(source.forbidden_globs),
            list(source.required_ui_scenario_refs),
        )
        actual = (
            artifact.get("project_id"),
            artifact.get("analysis_case_id"),
            artifact.get("edit_packet_id"),
            artifact.get("impact_report_id"),
            artifact.get("confirmation_id"),
            artifact.get("repository_id"),
            artifact.get("base_repository_revision"),
            artifact.get("editable_files"),
            artifact.get("read_only_files"),
            artifact.get("test_files"),
            artifact.get("forbidden_globs"),
            artifact.get("allowed_ui_scenarios"),
        )
        if actual != expected or artifact.get("change_session_id") != source.analysis_case_id:
            raise ValueError("Approval Grant Artifact is outside the active Edit Packet scope")
        _validate_grant_semantics(artifact=artifact, source=source)
        grant_id = str(artifact["approval_grant_id"])
        canonical = _json(artifact)
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        expires_at = _timestamp(str(artifact["expires_at"]))
        with self._connection.transaction(), self._connection.cursor() as cursor:
            packet = self._packets.get(source.edit_packet_id)
            if packet is None:
                raise ValueError("Approval Grant Edit Packet disappeared before issuance")
            _require_packet_source(packet=packet, source=source)
            existing = self._artifacts.get(grant_id)
            if existing is not None:
                if existing != artifact:
                    raise PersistenceConflictError(
                        f"Approval Grant identity has different content: {grant_id}"
                    )
                return ApprovalGrantRecord(False, grant_id, self.inspect(grant_id).state)
            cursor.execute(
                """
                SELECT packet.status, case_record.status, report.status,
                       graph.is_current, graph.status, packet.analysis_case_id,
                       packet.impact_report_id, packet.confirmation_id,
                       packet.repository_id, packet.base_repository_revision,
                       packet.editable_files, packet.read_only_files,
                       packet.test_files, packet.forbidden_globs,
                       packet.required_ui_scenario_refs,
                       report.repository_id, report.repository_revision,
                       %s > now()
                FROM edit_packets AS packet
                JOIN analysis_cases AS case_record
                  ON case_record.analysis_case_id = packet.analysis_case_id
                 AND case_record.project_id = packet.project_id
                JOIN impact_reports AS report
                  ON report.impact_report_id = packet.impact_report_id
                 AND report.project_id = packet.project_id
                JOIN code_graph_snapshots AS graph
                  ON graph.code_graph_snapshot_id = report.code_graph_snapshot_id
                 AND graph.project_id = report.project_id
                JOIN impact_confirmations AS confirmation
                  ON confirmation.confirmation_id = packet.confirmation_id
                 AND confirmation.impact_report_id = report.impact_report_id
                 AND confirmation.project_id = packet.project_id
                WHERE packet.edit_packet_id = %s AND packet.project_id = %s
                FOR UPDATE OF packet, case_record, report
                FOR SHARE OF graph, confirmation
                """,
                (expires_at, source.edit_packet_id, source.project_id),
            )
            current = cursor.fetchone()
            expected_current = _issue_source_identity(source)
            if current is None or tuple(current) != expected_current:
                raise ValueError("Approval Grant source became stale before issuance")
            locked_packet = self._packets.get(source.edit_packet_id)
            if (
                locked_packet is None
                or locked_packet.status != "active"
                or locked_packet.impact_report_status != "confirmed"
            ):
                raise ValueError("Approval Grant source became stale before issuance")
            _require_packet_source(packet=locked_packet, source=source)
            _validate_command_profile_binding(cursor, artifact=artifact)
            self._artifacts.store(
                artifact_id=grant_id,
                project_id=source.project_id,
                analysis_case_id=source.analysis_case_id,
                artifact=artifact,
            )
            cursor.execute(
                """
                INSERT INTO approval_grants (
                    approval_grant_id, project_id, analysis_case_id,
                    edit_packet_id, impact_report_id, confirmation_id,
                    repository_id, base_repository_revision, editable_files,
                    read_only_files, test_files, allowed_actions,
                    command_profile_version_id, allowed_test_command_refs,
                    allowed_ui_scenarios,
                    forbidden_globs, approved_by, expires_at,
                    out_of_scope_policy, payload_digest
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                    %s::jsonb, %s::jsonb, %s::jsonb, %s,
                    %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s
                )
                """,
                (
                    grant_id,
                    source.project_id,
                    source.analysis_case_id,
                    source.edit_packet_id,
                    source.impact_report_id,
                    source.confirmation_id,
                    source.repository_id,
                    source.base_repository_revision,
                    _json(artifact["editable_files"]),
                    _json(artifact["read_only_files"]),
                    _json(artifact["test_files"]),
                    _json(artifact["allowed_actions"]),
                    artifact["command_profile_version_id"],
                    _json(artifact["allowed_test_command_refs"]),
                    _json(artifact["allowed_ui_scenarios"]),
                    _json(artifact["forbidden_globs"]),
                    artifact["approved_by"],
                    expires_at,
                    artifact["out_of_scope_policy"],
                    digest,
                ),
            )
        return ApprovalGrantRecord(True, grant_id, "active_editing")

    def inspect(
        self,
        grant_id: str,
        *,
        at: datetime | None = None,
    ) -> ApprovalGrantAuthorization:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT grant_record.project_id, grant_record.analysis_case_id,
                       grant_record.edit_packet_id, grant_record.impact_report_id,
                       grant_record.confirmation_id, grant_record.repository_id,
                       grant_record.base_repository_revision,
                       grant_record.editable_files, grant_record.read_only_files,
                       grant_record.test_files,
                       grant_record.allowed_actions,
                       grant_record.command_profile_version_id,
                       grant_record.allowed_test_command_refs,
                       grant_record.allowed_ui_scenarios,
                       grant_record.forbidden_globs, grant_record.approved_by,
                       grant_record.expires_at,
                       grant_record.out_of_scope_policy, grant_record.payload_digest
                FROM approval_grants AS grant_record
                WHERE grant_record.approval_grant_id = %s
                """,
                (grant_id,),
            )
            row = cursor.fetchone()
            cursor.execute(
                """
                SELECT approval_grant_event_id, approval_grant_id, project_id,
                       event_type, actor, reason, payload_digest
                FROM approval_grant_events
                WHERE approval_grant_id = %s
                ORDER BY created_at, approval_grant_event_id
                """,
                (grant_id,),
            )
            event_rows = cursor.fetchall()
        if row is None:
            raise ValueError("Approval Grant does not exist")
        artifact = self._artifacts.get(grant_id)
        if artifact is None or artifact.get("artifact_type") != "ApprovalGrant":
            raise PersistenceConflictError(
                f"Approval Grant normalized identity has no immutable Artifact: {grant_id}"
            )
        canonical = _json(artifact)
        expected_digest = hashlib.sha256(canonical.encode()).hexdigest()
        expected_identity = (
            artifact["project_id"],
            artifact["analysis_case_id"],
            artifact["edit_packet_id"],
            artifact["impact_report_id"],
            artifact["confirmation_id"],
            artifact["repository_id"],
            artifact["base_repository_revision"],
            artifact["editable_files"],
            artifact["read_only_files"],
            artifact["test_files"],
            artifact["allowed_actions"],
            artifact["command_profile_version_id"],
            artifact["allowed_test_command_refs"],
            artifact["allowed_ui_scenarios"],
            artifact["forbidden_globs"],
            artifact["approved_by"],
            _timestamp(str(artifact["expires_at"])),
            artifact["out_of_scope_policy"],
            expected_digest,
        )
        if tuple(row[:19]) != expected_identity:
            raise PersistenceConflictError(
                f"Approval Grant normalized identity differs: {grant_id}"
            )
        packet = self._packets.get(str(artifact["edit_packet_id"]))
        if packet is None:
            raise PersistenceConflictError(f"Approval Grant Edit Packet is missing: {grant_id}")
        source = _packet_source(packet)
        expected_source = ApprovalGrantSource(
            project_id=str(artifact["project_id"]),
            analysis_case_id=str(artifact["analysis_case_id"]),
            edit_packet_id=str(artifact["edit_packet_id"]),
            impact_report_id=str(artifact["impact_report_id"]),
            confirmation_id=str(artifact["confirmation_id"]),
            repository_id=str(artifact["repository_id"]),
            base_repository_revision=str(artifact["base_repository_revision"]),
            editable_files=_strings(artifact["editable_files"]),
            read_only_files=_strings(artifact["read_only_files"]),
            test_files=_strings(artifact["test_files"]),
            forbidden_globs=_strings(artifact["forbidden_globs"]),
            required_ui_scenario_refs=_strings(artifact["allowed_ui_scenarios"]),
        )
        if source != expected_source:
            raise PersistenceConflictError(f"Approval Grant Packet scope differs: {grant_id}")
        try:
            _validate_grant_semantics(artifact=artifact, source=source)
        except ValueError as error:
            raise PersistenceConflictError(
                f"Approval Grant derived scope differs: {grant_id}"
            ) from error
        with self._connection.cursor() as cursor:
            _validate_command_profile_binding(cursor, artifact=artifact)
        if row[11] is None:
            raise RuntimeError("Approval Grant has no bound Command Profile Version")
        expires_at = cast(datetime, row[16])
        events: list[str] = []
        for event_row in event_rows:
            event_payload = {
                "event_id": str(event_row[0]),
                "grant_id": str(event_row[1]),
                "project_id": str(event_row[2]),
                "event_type": str(event_row[3]),
                "actor": str(event_row[4]),
                "reason": str(event_row[5]),
            }
            event_digest = hashlib.sha256(_json(event_payload).encode()).hexdigest()
            if (
                str(event_row[1]) != grant_id
                or str(event_row[2]) != str(row[0])
                or str(event_row[6]) != event_digest
            ):
                raise PersistenceConflictError(
                    f"Approval Grant Event normalized identity differs: {event_row[0]}"
                )
            events.append(str(event_row[3]))
        state = _state(events=tuple(events), expires_at=expires_at, at=at)
        return ApprovalGrantAuthorization(
            grant_id=grant_id,
            project_id=str(row[0]),
            analysis_case_id=str(row[1]),
            edit_packet_id=str(row[2]),
            impact_report_id=str(row[3]),
            confirmation_id=str(row[4]),
            repository_id=str(row[5]),
            base_repository_revision=str(row[6]),
            allowed_actions=_strings(row[10]),
            command_profile_version_id=str(row[11]),
            allowed_test_command_refs=_strings(row[12]),
            allowed_ui_scenarios=_strings(row[13]),
            expires_at=expires_at,
            state=state,
        )

    def authorize_edit(
        self,
        *,
        grant_id: str,
        project_id: str,
        analysis_case_id: str,
        edit_packet_id: str,
        required_action: str = "record_result",
        at: datetime | None = None,
        lock: bool = False,
    ) -> ApprovalGrantAuthorization:
        if lock:
            self.lock(grant_id=grant_id, project_id=project_id)
        grant = self.inspect(grant_id, at=at)
        if (grant.project_id, grant.analysis_case_id, grant.edit_packet_id) != (
            project_id,
            analysis_case_id,
            edit_packet_id,
        ):
            raise ValueError("Approval Grant does not match Edit Result scope")
        if grant.state != "active_editing":
            raise ValueError(f"Approval Grant does not permit editing in state: {grant.state}")
        if required_action not in grant.allowed_actions:
            raise ValueError(f"Approval Grant does not allow action: {required_action}")
        self._assert_edit_source_current(grant, lock=lock)
        return grant

    def _assert_edit_source_current(
        self,
        grant: ApprovalGrantAuthorization,
        *,
        lock: bool,
    ) -> None:
        locking = "FOR UPDATE OF packet, report, case_record FOR SHARE OF graph" if lock else ""
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT packet.status, report.status, graph.is_current, graph.status,
                       case_record.status, packet.base_repository_revision,
                       report.repository_revision, packet.impact_report_id,
                       packet.confirmation_id, packet.repository_id
                FROM edit_packets AS packet
                JOIN impact_reports AS report
                  ON report.impact_report_id = packet.impact_report_id
                 AND report.project_id = packet.project_id
                JOIN code_graph_snapshots AS graph
                  ON graph.code_graph_snapshot_id = report.code_graph_snapshot_id
                 AND graph.project_id = report.project_id
                JOIN analysis_cases AS case_record
                  ON case_record.analysis_case_id = packet.analysis_case_id
                 AND case_record.project_id = packet.project_id
                WHERE packet.edit_packet_id = %s
                  AND packet.project_id = %s
                  AND packet.analysis_case_id = %s
                {locking}
                """,
                (grant.edit_packet_id, grant.project_id, grant.analysis_case_id),
            )
            source = cursor.fetchone()
        expected = (
            "active",
            "confirmed",
            True,
            "complete",
            "editing",
            grant.base_repository_revision,
            grant.base_repository_revision,
            grant.impact_report_id,
            grant.confirmation_id,
            grant.repository_id,
        )
        if source is None or tuple(source) != expected:
            raise ValueError("Approval Grant source is no longer current for editing")

    def lock(self, *, grant_id: str, project_id: str) -> None:
        """Serialize one authorization decision with its protected write transaction."""

        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT project_id FROM approval_grants
                WHERE approval_grant_id = %s
                FOR UPDATE
                """,
                (grant_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise ValueError("Approval Grant does not exist")
        if str(row[0]) != project_id:
            raise ValueError("Approval Grant project does not match")

    def append_event(
        self,
        *,
        event_id: str,
        grant_id: str,
        project_id: str,
        event_type: str,
        actor: str,
        reason: str,
    ) -> bool:
        if event_type not in {"edit_completed", "completed", "revoked"}:
            raise ValueError("Approval Grant event type is invalid")
        if any(not value.strip() for value in (event_id, grant_id, project_id, actor, reason)):
            raise ValueError("Approval Grant event fields must not be blank")
        payload = {
            "event_id": event_id,
            "grant_id": grant_id,
            "project_id": project_id,
            "event_type": event_type,
            "actor": actor,
            "reason": reason,
        }
        digest = hashlib.sha256(_json(payload).encode()).hexdigest()
        expected = (grant_id, project_id, event_type, actor, reason, digest)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            self.lock(grant_id=grant_id, project_id=project_id)
            cursor.execute(
                """
                SELECT approval_grant_id, project_id, event_type, actor,
                       reason, payload_digest
                FROM approval_grant_events
                WHERE approval_grant_event_id = %s
                """,
                (event_id,),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if tuple(existing) != expected:
                    raise PersistenceConflictError(
                        f"Approval Grant Event has different content: {event_id}"
                    )
                return False
            current = self.inspect(grant_id)
            if current.project_id != project_id:
                raise ValueError("Approval Grant Event project does not match")
            if event_type == "edit_completed" and current.state != "active_editing":
                raise ValueError("Approval Grant edit completion requires active editing")
            if event_type in {"completed", "revoked"} and current.state not in {
                "active_editing",
                "ui_pending",
            }:
                raise ValueError("Approval Grant is already terminal")
            cursor.execute(
                """
                INSERT INTO approval_grant_events (
                    approval_grant_event_id, approval_grant_id, project_id,
                    event_type, actor, reason, payload_digest
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (event_id, grant_id, project_id, event_type, actor, reason, digest),
            )
        return True


def _state(*, events: tuple[str, ...], expires_at: datetime, at: datetime | None) -> str:
    if "revoked" in events:
        return "revoked"
    if "completed" in events:
        return "completed"
    instant = at or datetime.now(UTC)
    if instant.tzinfo is None:
        raise ValueError("Approval Grant inspection time must include a timezone")
    if expires_at <= instant:
        return "expired"
    return "ui_pending" if "edit_completed" in events else "active_editing"


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Approval Grant expires_at must include a timezone")
    return parsed


def _strings(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in cast(list[object], value))


def _packet_source(packet: EditPacketRecord) -> ApprovalGrantSource:
    artifact = packet.artifact
    return ApprovalGrantSource(
        project_id=packet.project_id,
        analysis_case_id=packet.analysis_case_id,
        edit_packet_id=str(artifact["edit_packet_id"]),
        impact_report_id=str(artifact["impact_report_id"]),
        confirmation_id=str(artifact["confirmation_id"]),
        repository_id=str(artifact["repository_id"]),
        base_repository_revision=str(artifact["base_repository_revision"]),
        editable_files=_strings(artifact["editable_files"]),
        read_only_files=_strings(artifact["read_only_files"]),
        test_files=_strings(artifact["test_files"]),
        forbidden_globs=_strings(artifact["forbidden_globs"]),
        required_ui_scenario_refs=_strings(artifact["required_ui_scenario_refs"]),
    )


def _require_packet_source(*, packet: EditPacketRecord, source: ApprovalGrantSource) -> None:
    if _packet_source(packet) != source:
        raise PersistenceConflictError(
            f"Approval Grant Packet source differs: {source.edit_packet_id}"
        )


def _issue_source_identity(source: ApprovalGrantSource) -> tuple[object, ...]:
    """Mirror the issuance lock query column order, including expiry last."""

    return (
        "active",
        "editing",
        "confirmed",
        True,
        "complete",
        source.analysis_case_id,
        source.impact_report_id,
        source.confirmation_id,
        source.repository_id,
        source.base_repository_revision,
        list(source.editable_files),
        list(source.read_only_files),
        list(source.test_files),
        list(source.forbidden_globs),
        list(source.required_ui_scenario_refs),
        source.repository_id,
        source.base_repository_revision,
        True,
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _validate_grant_semantics(*, artifact: dict[str, Any], source: ApprovalGrantSource) -> None:
    if artifact.get("change_session_id") != source.analysis_case_id:
        raise ValueError("Approval Grant change session differs from the Case scope")
    expected_actions = ["read", "modify", "record_result"]
    if source.test_files:
        expected_actions.extend(("add_test", "run_test"))
    if source.required_ui_scenario_refs:
        expected_actions.extend(("execute_ui", "record_evidence"))
    if artifact["allowed_actions"] != expected_actions:
        raise ValueError("Approval Grant actions are not derived from the Edit Packet scope")
    if artifact["allowed_test_command_refs"] and not source.test_files:
        raise ValueError("Approval Grant cannot allow test commands without approved test files")


def _validate_command_profile_binding(cursor: Cursor[Any], *, artifact: dict[str, Any]) -> None:
    profile_version_id = str(artifact["command_profile_version_id"])
    cursor.execute(
        """
        SELECT profile_type, profile_id, semantic_version, payload, payload_digest
        FROM profile_versions
        WHERE profile_version_id = %s
        FOR SHARE
        """,
        (profile_version_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError("Approval Grant Command Profile Version does not exist")
    profile = cast(dict[str, Any], row[3])
    digest = hashlib.sha256(_json(profile).encode()).hexdigest()
    expected_identity = (
        "CommandExecutionProfile",
        profile.get("profile_id"),
        profile.get("profile_version"),
        profile,
        digest,
    )
    if tuple(row) != expected_identity:
        raise PersistenceConflictError(
            f"Command Profile Version normalized identity differs: {profile_version_id}"
        )
    templates = profile.get("templates")
    if not isinstance(templates, list):
        raise PersistenceConflictError("Command Profile Version has invalid templates")
    available_refs = {
        str(template["command_ref"])
        for template in templates
        if isinstance(template, dict) and isinstance(template.get("command_ref"), str)
    }
    requested_refs = set(_strings(artifact["allowed_test_command_refs"]))
    unknown_refs = sorted(requested_refs - available_refs)
    if unknown_refs:
        raise ValueError(f"Approval Grant references unknown command templates: {unknown_refs}")
