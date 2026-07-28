"""Canonical local-Bridge task queue and MCP result ledger."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from psycopg import Connection, Cursor

from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres.artifact_repository import ArtifactRepository
from operamind.infrastructure.postgres.errors import PersistenceConflictError

CLAIM_LEASE_SECONDS = 60


@dataclass(frozen=True, slots=True)
class CopilotCodingTaskRecord:
    coding_task_id: str
    project_id: str
    change_request_id: str
    analysis_case_id: str | None
    repository_id: str | None
    edit_packet_id: str | None
    approval_grant_id: str | None
    base_repository_revision: str | None
    execution_mode: str
    provider_route: str
    provider_id: str
    workspace_root: str
    state: str
    claimed_by: str | None
    claim_expires_at: datetime | None
    accepted_by: str | None
    retry_of_coding_task_id: str | None
    attempt_number: int
    current_stage: str
    created: bool = False


class CopilotCodingTaskRepository:
    """Persist one transport-neutral task and its local delivery/result events."""

    def __init__(self, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._connection = connection
        self._artifacts = ArtifactRepository(connection, contracts)

    def publish(
        self,
        *,
        artifact: dict[str, Any],
        workspace_root: Path,
        idempotency_key: str,
    ) -> CopilotCodingTaskRecord:
        task_id = str(artifact["coding_task_id"])
        project_id = str(artifact["project_id"])
        case_id = _optional_text(artifact.get("analysis_case_id"))
        provider = cast(dict[str, object], artifact["provider_contract"])
        resolved_workspace = str(workspace_root.resolve(strict=True))
        with self._connection.transaction():
            digest = self._artifacts.store(
                artifact_id=task_id,
                project_id=project_id,
                analysis_case_id=case_id,
                artifact=artifact,
            )
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO copilot_coding_tasks (
                        coding_task_id, project_id, change_request_id,
                        analysis_case_id, repository_id, edit_packet_id,
                        approval_grant_id, base_repository_revision,
                        execution_mode, provider_route, provider_id,
                        workspace_root, state, payload_digest, created_by,
                        retry_of_coding_task_id, attempt_number, current_stage
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, 'pending_confirmation', %s, %s, %s, %s, %s
                    )
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        task_id,
                        project_id,
                        artifact["change_request_id"],
                        case_id,
                        artifact.get("repository_id"),
                        artifact.get("edit_packet_id"),
                        artifact.get("approval_grant_id"),
                        artifact.get("base_repository_revision"),
                        artifact["execution_mode"],
                        provider["route"],
                        provider["provider_id"],
                        resolved_workspace,
                        digest,
                        artifact["created_by"],
                        artifact.get("retry_of_coding_task_id"),
                        artifact.get("attempt_number", 1),
                        artifact.get("initial_stage", "compile_test"),
                    ),
                )
                created = cursor.rowcount == 1
                record = self._get_locked(cursor, task_id)
                if record is None:
                    raise RuntimeError("Copilot Coding Task disappeared during publication")
                expected = (
                    project_id,
                    str(artifact["change_request_id"]),
                    case_id,
                    _optional_text(artifact.get("repository_id")),
                    _optional_text(artifact.get("edit_packet_id")),
                    _optional_text(artifact.get("approval_grant_id")),
                    _optional_text(artifact.get("base_repository_revision")),
                    str(artifact["execution_mode"]),
                    str(provider["route"]),
                    str(provider["provider_id"]),
                    resolved_workspace,
                    str(artifact["retry_of_coding_task_id"])
                    if artifact.get("retry_of_coding_task_id") is not None
                    else None,
                    int(artifact.get("attempt_number", 1)),
                    str(artifact.get("initial_stage", "compile_test")),
                    digest,
                )
                actual = (
                    record.project_id,
                    record.change_request_id,
                    record.analysis_case_id,
                    record.repository_id,
                    record.edit_packet_id,
                    record.approval_grant_id,
                    record.base_repository_revision,
                    record.execution_mode,
                    record.provider_route,
                    record.provider_id,
                    record.workspace_root,
                    record.retry_of_coding_task_id,
                    record.attempt_number,
                    record.current_stage,
                    self._payload_digest(cursor, task_id),
                )
                if actual != expected:
                    raise PersistenceConflictError(
                        f"Copilot Coding Task identity has different content: {task_id}"
                    )
                self._append_event(
                    cursor,
                    record=record,
                    event_type="published",
                    actor=str(artifact["created_by"]),
                    idempotency_key=f"publish:{idempotency_key}",
                    payload={"state": "pending_confirmation"},
                )
        return replace(record, created=created)

    def claim_next(self, *, workspace_root: Path, consumer_id: str) -> dict[str, object] | None:
        root = str(workspace_root.resolve(strict=True))
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT task.coding_task_id, task.claimed_by,
                       COALESCE(task.claim_expires_at <= now(), false),
                       task.claim_expires_at
                FROM copilot_coding_tasks AS task
                LEFT JOIN approval_grants AS grant_record
                  ON grant_record.approval_grant_id = task.approval_grant_id
                 AND grant_record.project_id = task.project_id
                WHERE task.provider_route = 'local_bridge'
                  AND task.workspace_root = %s
                  AND task.state IN ('pending_confirmation', 'accepted', 'in_progress')
                  AND (
                      task.claimed_by IS NULL
                      OR task.claimed_by = %s
                      OR task.claim_expires_at <= now()
                  )
                  AND (
                      task.approval_grant_id IS NULL
                      OR (
                          grant_record.expires_at > now()
                          AND NOT EXISTS (
                              SELECT 1 FROM approval_grant_events AS grant_event
                              WHERE grant_event.approval_grant_id = task.approval_grant_id
                          )
                      )
                  )
                ORDER BY
                    CASE WHEN task.claimed_by = %s THEN 0 ELSE 1 END,
                    CASE task.state
                        WHEN 'in_progress' THEN 0
                        WHEN 'accepted' THEN 1
                        ELSE 2
                    END,
                    task.created_at,
                    task.coding_task_id
                LIMIT 1
                FOR UPDATE OF task SKIP LOCKED
                """,
                (root, consumer_id, consumer_id),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            task_id = str(row[0])
            self._renew_claim_locked(
                cursor,
                record=self._require_locked(cursor, task_id),
                consumer_id=consumer_id,
                previous_consumer=str(row[1]) if row[1] is not None else None,
                lease_expired=bool(row[2]),
                previous_expiry=cast(datetime | None, row[3]),
            )
        return self.view(task_id)

    def resume(
        self,
        *,
        coding_task_id: str,
        workspace_root: Path,
        consumer_id: str,
    ) -> dict[str, object]:
        root = str(workspace_root.resolve(strict=True))
        with self._connection.transaction(), self._connection.cursor() as cursor:
            record = self._require_locked(cursor, coding_task_id)
            if record.workspace_root != root:
                raise ValueError("Copilot Coding Task Workspace does not match Bridge delivery")
            if record.state in {"completed", "failed", "reanalysis_required", "cancelled"}:
                return self.view(coding_task_id)
            self._require_live_grant(cursor, record)
            cursor.execute(
                """
                SELECT claimed_by, COALESCE(claim_expires_at <= now(), false),
                       claim_expires_at
                FROM copilot_coding_tasks
                WHERE coding_task_id = %s
                """,
                (coding_task_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("Copilot Coding Task disappeared during resume")
            previous_consumer = str(row[0]) if row[0] is not None else None
            expired = bool(row[1])
            if previous_consumer not in {None, consumer_id} and not expired:
                raise ValueError("Copilot Coding Task is leased by another VS Code instance")
            self._renew_claim_locked(
                cursor,
                record=record,
                consumer_id=consumer_id,
                previous_consumer=previous_consumer,
                lease_expired=expired,
                previous_expiry=cast(datetime | None, row[2]),
            )
        return self.view(coding_task_id)

    def cancel(
        self,
        *,
        coding_task_id: str,
        actor: str,
        reason: str,
        idempotency_key: str,
        consumer_id: str | None = None,
    ) -> dict[str, object]:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            record = self._require_locked(cursor, coding_task_id)
            if record.state == "cancelled":
                self._append_event(
                    cursor,
                    record=record,
                    event_type="cancelled",
                    actor=actor,
                    idempotency_key=f"cancel:{idempotency_key}",
                    payload={"state": "cancelled", "reason": reason},
                )
            elif record.state in {"pending_confirmation", "accepted", "in_progress"}:
                if consumer_id is not None:
                    self._require_current_lease(cursor, record, consumer_id)
                cursor.execute(
                    """
                    UPDATE copilot_coding_tasks
                    SET state = 'cancelled', claimed_by = NULL, claimed_at = NULL,
                        claim_expires_at = NULL, updated_at = now()
                    WHERE coding_task_id = %s
                    """,
                    (coding_task_id,),
                )
                record = self._require_locked(cursor, coding_task_id)
                self._append_event(
                    cursor,
                    record=record,
                    event_type="cancelled",
                    actor=actor,
                    idempotency_key=f"cancel:{idempotency_key}",
                    payload={"state": "cancelled", "reason": reason},
                )
            else:
                raise ValueError(f"Copilot Coding Task cannot be cancelled from {record.state}")
        return self.view(coding_task_id)

    def accept(
        self,
        *,
        coding_task_id: str,
        workspace_root: Path,
        consumer_id: str,
        actor: str,
    ) -> dict[str, object]:
        root = str(workspace_root.resolve(strict=True))
        with self._connection.transaction(), self._connection.cursor() as cursor:
            record = self._require_locked(cursor, coding_task_id)
            if record.workspace_root != root:
                raise ValueError("Copilot Coding Task Workspace does not match Bridge delivery")
            self._require_current_lease(cursor, record, consumer_id)
            if record.state == "pending_confirmation":
                self._require_live_grant(cursor, record)
                cursor.execute(
                    """
                    UPDATE copilot_coding_tasks
                    SET state = 'accepted', accepted_by = %s, accepted_at = now(),
                        updated_at = now()
                    WHERE coding_task_id = %s
                    """,
                    (actor, coding_task_id),
                )
                record = self._require_locked(cursor, coding_task_id)
                self._append_event(
                    cursor,
                    record=record,
                    event_type="accepted",
                    actor=actor,
                    idempotency_key=f"accept:{consumer_id}",
                    payload={"state": "accepted"},
                )
            elif record.state not in {"accepted", "in_progress", "completed"}:
                raise ValueError(f"Copilot Coding Task cannot be accepted from {record.state}")
        return self.view(coding_task_id)

    def begin_mcp(
        self, *, coding_task_id: str, workspace_root: Path, actor: str
    ) -> CopilotCodingTaskRecord:
        root = str(workspace_root.resolve(strict=True))
        with self._connection.transaction(), self._connection.cursor() as cursor:
            record = self._require_locked(cursor, coding_task_id)
            if record.workspace_root != root:
                raise ValueError("Copilot Coding Task Workspace does not match MCP request")
            if record.state not in {"accepted", "in_progress"}:
                raise ValueError("Copilot Coding Task requires VS Code user confirmation")
            self._require_live_grant(cursor, record)
            cursor.execute(
                """
                UPDATE copilot_coding_tasks SET state = 'in_progress', updated_at = now()
                WHERE coding_task_id = %s
                """,
                (coding_task_id,),
            )
            record = self._require_locked(cursor, coding_task_id)
            self._append_event(
                cursor,
                record=record,
                event_type="context_loaded",
                actor=actor,
                idempotency_key=f"context:{actor}",
                payload={"state": "in_progress"},
            )
            return record

    def bind_command(
        self,
        *,
        coding_task_id: str,
        command_execution_id: str,
        actor: str,
        result: dict[str, object],
    ) -> None:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            record = self._require_locked(cursor, coding_task_id)
            if record.state != "in_progress":
                raise ValueError("Copilot Coding Task is not running")
            cursor.execute(
                """
                SELECT approval_grant_id, project_id, analysis_case_id, edit_packet_id
                FROM command_execution_requests
                WHERE command_execution_id = %s
                """,
                (command_execution_id,),
            )
            scope = cursor.fetchone()
            if scope is None or tuple(str(value) for value in scope) != (
                record.approval_grant_id,
                record.project_id,
                record.analysis_case_id,
                record.edit_packet_id,
            ):
                raise ValueError("Command Execution is outside Copilot Coding Task scope")
            cursor.execute(
                """
                INSERT INTO copilot_coding_task_commands (
                    coding_task_id, project_id, command_execution_id
                ) VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (coding_task_id, record.project_id, command_execution_id),
            )
            self._append_event(
                cursor,
                record=record,
                event_type="command_recorded",
                actor=actor,
                idempotency_key=f"command:{command_execution_id}",
                payload={
                    "command_execution_id": command_execution_id,
                    "command_ref": result.get("command_ref"),
                    "status": result.get("status"),
                    "exit_code": result.get("exit_code"),
                    "stdout_digest": result.get("stdout_digest"),
                    "stderr_digest": result.get("stderr_digest"),
                },
            )

    def bind_edit_result(
        self,
        *,
        coding_task_id: str,
        edit_result_id: str,
        actor: str,
        result: dict[str, object],
        committed: bool,
    ) -> None:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            record = self._require_locked(cursor, coding_task_id)
            if record.state not in {"in_progress", "reanalysis_required"}:
                raise ValueError("Copilot Coding Task is not accepting Edit Results")
            cursor.execute(
                """
                SELECT edit_packet_id, approval_grant_id, project_id, analysis_case_id,
                       validation_mode, status, tests_passed, command_evidence_status
                FROM edit_results
                WHERE edit_result_id = %s
                """,
                (edit_result_id,),
            )
            scope = cursor.fetchone()
            if scope is None or tuple(str(value) for value in scope[:4]) != (
                record.edit_packet_id,
                record.approval_grant_id,
                record.project_id,
                record.analysis_case_id,
            ):
                raise ValueError("Edit Result is outside Copilot Coding Task scope")
            expected_mode = "committed" if committed else "working"
            if str(scope[4]) != expected_mode:
                raise ValueError("Edit Result mode differs from Copilot Coding Task operation")
            cursor.execute(
                """
                INSERT INTO copilot_coding_task_edit_results (
                    coding_task_id, project_id, edit_result_id
                ) VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (coding_task_id, record.project_id, edit_result_id),
            )
            status = str(scope[5])
            if status == "out_of_scope":
                next_state = "reanalysis_required"
                event_type = "reanalysis_required"
            elif committed:
                successful = status == "in_scope" and scope[6] is True and scope[7] == "verified"
                next_state = "completed" if successful else "failed"
                event_type = "result_recorded" if successful else "failed"
            else:
                next_state = "in_progress"
                event_type = "diff_recorded"
            cursor.execute(
                """
                UPDATE copilot_coding_tasks SET state = %s, updated_at = now()
                WHERE coding_task_id = %s
                """,
                (next_state, coding_task_id),
            )
            record = self._require_locked(cursor, coding_task_id)
            self._append_event(
                cursor,
                record=record,
                event_type=event_type,
                actor=actor,
                idempotency_key=f"edit-result:{edit_result_id}",
                payload={
                    "edit_result_id": edit_result_id,
                    "validation_mode": expected_mode,
                    "status": result.get("status"),
                    "changed_paths": result.get("changed_paths", []),
                    "out_of_scope_files": result.get("out_of_scope_files", []),
                    "tests_passed": scope[6],
                    "command_evidence_status": scope[7],
                    "state": next_state,
                },
            )

    def record_change_outputs(
        self,
        *,
        coding_task_id: str,
        actor: str,
        output_stage: str,
        expected_stage: str,
        next_stage: str,
        output_refs: dict[str, object],
    ) -> None:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            record = self._require_locked(cursor, coding_task_id)
            if record.state != "in_progress":
                raise ValueError("Copilot Change Task is not accepting outputs")
            if record.current_stage not in {expected_stage, next_stage}:
                raise ValueError(
                    "Copilot Change Task output is out of order: "
                    f"expected {expected_stage}, current {record.current_stage}"
                )
            payload = {"output_stage": output_stage, **output_refs}
            self._append_event(
                cursor,
                record=record,
                event_type="outputs_recorded",
                actor=actor,
                idempotency_key=f"outputs:{output_stage}",
                payload=payload,
            )
            if record.current_stage == expected_stage and next_stage != expected_stage:
                cursor.execute(
                    """
                    UPDATE copilot_coding_tasks
                    SET current_stage = %s, updated_at = now()
                    WHERE coding_task_id = %s
                    """,
                    (next_stage, coding_task_id),
                )

    def bind_execution_scope(
        self,
        *,
        coding_task_id: str,
        analysis_case_id: str,
        repository_id: str,
        edit_packet_id: str,
        approval_grant_id: str,
        base_repository_revision: str,
        actor: str,
    ) -> CopilotCodingTaskRecord:
        values = (
            analysis_case_id,
            repository_id,
            edit_packet_id,
            approval_grant_id,
            base_repository_revision,
            actor,
        )
        if any(not value.strip() for value in values):
            raise ValueError("Copilot Change Task execution scope must not be blank")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            record = self._require_locked(cursor, coding_task_id)
            if record.current_stage == "document_change":
                raise ValueError(
                    "Copilot Change Task execution scope requires recorded code scope"
                )
            cursor.execute(
                """
                SELECT 1
                FROM copilot_coding_task_events
                WHERE coding_task_id = %s
                  AND event_type = 'outputs_recorded'
                  AND payload ->> 'output_stage' = 'code_scope'
                LIMIT 1
                """,
                (coding_task_id,),
            )
            if cursor.fetchone() is None:
                raise ValueError(
                    "Copilot Change Task execution scope requires recorded code scope"
                )
            requested = (
                analysis_case_id,
                repository_id,
                edit_packet_id,
                approval_grant_id,
                base_repository_revision,
            )
            existing = (
                record.analysis_case_id,
                record.repository_id,
                record.edit_packet_id,
                record.approval_grant_id,
                record.base_repository_revision,
            )
            if all(value is None for value in existing):
                cursor.execute(
                    """
                    UPDATE copilot_coding_tasks
                    SET analysis_case_id = %s, repository_id = %s,
                        edit_packet_id = %s, approval_grant_id = %s,
                        base_repository_revision = %s,
                        current_stage = 'compile_test', updated_at = now()
                    WHERE coding_task_id = %s
                    """,
                    (*requested, coding_task_id),
                )
                record = self._require_locked(cursor, coding_task_id)
            elif existing != requested:
                raise PersistenceConflictError(
                    "Copilot Change Task execution scope is already bound differently"
                )
            elif record.current_stage == "code_scope":
                cursor.execute(
                    """
                    UPDATE copilot_coding_tasks
                    SET current_stage = 'compile_test', updated_at = now()
                    WHERE coding_task_id = %s
                    """,
                    (coding_task_id,),
                )
                record = self._require_locked(cursor, coding_task_id)
            self._append_event(
                cursor,
                record=record,
                event_type="scope_bound",
                actor=actor,
                idempotency_key=f"scope:{edit_packet_id}:{approval_grant_id}",
                payload={
                    "analysis_case_id": analysis_case_id,
                    "repository_id": repository_id,
                    "edit_packet_id": edit_packet_id,
                    "approval_grant_id": approval_grant_id,
                    "base_repository_revision": base_repository_revision,
                    "current_stage": "compile_test",
                },
            )
        return record

    def get(self, coding_task_id: str) -> CopilotCodingTaskRecord:
        with self._connection.cursor() as cursor:
            record = self._get_locked(cursor, coding_task_id, lock=False)
        if record is None:
            raise ValueError("Copilot Coding Task does not exist")
        return record

    def latest_for_request(self, change_request_id: str) -> dict[str, object] | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT coding_task_id
                FROM copilot_coding_tasks
                WHERE change_request_id = %s
                ORDER BY
                    CASE state
                        WHEN 'in_progress' THEN 0
                        WHEN 'accepted' THEN 1
                        WHEN 'pending_confirmation' THEN 2
                        WHEN 'completed' THEN 3
                        ELSE 4
                    END,
                    attempt_number DESC,
                    created_at DESC,
                    coding_task_id DESC
                LIMIT 1
                """,
                (change_request_id,),
            )
            row = cursor.fetchone()
        return self.view(str(row[0])) if row is not None else None

    def view(self, coding_task_id: str) -> dict[str, object]:
        record = self.get(coding_task_id)
        artifact = self._artifacts.get(coding_task_id)
        if artifact is None or artifact.get("artifact_type") != "CopilotCodingTask":
            raise RuntimeError("Copilot Coding Task has no immutable Artifact")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT event_type, actor, payload, created_at
                FROM copilot_coding_task_events
                WHERE coding_task_id = %s AND project_id = %s
                ORDER BY event_sequence
                """,
                (coding_task_id, record.project_id),
            )
            events = cursor.fetchall()
            cursor.execute(
                """
                SELECT request.command_execution_id, request.command_ref,
                       result.status, result.exit_code, result.stdout_digest,
                       result.stderr_digest, result.completed_at
                FROM copilot_coding_task_commands AS task_command
                JOIN command_execution_requests AS request
                  ON request.command_execution_id = task_command.command_execution_id
                 AND request.project_id = task_command.project_id
                LEFT JOIN command_execution_results AS result
                  ON result.command_execution_id = request.command_execution_id
                 AND result.project_id = request.project_id
                WHERE task_command.coding_task_id = %s
                ORDER BY task_command.recorded_at, request.command_execution_id
                """,
                (coding_task_id,),
            )
            commands = cursor.fetchall()
            cursor.execute(
                """
                SELECT result.edit_result_id, result.validation_mode, result.status,
                       result.changed_paths, result.out_of_scope_files,
                       result.test_result_refs, result.tests_passed,
                       result.command_evidence_status, result.result_repository_revision,
                       result.recorded_at
                FROM copilot_coding_task_edit_results AS task_result
                JOIN edit_results AS result
                  ON result.edit_result_id = task_result.edit_result_id
                 AND result.project_id = task_result.project_id
                JOIN copilot_coding_task_events AS result_event
                  ON result_event.coding_task_id = task_result.coding_task_id
                 AND result_event.project_id = task_result.project_id
                 AND result_event.payload ->> 'edit_result_id' = result.edit_result_id
                WHERE task_result.coding_task_id = %s
                ORDER BY result_event.event_sequence
                """,
                (coding_task_id,),
            )
            edit_results = cursor.fetchall()
        return {
            "task": artifact,
            "state": record.state,
            "claimed_by": record.claimed_by,
            "claim_expires_at": (
                record.claim_expires_at.isoformat() if record.claim_expires_at is not None else None
            ),
            "accepted_by": record.accepted_by,
            "retry_of_coding_task_id": record.retry_of_coding_task_id,
            "attempt_number": record.attempt_number,
            "current_stage": record.current_stage,
            "execution_scope": {
                "analysis_case_id": record.analysis_case_id,
                "repository_id": record.repository_id,
                "edit_packet_id": record.edit_packet_id,
                "approval_grant_id": record.approval_grant_id,
                "base_repository_revision": record.base_repository_revision,
                "bound": record.approval_grant_id is not None,
            },
            "commands": [
                {
                    "command_execution_id": str(row[0]),
                    "command_ref": str(row[1]),
                    "status": str(row[2]) if row[2] is not None else "running",
                    "exit_code": int(row[3]) if row[3] is not None else None,
                    "stdout_digest": str(row[4]) if row[4] is not None else None,
                    "stderr_digest": str(row[5]) if row[5] is not None else None,
                    "completed_at": row[6].isoformat() if row[6] is not None else None,
                }
                for row in commands
            ],
            "edit_results": [
                {
                    "edit_result_id": str(row[0]),
                    "validation_mode": str(row[1]),
                    "status": str(row[2]),
                    "changed_paths": list(cast(list[object], row[3])),
                    "out_of_scope_files": list(cast(list[object], row[4])),
                    "test_result_refs": list(cast(list[object], row[5])),
                    "tests_passed": row[6],
                    "command_evidence_status": str(row[7]),
                    "result_repository_revision": str(row[8]) if row[8] is not None else None,
                    "recorded_at": row[9].isoformat(),
                }
                for row in edit_results
            ],
            "events": [
                {
                    "event_type": str(row[0]),
                    "actor": str(row[1]),
                    "payload": cast(dict[str, object], row[2]),
                    "created_at": row[3].isoformat(),
                }
                for row in events
            ],
        }

    @staticmethod
    def _get_locked(
        cursor: Cursor[Any], coding_task_id: str, *, lock: bool = True
    ) -> CopilotCodingTaskRecord | None:
        query = (
            """
            SELECT coding_task_id, project_id, change_request_id, analysis_case_id,
                   repository_id, edit_packet_id, approval_grant_id,
                   base_repository_revision, execution_mode, provider_route,
                   provider_id, workspace_root, state, claimed_by,
                   claim_expires_at, accepted_by, retry_of_coding_task_id,
                   attempt_number, current_stage
            FROM copilot_coding_tasks
            WHERE coding_task_id = %s
            """,
            " FOR UPDATE" if lock else "",
        )
        cursor.execute("".join(query), (coding_task_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        return CopilotCodingTaskRecord(
            coding_task_id=str(row[0]),
            project_id=str(row[1]),
            change_request_id=str(row[2]),
            analysis_case_id=_optional_text(row[3]),
            repository_id=_optional_text(row[4]),
            edit_packet_id=_optional_text(row[5]),
            approval_grant_id=_optional_text(row[6]),
            base_repository_revision=_optional_text(row[7]),
            execution_mode=str(row[8]),
            provider_route=str(row[9]),
            provider_id=str(row[10]),
            workspace_root=str(row[11]),
            state=str(row[12]),
            claimed_by=str(row[13]) if row[13] is not None else None,
            claim_expires_at=cast(datetime | None, row[14]),
            accepted_by=str(row[15]) if row[15] is not None else None,
            retry_of_coding_task_id=str(row[16]) if row[16] is not None else None,
            attempt_number=int(row[17]),
            current_stage=str(row[18]),
        )

    @classmethod
    def _require_locked(cls, cursor: Cursor[Any], coding_task_id: str) -> CopilotCodingTaskRecord:
        record = cls._get_locked(cursor, coding_task_id)
        if record is None:
            raise ValueError("Copilot Coding Task does not exist")
        return record

    @staticmethod
    def _payload_digest(cursor: Cursor[Any], coding_task_id: str) -> str:
        cursor.execute(
            "SELECT payload_digest FROM copilot_coding_tasks WHERE coding_task_id = %s",
            (coding_task_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Copilot Coding Task payload digest disappeared")
        return str(row[0])

    @classmethod
    def _renew_claim_locked(
        cls,
        cursor: Cursor[Any],
        *,
        record: CopilotCodingTaskRecord,
        consumer_id: str,
        previous_consumer: str | None,
        lease_expired: bool,
        previous_expiry: datetime | None,
    ) -> None:
        recovered = previous_consumer is not None and (
            previous_consumer != consumer_id or lease_expired
        )
        cursor.execute(
            """
            UPDATE copilot_coding_tasks
            SET claimed_by = %s,
                claimed_at = CASE
                    WHEN claimed_by IS NULL
                      OR claimed_by <> %s
                      OR claim_expires_at <= now()
                    THEN now()
                    ELSE claimed_at
                END,
                claim_expires_at = now() + make_interval(secs => %s),
                updated_at = now()
            WHERE coding_task_id = %s
            """,
            (consumer_id, consumer_id, CLAIM_LEASE_SECONDS, record.coding_task_id),
        )
        renewed = cls._require_locked(cursor, record.coding_task_id)
        if previous_consumer is None:
            cls._append_event(
                cursor,
                record=renewed,
                event_type="claimed",
                actor=consumer_id,
                idempotency_key=f"claim:{consumer_id}",
                payload={"state": renewed.state},
            )
        elif recovered:
            expiry_key = previous_expiry.isoformat() if previous_expiry is not None else "missing"
            cls._append_event(
                cursor,
                record=renewed,
                event_type="claim_recovered",
                actor=consumer_id,
                idempotency_key=f"claim-recovered:{consumer_id}:{expiry_key}",
                payload={
                    "state": renewed.state,
                    "previous_consumer": previous_consumer,
                },
            )

    @staticmethod
    def _require_current_lease(
        cursor: Cursor[Any], record: CopilotCodingTaskRecord, consumer_id: str
    ) -> None:
        cursor.execute(
            """
            SELECT claimed_by = %s AND claim_expires_at > now()
            FROM copilot_coding_tasks
            WHERE coding_task_id = %s
            """,
            (consumer_id, record.coding_task_id),
        )
        row = cursor.fetchone()
        if row is None or row[0] is not True:
            raise ValueError("Copilot Coding Task lease is not held by this VS Code instance")

    @staticmethod
    def _require_live_grant(cursor: Cursor[Any], record: CopilotCodingTaskRecord) -> None:
        scope = (
            record.analysis_case_id,
            record.repository_id,
            record.edit_packet_id,
            record.approval_grant_id,
            record.base_repository_revision,
        )
        if all(value is None for value in scope) and record.execution_mode == "copilot_change_task":
            return
        if any(value is None for value in scope):
            raise PersistenceConflictError("Copilot Change Task execution scope is incomplete")
        cursor.execute(
            """
            SELECT grant_record.expires_at > now()
                   AND NOT EXISTS (
                       SELECT 1 FROM approval_grant_events AS event
                       WHERE event.approval_grant_id = grant_record.approval_grant_id
                   )
            FROM approval_grants AS grant_record
            WHERE grant_record.approval_grant_id = %s
              AND grant_record.project_id = %s
              AND grant_record.analysis_case_id = %s
              AND grant_record.edit_packet_id = %s
            FOR SHARE
            """,
            (
                record.approval_grant_id,
                record.project_id,
                record.analysis_case_id,
                record.edit_packet_id,
            ),
        )
        row = cursor.fetchone()
        if row is None or row[0] is not True:
            raise ValueError("Copilot Coding Task Approval Grant is no longer active")

    @staticmethod
    def _append_event(
        cursor: Cursor[Any],
        *,
        record: CopilotCodingTaskRecord,
        event_type: str,
        actor: str,
        idempotency_key: str,
        payload: dict[str, object],
    ) -> None:
        canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        normalized_payload = cast(dict[str, object], json.loads(canonical))
        event_id = (
            "copilot-task-event:"
            + hashlib.sha256(f"{record.coding_task_id}\0{idempotency_key}".encode()).hexdigest()
        )
        cursor.execute(
            """
            INSERT INTO copilot_coding_task_events (
                coding_task_event_id, coding_task_id, project_id,
                event_type, actor, idempotency_key, payload
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT DO NOTHING
            """,
            (
                event_id,
                record.coding_task_id,
                record.project_id,
                event_type,
                actor,
                idempotency_key,
                canonical,
            ),
        )
        cursor.execute(
            """
            SELECT event_type, actor, payload
            FROM copilot_coding_task_events
            WHERE coding_task_id = %s AND idempotency_key = %s
            """,
            (record.coding_task_id, idempotency_key),
        )
        stored = cursor.fetchone()
        if stored is None or tuple(stored) != (event_type, actor, normalized_payload):
            raise PersistenceConflictError("Copilot Coding Task event idempotency payload differs")


def _optional_text(value: object) -> str | None:
    return str(value) if value is not None else None
