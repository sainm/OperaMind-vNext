"""PostgreSQL event ledger for resumable change automation runs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row

from operamind.infrastructure.postgres.errors import PersistenceConflictError


@dataclass(frozen=True, slots=True)
class ChangeAutomationRunRecord:
    automation_run_id: str
    change_request_id: str
    project_id: str
    status: str
    current_stage: str
    next_action: str | None
    blocking_reason: str | None
    created: bool
    superseded_run_ids: tuple[str, ...] = ()


class ChangeAutomationRepository:
    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def start(
        self,
        *,
        run_id: str,
        request_id: str,
        project_id: str,
        idempotency_key: str,
        actor: str,
    ) -> ChangeAutomationRunRecord:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT project_id FROM change_requests
                WHERE change_request_id = %s FOR UPDATE
                """,
                (request_id,),
            )
            request = cursor.fetchone()
            if request is None:
                raise ValueError("Change Request does not exist")
            if str(request[0]) != project_id:
                raise PersistenceConflictError(
                    "Change Automation project differs from Change Request"
                )
            cursor.execute(
                """
                INSERT INTO change_automation_runs (
                    automation_run_id, change_request_id, project_id,
                    idempotency_key, status, current_stage, next_action, created_by
                ) VALUES (%s, %s, %s, %s, 'running', 'requirement_confirmation',
                          'inspect_canonical_state', %s)
                ON CONFLICT DO NOTHING
                """,
                (run_id, request_id, project_id, idempotency_key, actor),
            )
            created = cursor.rowcount == 1
            superseded_run_ids = (
                self._supersede_prior_runs(
                    cursor,
                    request_id=request_id,
                    current_run_id=run_id,
                    actor=actor,
                )
                if created
                else ()
            )
            cursor.execute(
                """
                SELECT automation_run_id, change_request_id, project_id, status,
                       current_stage, next_action, blocking_reason, created_by,
                       idempotency_key
                FROM change_automation_runs
                WHERE change_request_id = %s AND idempotency_key = %s
                """,
                (request_id, idempotency_key),
            )
            row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Change Automation Run disappeared during creation")
        if (str(row[0]), str(row[2]), str(row[7]), str(row[8])) != (
            run_id,
            project_id,
            actor,
            idempotency_key,
        ):
            raise PersistenceConflictError(
                "Change Automation idempotency identity has different content"
            )
        return ChangeAutomationRunRecord(
            str(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3]),
            str(row[4]),
            str(row[5]) if row[5] is not None else None,
            str(row[6]) if row[6] is not None else None,
            created,
            superseded_run_ids,
        )

    def transition(
        self,
        *,
        run_id: str,
        actor: str,
        stage: str,
        status: str,
        next_action: str | None,
        blocking_reason: str | None,
        message: str,
        artifact_refs: tuple[str, ...] = (),
    ) -> ChangeAutomationRunRecord:
        payload = {
            "run_id": run_id,
            "stage": stage,
            "status": status,
            "next_action": next_action,
            "blocking_reason": blocking_reason,
            "message": message,
            "artifact_refs": list(artifact_refs),
        }
        digest = hashlib.sha256(_json(payload).encode()).hexdigest()
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT change_request_id, project_id, status, current_stage,
                       next_action, blocking_reason
                FROM change_automation_runs
                WHERE automation_run_id = %s
                FOR UPDATE
                """,
                (run_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("Change Automation Run does not exist")
            project_id = str(row[1])
            current = (
                str(row[2]),
                str(row[3]),
                str(row[4]) if row[4] is not None else None,
                str(row[5]) if row[5] is not None else None,
            )
            target = (status, stage, next_action, blocking_reason)
            if current != target:
                cursor.execute(
                    """
                    SELECT COALESCE(max(sequence), 0) + 1
                    FROM change_automation_events
                    WHERE automation_run_id = %s
                    """,
                    (run_id,),
                )
                sequence_row = cursor.fetchone()
                if sequence_row is None:
                    raise RuntimeError("Change Automation sequence query returned no row")
                sequence = int(sequence_row[0])
                event_id = f"{run_id}-event-{sequence:04d}"
                cursor.execute(
                    """
                    INSERT INTO change_automation_events (
                        event_id, automation_run_id, project_id, sequence,
                        event_type, stage, status, actor, message,
                        artifact_refs, payload_digest
                    ) VALUES (%s, %s, %s, %s, 'transition', %s, %s, %s, %s,
                              %s::jsonb, %s)
                    """,
                    (
                        event_id,
                        run_id,
                        project_id,
                        sequence,
                        stage,
                        status,
                        actor,
                        message,
                        _json(list(artifact_refs)),
                        digest,
                    ),
                )
                cursor.execute(
                    """
                    UPDATE change_automation_runs
                    SET status = %s, current_stage = %s, next_action = %s,
                        blocking_reason = %s, updated_at = now()
                    WHERE automation_run_id = %s
                    """,
                    (status, stage, next_action, blocking_reason, run_id),
                )
        return self.get(run_id, created=False)

    def latest_for_request(self, request_id: str) -> dict[str, object] | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT automation_run_id
                FROM change_automation_runs
                WHERE change_request_id = %s
                  AND status <> 'superseded'
                ORDER BY updated_at DESC, automation_run_id DESC
                LIMIT 1
                """,
                (request_id,),
            )
            row = cursor.fetchone()
        return self.view(str(row[0])) if row is not None else None

    def _supersede_prior_runs(
        self,
        cursor: Any,
        *,
        request_id: str,
        current_run_id: str,
        actor: str,
    ) -> tuple[str, ...]:
        cursor.execute(
            """
            SELECT automation_run_id, project_id
            FROM change_automation_runs
            WHERE change_request_id = %s
              AND automation_run_id <> %s
              AND status IN ('running', 'waiting', 'blocked')
            FOR UPDATE
            """,
            (request_id, current_run_id),
        )
        superseded: list[str] = []
        for prior_run_id, project_id in cursor.fetchall():
            prior_id = str(prior_run_id)
            superseded.append(prior_id)
            cursor.execute(
                """
                SELECT COALESCE(max(sequence), 0) + 1
                FROM change_automation_events
                WHERE automation_run_id = %s
                """,
                (prior_id,),
            )
            sequence_row = cursor.fetchone()
            if sequence_row is None:
                raise RuntimeError("Change Automation supersession sequence query returned no row")
            sequence = int(sequence_row[0])
            message = "新しい変更フローに引き継いだため、旧フローを終了しました。"
            payload: dict[str, object] = {
                "run_id": prior_id,
                "stage": "superseded",
                "status": "superseded",
                "next_action": None,
                "blocking_reason": None,
                "message": message,
                "artifact_refs": [],
            }
            cursor.execute(
                """
                INSERT INTO change_automation_events (
                    event_id, automation_run_id, project_id, sequence,
                    event_type, stage, status, actor, message,
                    artifact_refs, payload_digest
                ) VALUES (%s, %s, %s, %s, 'superseded', 'superseded',
                          'superseded', %s, %s, '[]'::jsonb, %s)
                """,
                (
                    f"{prior_id}-event-{sequence:04d}",
                    prior_id,
                    str(project_id),
                    sequence,
                    actor,
                    message,
                    hashlib.sha256(_json(payload).encode()).hexdigest(),
                ),
            )
            cursor.execute(
                """
                UPDATE change_automation_runs
                SET status = 'superseded', current_stage = 'superseded',
                    next_action = NULL, blocking_reason = NULL, updated_at = now()
                WHERE automation_run_id = %s
                """,
                (prior_id,),
            )
        return tuple(superseded)

    def latest_confirmation_for_project(self, project_id: str) -> dict[str, object] | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT active.automation_run_id
                FROM (
                    SELECT run.automation_run_id, run.status, run.next_action
                    FROM change_automation_runs AS run
                    JOIN change_requests AS request
                      ON request.change_request_id = run.change_request_id
                     AND request.project_id = run.project_id
                    WHERE run.project_id = %s
                      AND run.status <> 'superseded'
                    ORDER BY request.submitted_at DESC,
                             run.updated_at DESC,
                             run.automation_run_id DESC
                    LIMIT 1
                ) AS active
                WHERE active.status = 'waiting'
                  AND active.next_action LIKE 'confirm_%%'
                """,
                (project_id,),
            )
            row = cursor.fetchone()
        return self.view(str(row[0])) if row is not None else None

    def record_confirmation(
        self,
        *,
        confirmation_id: str,
        run_id: str,
        checkpoint: str,
        subject_digest: str,
        decision: str,
        surface: str,
        actor: str,
        note: str | None,
    ) -> dict[str, object]:
        """Append one subject-bound decision shared by Web and VS Code."""
        if decision not in {"confirmed", "rejected"}:
            raise ValueError("Change checkpoint decision is invalid")
        if surface not in {"web", "vscode_copilot"}:
            raise ValueError("Change checkpoint surface is invalid")
        if len(subject_digest) != 64 or any(
            character not in "0123456789abcdef" for character in subject_digest
        ):
            raise ValueError("Change checkpoint subject_digest must be SHA-256")
        normalized_note = note.strip() if isinstance(note, str) and note.strip() else None
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT project_id FROM change_automation_runs
                WHERE automation_run_id = %s FOR SHARE
                """,
                (run_id,),
            )
            run = cursor.fetchone()
            if run is None:
                raise ValueError("Change Automation Run does not exist")
            project_id = str(run[0])
            cursor.execute(
                """
                INSERT INTO change_checkpoint_confirmations (
                    confirmation_id, automation_run_id, project_id, checkpoint,
                    subject_digest, decision, surface, actor, note
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    confirmation_id,
                    run_id,
                    project_id,
                    checkpoint,
                    subject_digest,
                    decision,
                    surface,
                    actor,
                    normalized_note,
                ),
            )
            created = cursor.rowcount == 1
            cursor.execute(
                """
                SELECT automation_run_id, project_id, checkpoint, subject_digest,
                       decision, surface, actor, note, created_at
                FROM change_checkpoint_confirmations
                WHERE confirmation_id = %s
                """,
                (confirmation_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Change checkpoint confirmation disappeared")
        actual = (
            *(str(row[index]) for index in range(7)),
            str(row[7]) if row[7] is not None else None,
        )
        expected = (
            run_id,
            project_id,
            checkpoint,
            subject_digest,
            decision,
            surface,
            actor,
            normalized_note,
        )
        if actual != expected:
            raise PersistenceConflictError(
                "Change checkpoint confirmation identity has different content"
            )
        return {
            "confirmation_id": confirmation_id,
            "checkpoint": checkpoint,
            "subject_digest": subject_digest,
            "decision": decision,
            "surface": surface,
            "actor": actor,
            "note": normalized_note,
            "created_at": _time(row[8]),
            "created": created,
        }

    def record_rag_discovery(
        self, *, run_id: str, discovery: dict[str, object]
    ) -> dict[str, object]:
        """Persist the immutable RAG candidate set shown at confirmation time."""
        if discovery.get("status") != "ready":
            raise ValueError("Only ready RAG discovery evidence can be persisted")
        discovery_text = _json(discovery)
        subject_digest = hashlib.sha256(discovery_text.encode()).hexdigest()
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT project_id FROM change_automation_runs
                WHERE automation_run_id = %s FOR SHARE
                """,
                (run_id,),
            )
            run = cursor.fetchone()
            if run is None:
                raise ValueError("Change Automation Run does not exist")
            project_id = str(run[0])
            cursor.execute(
                """
                INSERT INTO change_automation_rag_discoveries (
                    automation_run_id, project_id, discovery, subject_digest
                ) VALUES (%s, %s, %s::jsonb, %s)
                ON CONFLICT DO NOTHING
                """,
                (run_id, project_id, discovery_text, subject_digest),
            )
            created = cursor.rowcount == 1
            cursor.execute(
                """
                SELECT project_id, discovery, subject_digest, created_at
                FROM change_automation_rag_discoveries
                WHERE automation_run_id = %s
                """,
                (run_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise RuntimeError("RAG discovery evidence disappeared")
        stored_discovery = row[1]
        if not isinstance(stored_discovery, dict):
            raise PersistenceConflictError("RAG discovery evidence lost its object shape")
        if (
            str(row[0]) != project_id
            or _json(stored_discovery) != discovery_text
            or str(row[2]) != subject_digest
        ):
            raise PersistenceConflictError("RAG discovery evidence differs for this run")
        return {
            "discovery": stored_discovery,
            "subject_digest": subject_digest,
            "created_at": _time(row[3]),
            "created": created,
        }

    def rag_discovery(self, run_id: str) -> dict[str, object] | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT discovery, subject_digest
                FROM change_automation_rag_discoveries
                WHERE automation_run_id = %s
                """,
                (run_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        discovery = row[0]
        if not isinstance(discovery, dict):
            raise PersistenceConflictError("RAG discovery evidence lost its object shape")
        expected_digest = hashlib.sha256(_json(discovery).encode()).hexdigest()
        if str(row[1]) != expected_digest:
            raise PersistenceConflictError("RAG discovery evidence digest differs")
        return discovery

    def current_confirmations(
        self, *, run_id: str, subject_digests: dict[str, str]
    ) -> dict[str, dict[str, object]]:
        """Return only latest decisions that still match current evidence."""
        if not subject_digests:
            return {}
        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT DISTINCT ON (checkpoint)
                       confirmation_id, checkpoint, subject_digest, decision,
                       surface, actor, note, created_at, sequence
                FROM change_checkpoint_confirmations
                WHERE automation_run_id = %s
                ORDER BY checkpoint, sequence DESC
                """,
                (run_id,),
            )
            rows = cursor.fetchall()
        return {
            str(row["checkpoint"]): {
                "confirmation_id": str(row["confirmation_id"]),
                "checkpoint": str(row["checkpoint"]),
                "subject_digest": str(row["subject_digest"]),
                "decision": str(row["decision"]),
                "surface": str(row["surface"]),
                "actor": str(row["actor"]),
                "note": row["note"],
                "created_at": _time(row["created_at"]),
            }
            for row in rows
            if subject_digests.get(str(row["checkpoint"])) == str(row["subject_digest"])
        }

    def latest_confirmation(
        self, *, run_id: str, checkpoint: str
    ) -> dict[str, object] | None:
        """Return the latest raw checkpoint decision for recovery reconciliation."""
        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT confirmation_id, checkpoint, subject_digest, decision,
                       surface, actor, note, created_at
                FROM change_checkpoint_confirmations
                WHERE automation_run_id = %s AND checkpoint = %s
                ORDER BY sequence DESC
                LIMIT 1
                """,
                (run_id, checkpoint),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return {
            "confirmation_id": str(row["confirmation_id"]),
            "checkpoint": str(row["checkpoint"]),
            "subject_digest": str(row["subject_digest"]),
            "decision": str(row["decision"]),
            "surface": str(row["surface"]),
            "actor": str(row["actor"]),
            "note": row["note"],
            "created_at": _time(row["created_at"]),
        }

    def get(self, run_id: str, *, created: bool = False) -> ChangeAutomationRunRecord:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT automation_run_id, change_request_id, project_id, status,
                       current_stage, next_action, blocking_reason
                FROM change_automation_runs WHERE automation_run_id = %s
                """,
                (run_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise ValueError("Change Automation Run does not exist")
        return ChangeAutomationRunRecord(
            str(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3]),
            str(row[4]),
            str(row[5]) if row[5] is not None else None,
            str(row[6]) if row[6] is not None else None,
            created,
        )

    def view(self, run_id: str) -> dict[str, object]:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT * FROM change_automation_runs WHERE automation_run_id = %s
                """,
                (run_id,),
            )
            run = cursor.fetchone()
            if run is None:
                raise ValueError("Change Automation Run does not exist")
            cursor.execute(
                """
                SELECT sequence, event_type, stage, status, actor, message,
                       artifact_refs, created_at
                FROM change_automation_events
                WHERE automation_run_id = %s
                ORDER BY sequence
                """,
                (run_id,),
            )
            events = cursor.fetchall()
        return {
            "automation_run_id": str(run["automation_run_id"]),
            "change_request_id": str(run["change_request_id"]),
            "project_id": str(run["project_id"]),
            "status": str(run["status"]),
            "current_stage": str(run["current_stage"]),
            "next_action": run["next_action"],
            "blocking_reason": run["blocking_reason"],
            "created_by": str(run["created_by"]),
            "created_at": _time(run["created_at"]),
            "updated_at": _time(run["updated_at"]),
            "events": [
                {
                    "sequence": int(event["sequence"]),
                    "event_type": str(event["event_type"]),
                    "stage": str(event["stage"]),
                    "status": str(event["status"]),
                    "actor": str(event["actor"]),
                    "message": str(event["message"]),
                    "artifact_refs": list(event["artifact_refs"]),
                    "created_at": _time(event["created_at"]),
                }
                for event in events
            ],
        }


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _time(value: datetime) -> str:
    return value.isoformat()
