"""Append-only request reservations and results for approved local commands."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any, cast

from psycopg import Connection, Cursor

from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres.approval_grant_repository import (
    ApprovalGrantRepository,
)
from operamind.infrastructure.postgres.errors import PersistenceConflictError


@dataclass(frozen=True, slots=True)
class CommandExecutionScope:
    approval_grant_id: str
    project_id: str
    analysis_case_id: str
    edit_packet_id: str
    repository_id: str
    command_profile_version_id: str
    command_ref: str
    base_repository_revision: str
    remote_url: str
    workspace_root: str


@dataclass(frozen=True, slots=True)
class CommandExecutionRequestWrite:
    command_execution_id: str
    scope: CommandExecutionScope
    template_digest: str
    request_digest: str


@dataclass(frozen=True, slots=True)
class CommandExecutionResultWrite:
    status: str
    exit_code: int | None
    executable_path: str
    working_directory: str
    stdout_digest: str
    stderr_digest: str
    stdout_bytes: int
    stderr_bytes: int
    output_truncated: bool
    started_at: datetime
    completed_at: datetime
    coverage_report_format: str | None = None
    coverage_report_path: str | None = None
    coverage_report_digest: str | None = None
    recovery_id: str | None = None
    recovery_actor: str | None = None
    recovery_reason: str | None = None
    recovery_stale_before: datetime | None = None

    def __post_init__(self) -> None:
        coverage_values = (
            self.coverage_report_format,
            self.coverage_report_path,
            self.coverage_report_digest,
        )
        if any(value is not None for value in coverage_values):
            if any(value is None or not value.strip() for value in coverage_values):
                raise ValueError("Coverage report binding must be complete")
            if self.status != "passed":
                raise ValueError("Only a passed command can bind a Coverage report")
            if len(self.coverage_report_digest or "") != 64:
                raise ValueError("Coverage report digest must be SHA-256")
        recovery_values = (
            self.recovery_id,
            self.recovery_actor,
            self.recovery_reason,
            self.recovery_stale_before,
        )
        if self.status == "interrupted":
            if any(value is None for value in recovery_values):
                raise ValueError("Interrupted command result requires complete recovery audit")
            if any(
                not value.strip()
                for value in (
                    self.recovery_id,
                    self.recovery_actor,
                    self.recovery_reason,
                )
                if value is not None
            ):
                raise ValueError("Command recovery audit fields must not be blank")
            if self.recovery_stale_before is None or self.recovery_stale_before.utcoffset() is None:
                raise ValueError("Command recovery stale_before must include a timezone")
            if self.exit_code is not None:
                raise ValueError("Interrupted command result must not have an exit code")
        elif any(value is not None for value in recovery_values):
            raise ValueError("Recovery audit is only valid for an interrupted command result")


@dataclass(frozen=True, slots=True)
class CommandExecutionRecord:
    created: bool
    command_execution_id: str
    status: str
    exit_code: int | None
    executable_path: str
    working_directory: str
    stdout_digest: str
    stderr_digest: str
    stdout_bytes: int
    stderr_bytes: int
    output_truncated: bool
    started_at: datetime
    completed_at: datetime
    coverage_report_format: str | None = None
    coverage_report_path: str | None = None
    coverage_report_digest: str | None = None
    recovery_id: str | None = None
    recovery_actor: str | None = None
    recovery_reason: str | None = None
    recovery_stale_before: datetime | None = None


@dataclass(frozen=True, slots=True)
class CommandExecutionReservation:
    created: bool
    incomplete: bool
    result: CommandExecutionRecord | None


class CommandExecutionRepository:
    """Persist an execution intent before running and exactly one immutable result."""

    def __init__(self, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._connection = connection
        self._grants = ApprovalGrantRepository(connection, contracts)

    def load_scope(
        self,
        *,
        approval_grant_id: str,
        project_id: str,
        analysis_case_id: str,
        edit_packet_id: str,
        command_ref: str,
    ) -> CommandExecutionScope:
        grant = self._grants.authorize_edit(
            grant_id=approval_grant_id,
            project_id=project_id,
            analysis_case_id=analysis_case_id,
            edit_packet_id=edit_packet_id,
            required_action="run_test",
        )
        if command_ref not in grant.allowed_test_command_refs:
            raise ValueError(f"Approval Grant does not allow command_ref: {command_ref}")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT packet.repository_id, repository.remote_url, repository.workspace_root
                FROM edit_packets AS packet
                JOIN repositories AS repository
                  ON repository.repository_id = packet.repository_id
                 AND repository.project_id = packet.project_id
                WHERE packet.edit_packet_id = %s
                  AND packet.project_id = %s
                  AND packet.analysis_case_id = %s
                """,
                (edit_packet_id, project_id, analysis_case_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise ValueError("Approved command Edit Packet does not exist")
        if row[2] is None:
            raise ValueError("Approved command Repository workspace_root is not registered")
        return CommandExecutionScope(
            approval_grant_id=approval_grant_id,
            project_id=project_id,
            analysis_case_id=analysis_case_id,
            edit_packet_id=edit_packet_id,
            repository_id=str(row[0]),
            command_profile_version_id=grant.command_profile_version_id,
            command_ref=command_ref,
            base_repository_revision=grant.base_repository_revision,
            remote_url=str(row[1]),
            workspace_root=str(row[2]),
        )

    def reserve(self, write: CommandExecutionRequestWrite) -> CommandExecutionReservation:
        expected = _request_identity(write)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT approval_grant_id, project_id, analysis_case_id, edit_packet_id,
                       repository_id, command_profile_version_id, command_ref,
                       base_repository_revision, remote_url, workspace_root,
                       template_digest, request_digest
                FROM command_execution_requests
                WHERE command_execution_id = %s
                FOR UPDATE
                """,
                (write.command_execution_id,),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if tuple(existing) != expected:
                    raise PersistenceConflictError(
                        "Command execution identity has different content: "
                        f"{write.command_execution_id}"
                    )
                result = self._load_result(cursor, write.command_execution_id)
                return CommandExecutionReservation(False, result is None, result)
            grant = self._grants.authorize_edit(
                grant_id=write.scope.approval_grant_id,
                project_id=write.scope.project_id,
                analysis_case_id=write.scope.analysis_case_id,
                edit_packet_id=write.scope.edit_packet_id,
                required_action="run_test",
                lock=True,
            )
            if write.scope.command_ref not in grant.allowed_test_command_refs:
                raise ValueError(
                    f"Approval Grant no longer allows command_ref: {write.scope.command_ref}"
                )
            cursor.execute(
                """
                INSERT INTO command_execution_requests (
                    command_execution_id, approval_grant_id, project_id,
                    analysis_case_id, edit_packet_id, repository_id,
                    command_profile_version_id, command_ref,
                    base_repository_revision, remote_url, workspace_root,
                    template_digest, request_digest
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (write.command_execution_id, *expected),
            )
        return CommandExecutionReservation(True, False, None)

    def record(
        self,
        *,
        command_execution_id: str,
        project_id: str,
        write: CommandExecutionResultWrite,
    ) -> CommandExecutionRecord:
        result_digest = _result_digest(write)
        expected = _result_identity(project_id, write, result_digest)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT project_id
                FROM command_execution_requests
                WHERE command_execution_id = %s
                FOR UPDATE
                """,
                (command_execution_id,),
            )
            request = cursor.fetchone()
            if request is None or str(request[0]) != project_id:
                raise ValueError("Command execution request does not exist in requested project")
            existing = self._load_result(cursor, command_execution_id)
            if existing is not None:
                if _record_identity(existing, project_id) != expected:
                    raise PersistenceConflictError(
                        f"Command execution result has different content: {command_execution_id}"
                    )
                return _with_created(existing, False)
            cursor.execute(
                """
                INSERT INTO command_execution_results (
                    command_execution_id, project_id, status, exit_code,
                    executable_path, working_directory, stdout_digest, stderr_digest,
                    stdout_bytes, stderr_bytes, output_truncated, result_digest,
                    started_at, completed_at, recovery_id, recovery_actor,
                    recovery_reason, recovery_stale_before,
                    coverage_report_format, coverage_report_path,
                    coverage_report_digest
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s
                )
                """,
                (command_execution_id, *expected),
            )
        return CommandExecutionRecord(
            created=True,
            command_execution_id=command_execution_id,
            status=write.status,
            exit_code=write.exit_code,
            executable_path=write.executable_path,
            working_directory=write.working_directory,
            stdout_digest=write.stdout_digest,
            stderr_digest=write.stderr_digest,
            stdout_bytes=write.stdout_bytes,
            stderr_bytes=write.stderr_bytes,
            output_truncated=write.output_truncated,
            started_at=write.started_at,
            completed_at=write.completed_at,
            coverage_report_format=write.coverage_report_format,
            coverage_report_path=write.coverage_report_path,
            coverage_report_digest=write.coverage_report_digest,
            recovery_id=write.recovery_id,
            recovery_actor=write.recovery_actor,
            recovery_reason=write.recovery_reason,
            recovery_stale_before=write.recovery_stale_before,
        )

    def recover_incomplete(
        self,
        *,
        recovery_id: str,
        command_execution_id: str,
        project_id: str,
        actor: str,
        reason: str,
        stale_before: datetime,
    ) -> CommandExecutionRecord:
        """Close a stale reservation with one immutable interrupted result."""

        required = (recovery_id, command_execution_id, project_id, actor, reason)
        if any(not value.strip() for value in required):
            raise ValueError("Command execution recovery fields must not be blank")
        if stale_before.utcoffset() is None:
            raise ValueError("Command recovery stale_before must include a timezone")
        boundary = stale_before.astimezone(UTC)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT project_id, workspace_root, requested_at,
                       requested_at <= %s,
                       %s <= clock_timestamp(),
                       clock_timestamp()
                FROM command_execution_requests
                WHERE command_execution_id = %s
                FOR UPDATE
                """,
                (boundary, boundary, command_execution_id),
            )
            request = cursor.fetchone()
            if request is None or str(request[0]) != project_id:
                raise ValueError("Command execution request does not exist in requested project")
            existing = self._load_result(cursor, command_execution_id)
            if existing is not None:
                expected_recovery = (recovery_id, actor, reason, boundary)
                actual_recovery = (
                    existing.recovery_id,
                    existing.recovery_actor,
                    existing.recovery_reason,
                    existing.recovery_stale_before,
                )
                if existing.status != "interrupted" or actual_recovery != expected_recovery:
                    raise PersistenceConflictError(
                        f"Command execution has a different terminal result: {command_execution_id}"
                    )
                return _with_created(existing, False)
            if not bool(request[3]) or not bool(request[4]):
                raise ValueError("Command reservation is newer than the recovery boundary")
            empty_digest = hashlib.sha256(b"").hexdigest()
            write = CommandExecutionResultWrite(
                status="interrupted",
                exit_code=None,
                executable_path="<interrupted-before-result>",
                working_directory=str(request[1]),
                stdout_digest=empty_digest,
                stderr_digest=empty_digest,
                stdout_bytes=0,
                stderr_bytes=0,
                output_truncated=False,
                started_at=cast(datetime, request[2]),
                completed_at=cast(datetime, request[5]),
                recovery_id=recovery_id,
                recovery_actor=actor,
                recovery_reason=reason,
                recovery_stale_before=boundary,
            )
            result_digest = _result_digest(write)
            expected = _result_identity(project_id, write, result_digest)
            cursor.execute(
                """
                INSERT INTO command_execution_results (
                    command_execution_id, project_id, status, exit_code,
                    executable_path, working_directory, stdout_digest, stderr_digest,
                    stdout_bytes, stderr_bytes, output_truncated, result_digest,
                    started_at, completed_at, recovery_id, recovery_actor,
                    recovery_reason, recovery_stale_before,
                    coverage_report_format, coverage_report_path,
                    coverage_report_digest
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s
                )
                """,
                (command_execution_id, *expected),
            )
        return CommandExecutionRecord(
            created=True,
            command_execution_id=command_execution_id,
            status=write.status,
            exit_code=write.exit_code,
            executable_path=write.executable_path,
            working_directory=write.working_directory,
            stdout_digest=write.stdout_digest,
            stderr_digest=write.stderr_digest,
            stdout_bytes=write.stdout_bytes,
            stderr_bytes=write.stderr_bytes,
            output_truncated=write.output_truncated,
            started_at=write.started_at,
            completed_at=write.completed_at,
            coverage_report_format=write.coverage_report_format,
            coverage_report_path=write.coverage_report_path,
            coverage_report_digest=write.coverage_report_digest,
            recovery_id=write.recovery_id,
            recovery_actor=write.recovery_actor,
            recovery_reason=write.recovery_reason,
            recovery_stale_before=write.recovery_stale_before,
        )

    @staticmethod
    def _load_result(
        cursor: Cursor[Any], command_execution_id: str
    ) -> CommandExecutionRecord | None:
        cursor.execute(
            """
            SELECT status, exit_code, executable_path, working_directory,
                   stdout_digest, stderr_digest, stdout_bytes, stderr_bytes,
                   output_truncated, started_at, completed_at,
                   recovery_id, recovery_actor, recovery_reason, recovery_stale_before,
                   project_id, result_digest, coverage_report_format,
                   coverage_report_path, coverage_report_digest
            FROM command_execution_results
            WHERE command_execution_id = %s
            """,
            (command_execution_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        record = CommandExecutionRecord(
            created=False,
            command_execution_id=command_execution_id,
            status=str(row[0]),
            exit_code=cast(int | None, row[1]),
            executable_path=str(row[2]),
            working_directory=str(row[3]),
            stdout_digest=str(row[4]),
            stderr_digest=str(row[5]),
            stdout_bytes=int(row[6]),
            stderr_bytes=int(row[7]),
            output_truncated=bool(row[8]),
            started_at=cast(datetime, row[9]),
            completed_at=cast(datetime, row[10]),
            coverage_report_format=str(row[17]) if row[17] is not None else None,
            coverage_report_path=str(row[18]) if row[18] is not None else None,
            coverage_report_digest=str(row[19]) if row[19] is not None else None,
            recovery_id=str(row[11]) if row[11] is not None else None,
            recovery_actor=str(row[12]) if row[12] is not None else None,
            recovery_reason=str(row[13]) if row[13] is not None else None,
            recovery_stale_before=cast(datetime | None, row[14]),
        )
        computed_digest = _record_identity(record, str(row[15]))[10]
        if str(row[16]) != computed_digest:
            raise PersistenceConflictError(
                f"Command execution result digest does not match content: {command_execution_id}"
            )
        return record


def _request_identity(write: CommandExecutionRequestWrite) -> tuple[object, ...]:
    scope = write.scope
    return (
        scope.approval_grant_id,
        scope.project_id,
        scope.analysis_case_id,
        scope.edit_packet_id,
        scope.repository_id,
        scope.command_profile_version_id,
        scope.command_ref,
        scope.base_repository_revision,
        scope.remote_url,
        scope.workspace_root,
        write.template_digest,
        write.request_digest,
    )


def _result_digest(write: CommandExecutionResultWrite) -> str:
    payload = asdict(write)
    if write.coverage_report_format is None:
        payload.pop("coverage_report_format")
        payload.pop("coverage_report_path")
        payload.pop("coverage_report_digest")
    payload["started_at"] = _canonical_timestamp(write.started_at)
    payload["completed_at"] = _canonical_timestamp(write.completed_at)
    if write.recovery_stale_before is not None:
        payload["recovery_stale_before"] = _canonical_timestamp(write.recovery_stale_before)
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _canonical_timestamp(value: datetime) -> str:
    """Bind an instant independently of the PostgreSQL session time zone."""

    if value.utcoffset() is None:
        raise ValueError("Command execution result timestamps must include a timezone")
    return value.astimezone(UTC).isoformat()


def _result_identity(
    project_id: str, write: CommandExecutionResultWrite, result_digest: str
) -> tuple[object, ...]:
    return (
        project_id,
        write.status,
        write.exit_code,
        write.executable_path,
        write.working_directory,
        write.stdout_digest,
        write.stderr_digest,
        write.stdout_bytes,
        write.stderr_bytes,
        write.output_truncated,
        result_digest,
        write.started_at,
        write.completed_at,
        write.recovery_id,
        write.recovery_actor,
        write.recovery_reason,
        write.recovery_stale_before,
        write.coverage_report_format,
        write.coverage_report_path,
        write.coverage_report_digest,
    )


def _record_identity(record: CommandExecutionRecord, project_id: str) -> tuple[object, ...]:
    write = CommandExecutionResultWrite(
        status=record.status,
        exit_code=record.exit_code,
        executable_path=record.executable_path,
        working_directory=record.working_directory,
        stdout_digest=record.stdout_digest,
        stderr_digest=record.stderr_digest,
        stdout_bytes=record.stdout_bytes,
        stderr_bytes=record.stderr_bytes,
        output_truncated=record.output_truncated,
        started_at=record.started_at,
        completed_at=record.completed_at,
        coverage_report_format=record.coverage_report_format,
        coverage_report_path=record.coverage_report_path,
        coverage_report_digest=record.coverage_report_digest,
        recovery_id=record.recovery_id,
        recovery_actor=record.recovery_actor,
        recovery_reason=record.recovery_reason,
        recovery_stale_before=record.recovery_stale_before,
    )
    return _result_identity(project_id, write, _result_digest(write))


def _with_created(record: CommandExecutionRecord, created: bool) -> CommandExecutionRecord:
    return replace(record, created=created)
