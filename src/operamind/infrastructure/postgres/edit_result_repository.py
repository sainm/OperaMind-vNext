"""Path-only Edit Result persistence and Analysis Case transition guards."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

from psycopg import Connection, Cursor

from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres.approval_grant_repository import (
    ApprovalGrantRepository,
)
from operamind.infrastructure.postgres.errors import PersistenceConflictError


@dataclass(frozen=True, slots=True)
class EditResultPacketScope:
    edit_packet_id: str
    approval_grant_id: str
    project_id: str
    analysis_case_id: str
    repository_id: str
    base_repository_revision: str
    remote_url: str
    workspace_root: str
    packet_status: str
    writable_files: tuple[str, ...]
    required_command_refs: tuple[str, ...]
    required_ui_scenario_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EditResultWrite:
    edit_result_id: str
    validation_mode: str
    status: str
    result_repository_revision: str | None
    path_changes: tuple[tuple[str, tuple[str, ...]], ...]
    changed_paths: tuple[str, ...]
    out_of_scope_files: tuple[str, ...]
    test_result_refs: tuple[str, ...]
    tests_passed: bool | None
    changed_line_coverage: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EditResultRecord:
    created: bool
    edit_result_id: str
    status: str
    case_status: str
    command_evidence_status: str


class EditResultRepository:
    """Record immutable path evidence and stop an active Packet on any overreach."""

    def __init__(self, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._connection = connection
        self._grants = ApprovalGrantRepository(connection, contracts)

    def load_packet_scope(
        self,
        *,
        project_id: str,
        analysis_case_id: str,
        edit_packet_id: str,
        approval_grant_id: str,
    ) -> EditResultPacketScope:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT packet.repository_id, packet.base_repository_revision,
                       repository.remote_url, repository.workspace_root, packet.status,
                       packet.editable_files, packet.test_files,
                       packet.required_ui_scenario_refs
                FROM edit_packets AS packet
                JOIN repositories AS repository
                  ON repository.repository_id = packet.repository_id
                 AND repository.project_id = packet.project_id
                WHERE packet.project_id = %s
                  AND packet.analysis_case_id = %s
                  AND packet.edit_packet_id = %s
                """,
                (project_id, analysis_case_id, edit_packet_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise ValueError("Edit Packet does not exist in Edit Result scope")
        if row[3] is None:
            raise ValueError("Edit Result Repository workspace_root is not registered")
        grant = self._grants.inspect(approval_grant_id)
        if (grant.project_id, grant.analysis_case_id, grant.edit_packet_id) != (
            project_id,
            analysis_case_id,
            edit_packet_id,
        ):
            raise ValueError("Approval Grant does not match Edit Result scope")
        editable = tuple(str(value) for value in cast(list[object], row[5]))
        tests = tuple(str(value) for value in cast(list[object], row[6]))
        return EditResultPacketScope(
            edit_packet_id=edit_packet_id,
            approval_grant_id=approval_grant_id,
            project_id=project_id,
            analysis_case_id=analysis_case_id,
            repository_id=str(row[0]),
            base_repository_revision=str(row[1]),
            remote_url=str(row[2]),
            workspace_root=str(row[3]),
            packet_status=str(row[4]),
            # An empty production edit scope is an explicit verification-only
            # grant.  Existing tests remain executable evidence, not writable
            # paths, so any file mutation still fails closed as out of scope.
            writable_files=(tuple(sorted({*editable, *tests})) if editable else ()),
            required_command_refs=tuple(sorted(grant.allowed_test_command_refs)),
            required_ui_scenario_refs=tuple(str(value) for value in cast(list[object], row[7])),
        )

    def record(self, *, scope: EditResultPacketScope, write: EditResultWrite) -> EditResultRecord:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            existing = self._load(cursor, write.edit_result_id)
            if existing is not None:
                if existing != _identity(scope, write):
                    raise PersistenceConflictError(
                        f"Edit Result identity has different content: {write.edit_result_id}"
                    )
                return EditResultRecord(
                    False,
                    write.edit_result_id,
                    write.status,
                    _next_case_status(scope, write),
                    _command_evidence_status(write),
                )
            self._grants.authorize_edit(
                grant_id=scope.approval_grant_id,
                project_id=scope.project_id,
                analysis_case_id=scope.analysis_case_id,
                edit_packet_id=scope.edit_packet_id,
                lock=True,
            )
            cursor.execute(
                """
                SELECT status FROM edit_packets
                WHERE edit_packet_id = %s AND project_id = %s
                FOR UPDATE
                """,
                (scope.edit_packet_id, scope.project_id),
            )
            packet = cursor.fetchone()
            if packet is None or str(packet[0]) != "active":
                raise ValueError("Edit Packet is no longer active")
            self._validate_command_evidence(cursor, scope=scope, write=write)
            case_status = _next_case_status(scope, write)
            cursor.execute(
                """
                INSERT INTO edit_results (
                    edit_result_id, edit_packet_id, approval_grant_id,
                    project_id, analysis_case_id,
                    validation_mode, status, base_repository_revision,
                    result_repository_revision, path_changes, changed_paths,
                    out_of_scope_files, test_result_refs, tests_passed,
                    command_evidence_status, changed_line_coverage,
                    changed_line_coverage_status
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                    %s::jsonb, %s::jsonb, %s, %s, %s::jsonb, %s
                )
                """,
                (
                    write.edit_result_id,
                    scope.edit_packet_id,
                    scope.approval_grant_id,
                    scope.project_id,
                    scope.analysis_case_id,
                    write.validation_mode,
                    write.status,
                    scope.base_repository_revision,
                    write.result_repository_revision,
                    _json(
                        [
                            {"status": status, "paths": list(paths)}
                            for status, paths in write.path_changes
                        ]
                    ),
                    _json(list(write.changed_paths)),
                    _json(list(write.out_of_scope_files)),
                    _json(list(write.test_result_refs)),
                    write.tests_passed,
                    _command_evidence_status(write),
                    _json(write.changed_line_coverage),
                    write.changed_line_coverage["status"],
                ),
            )
            if write.test_result_refs:
                cursor.executemany(
                    """
                    INSERT INTO edit_result_command_executions (
                        edit_result_id, command_execution_id, project_id
                    ) VALUES (%s, %s, %s)
                    """,
                    (
                        (write.edit_result_id, command_execution_id, scope.project_id)
                        for command_execution_id in write.test_result_refs
                    ),
                )
            if write.status == "out_of_scope" or write.validation_mode == "committed":
                cursor.execute(
                    "UPDATE edit_packets SET status = 'superseded' WHERE edit_packet_id = %s",
                    (scope.edit_packet_id,),
                )
            if write.status == "out_of_scope":
                self._grants.append_event(
                    event_id=f"approval-event:{write.edit_result_id}:revoked",
                    grant_id=scope.approval_grant_id,
                    project_id=scope.project_id,
                    event_type="revoked",
                    actor="operamind",
                    reason="Edit Result detected files outside the approved Packet",
                )
            elif write.validation_mode == "committed":
                successful = _successful_result(scope, write)
                event_type = (
                    "edit_completed"
                    if successful and scope.required_ui_scenario_refs
                    else "completed"
                )
                self._grants.append_event(
                    event_id=f"approval-event:{write.edit_result_id}:{event_type}",
                    grant_id=scope.approval_grant_id,
                    project_id=scope.project_id,
                    event_type=event_type,
                    actor="operamind",
                    reason=(
                        "Committed edit passed tests and is ready for approved UI scenarios"
                        if event_type == "edit_completed"
                        else "Committed edit validation closed the approval"
                    ),
                )
            cursor.execute(
                """
                UPDATE analysis_cases SET status = %s, updated_at = now()
                WHERE analysis_case_id = %s AND project_id = %s
                """,
                (case_status, scope.analysis_case_id, scope.project_id),
            )
        return EditResultRecord(
            True,
            write.edit_result_id,
            write.status,
            case_status,
            _command_evidence_status(write),
        )

    @staticmethod
    def _validate_command_evidence(
        cursor: Cursor[Any],
        *,
        scope: EditResultPacketScope,
        write: EditResultWrite,
    ) -> None:
        if not write.test_result_refs:
            return
        cursor.execute(
            """
            SELECT request.command_execution_id, request.approval_grant_id,
                   request.project_id, request.analysis_case_id, request.edit_packet_id,
                   request.command_ref, result.status
            FROM command_execution_requests AS request
            JOIN command_execution_results AS result
              ON result.command_execution_id = request.command_execution_id
             AND result.project_id = request.project_id
            WHERE request.command_execution_id = ANY(%s)
            """,
            (list(write.test_result_refs),),
        )
        rows = {str(row[0]): tuple(row[1:]) for row in cursor.fetchall()}
        missing = sorted(set(write.test_result_refs) - set(rows))
        if missing:
            raise ValueError(f"Edit Result command evidence does not exist: {missing}")
        expected_scope = (
            scope.approval_grant_id,
            scope.project_id,
            scope.analysis_case_id,
            scope.edit_packet_id,
        )
        mismatched = sorted(
            command_execution_id
            for command_execution_id, row in rows.items()
            if row[:4] != expected_scope
        )
        if mismatched:
            raise ValueError(
                f"Edit Result command evidence is outside the Grant/Packet scope: {mismatched}"
            )
        actual_command_refs = {str(row[4]) for row in rows.values()}
        if actual_command_refs != set(scope.required_command_refs):
            raise ValueError(
                "Edit Result command evidence does not cover the exact required command set: "
                f"expected={list(scope.required_command_refs)} "
                f"actual={sorted(actual_command_refs)}"
            )
        statuses = {command_execution_id: str(row[5]) for command_execution_id, row in rows.items()}
        if write.tests_passed and any(status != "passed" for status in statuses.values()):
            raise ValueError(
                "Edit Result cannot claim tests passed with non-passing command evidence"
            )
        if write.tests_passed is False and all(status == "passed" for status in statuses.values()):
            raise ValueError("Edit Result cannot claim tests failed when all commands passed")

    @staticmethod
    def _load(cursor: Cursor[Any], edit_result_id: str) -> tuple[object, ...] | None:
        cursor.execute(
            """
            SELECT result.edit_result_id, result.edit_packet_id, result.project_id,
                   result.analysis_case_id, result.approval_grant_id,
                   result.validation_mode, result.status,
                   result.base_repository_revision, result.result_repository_revision,
                   result.path_changes, result.changed_paths, result.out_of_scope_files,
                   result.test_result_refs, result.tests_passed,
                   result.command_evidence_status, result.changed_line_coverage,
                   result.changed_line_coverage_status
            FROM edit_results AS result
            WHERE result.edit_result_id = %s
            """,
            (edit_result_id,),
        )
        row = cursor.fetchone()
        return tuple(row) if row is not None else None


def _identity(scope: EditResultPacketScope, write: EditResultWrite) -> tuple[object, ...]:
    return (
        write.edit_result_id,
        scope.edit_packet_id,
        scope.project_id,
        scope.analysis_case_id,
        scope.approval_grant_id,
        write.validation_mode,
        write.status,
        scope.base_repository_revision,
        write.result_repository_revision,
        [{"status": status, "paths": list(paths)} for status, paths in write.path_changes],
        list(write.changed_paths),
        list(write.out_of_scope_files),
        list(write.test_result_refs),
        write.tests_passed,
        _command_evidence_status(write),
        write.changed_line_coverage,
        write.changed_line_coverage["status"],
    )


def _next_case_status(scope: EditResultPacketScope, write: EditResultWrite) -> str:
    if write.status == "out_of_scope":
        return "reanalysis_required"
    if write.validation_mode == "working":
        return "editing"
    if (
        (write.status == "no_changes" and bool(scope.writable_files))
        or not write.tests_passed
        or write.changed_line_coverage["status"] in {"failed", "missing"}
    ):
        return "failed"
    return "verifying_ui" if scope.required_ui_scenario_refs else "passed"


def _successful_result(scope: EditResultPacketScope, write: EditResultWrite) -> bool:
    status_ok = write.status == "in_scope" or (
        write.status == "no_changes" and not scope.writable_files
    )
    return bool(
        status_ok
        and write.tests_passed
        and write.changed_line_coverage["status"] in {"passed", "not_required"}
    )


def _command_evidence_status(write: EditResultWrite) -> str:
    return "verified" if write.validation_mode == "committed" else "not_applicable"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
