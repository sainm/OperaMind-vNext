"""Canonical persistence and bounded read models for the Web control plane."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row

from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres.artifact_repository import ArtifactRepository
from operamind.infrastructure.postgres.errors import PersistenceConflictError


@dataclass(frozen=True, slots=True)
class ChangeRequestRecord:
    change_request_id: str
    project_id: str
    analysis_case_id: str | None
    input_mode: str
    submitted_by: str
    submitted_at: datetime
    created: bool


@dataclass(frozen=True, slots=True)
class DocumentReviewRecord:
    review_event_id: str
    decision: str
    actor: str
    created_at: datetime
    created: bool


class WebControlPlaneRepository:
    """Keep Web writes canonical and Web reads explicitly bounded."""

    def __init__(self, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._connection = connection
        self._artifacts = ArtifactRepository(connection, contracts)

    def submit_change_request(
        self,
        *,
        artifact: dict[str, Any],
        analysis_case_id: str | None,
        submitted_by: str,
    ) -> ChangeRequestRecord:
        request_id = str(artifact["change_request_id"])
        project_id = str(artifact["project_id"])
        input_mode = str(artifact["input_mode"])
        if not submitted_by.strip():
            raise ValueError("Change Request submitter must not be blank")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            self._artifacts.store(
                artifact_id=request_id,
                project_id=project_id,
                analysis_case_id=None,
                artifact=artifact,
            )
            cursor.execute(
                """
                INSERT INTO change_requests (
                    change_request_id, project_id, analysis_case_id,
                    input_mode, submitted_by
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (request_id, project_id, analysis_case_id, input_mode, submitted_by),
            )
            created = cursor.rowcount == 1
            cursor.execute(
                """
                SELECT project_id, analysis_case_id, input_mode,
                       submitted_by, submitted_at
                FROM change_requests
                WHERE change_request_id = %s
                """,
                (request_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Change Request disappeared during submission")
        identity_matches = (
            str(row[0]) == project_id
            and str(row[2]) == input_mode
            and str(row[3]) == submitted_by
            and (row[1] == analysis_case_id or analysis_case_id is None)
        )
        if not identity_matches:
            raise PersistenceConflictError(
                "Change Request identity has different normalized content"
            )
        return ChangeRequestRecord(
            change_request_id=request_id,
            project_id=project_id,
            analysis_case_id=str(row[1]) if row[1] is not None else None,
            input_mode=input_mode,
            submitted_by=submitted_by,
            submitted_at=row[4],
            created=created,
        )

    def get_change_request(self, request_id: str) -> dict[str, object]:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT request.project_id, request.analysis_case_id, request.input_mode,
                       request.submitted_by, request.submitted_at,
                       review.decision AS document_review_status,
                       review.actor AS document_reviewed_by,
                       review.created_at AS document_reviewed_at
                FROM change_requests AS request
                LEFT JOIN LATERAL (
                    SELECT decision, actor, created_at
                    FROM change_request_review_events
                    WHERE change_request_id = request.change_request_id
                      AND project_id = request.project_id
                      AND review_step = 'document_diff'
                    ORDER BY created_at DESC, review_event_id DESC
                    LIMIT 1
                ) AS review ON true
                WHERE request.change_request_id = %s
                """,
                (request_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise ValueError("Change Request does not exist")
        artifact = self._artifacts.get(request_id)
        if artifact is None or artifact.get("artifact_type") != "ChangeRequest":
            raise PersistenceConflictError("Change Request has no immutable Artifact")
        return {
            "artifact": artifact,
            "project_id": str(row["project_id"]),
            "analysis_case_id": (
                str(row["analysis_case_id"]) if row["analysis_case_id"] is not None else None
            ),
            "input_mode": str(row["input_mode"]),
            "submitted_by": str(row["submitted_by"]),
            "submitted_at": row["submitted_at"].isoformat(),
            "document_review": {
                "status": (
                    str(row["document_review_status"])
                    if row["document_review_status"] is not None
                    else "pending"
                ),
                "actor": (
                    str(row["document_reviewed_by"])
                    if row["document_reviewed_by"] is not None
                    else None
                ),
                "reviewed_at": (
                    row["document_reviewed_at"].isoformat()
                    if row["document_reviewed_at"] is not None
                    else None
                ),
            },
        }

    def bind_analysis_case(
        self,
        *,
        event_id: str,
        request_id: str,
        project_id: str,
        case_id: str,
        idempotency_key: str,
        actor: str,
    ) -> bool:
        payload = {
            "event_id": event_id,
            "change_request_id": request_id,
            "project_id": project_id,
            "analysis_case_id": case_id,
            "idempotency_key": idempotency_key,
            "actor": actor,
        }
        digest = hashlib.sha256(_json(payload).encode()).hexdigest()
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT analysis_case_id FROM analysis_cases
                WHERE analysis_case_id = %s AND project_id = %s
                FOR SHARE
                """,
                (case_id, project_id),
            )
            if cursor.fetchone() is None:
                raise ValueError("Analysis Case does not exist in requested Project")
            cursor.execute(
                """
                SELECT analysis_case_id FROM change_requests
                WHERE change_request_id = %s AND project_id = %s
                FOR UPDATE
                """,
                (request_id, project_id),
            )
            request = cursor.fetchone()
            if request is None:
                raise ValueError("Change Request does not exist in requested Project")
            if request[0] is not None and str(request[0]) != case_id:
                raise PersistenceConflictError("Change Request is already bound to another Case")
            cursor.execute(
                """
                INSERT INTO change_request_case_binding_events (
                    binding_event_id, change_request_id, project_id,
                    analysis_case_id, actor, idempotency_key, payload_digest
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (event_id, request_id, project_id, case_id, actor, idempotency_key, digest),
            )
            created = cursor.rowcount == 1
            cursor.execute(
                """
                SELECT analysis_case_id, actor, idempotency_key, payload_digest
                FROM change_request_case_binding_events
                WHERE change_request_id = %s AND idempotency_key = %s
                """,
                (request_id, idempotency_key),
            )
            event = cursor.fetchone()
            if event is None or tuple(str(value) for value in event) != (
                case_id,
                actor,
                idempotency_key,
                digest,
            ):
                raise PersistenceConflictError(
                    "Change Request Case binding identity has different content"
                )
            cursor.execute(
                """
                UPDATE change_requests SET analysis_case_id = %s
                WHERE change_request_id = %s AND project_id = %s
                  AND analysis_case_id IS NULL
                """,
                (case_id, request_id, project_id),
            )
        return created

    def list_projects(self, *, limit: int = 50) -> tuple[dict[str, object], ...]:
        if not 1 <= limit <= 50:
            raise ValueError("Project limit must be between 1 and 50")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT project.project_id, project.name,
                       count(DISTINCT analysis_case.analysis_case_id) AS case_count,
                       count(DISTINCT request.change_request_id) AS request_count
                FROM projects AS project
                LEFT JOIN analysis_cases AS analysis_case
                  ON analysis_case.project_id = project.project_id
                LEFT JOIN change_requests AS request
                  ON request.project_id = project.project_id
                GROUP BY project.project_id, project.name
                ORDER BY project.name, project.project_id
                LIMIT %s
                """,
                (limit,),
            )
            rows = cursor.fetchall()
        return tuple(
            {
                "project_id": str(row[0]),
                "name": str(row[1]),
                "case_count": int(row[2]),
                "change_request_count": int(row[3]),
            }
            for row in rows
        )

    def list_change_requests(
        self, *, project_id: str, limit: int = 50
    ) -> tuple[dict[str, object], ...]:
        if not 1 <= limit <= 50:
            raise ValueError("Change Request limit must be between 1 and 50")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT request.change_request_id, request.analysis_case_id,
                       request.input_mode, request.submitted_by, request.submitted_at,
                       artifact.payload ->> 'requirement_text',
                       COALESCE(review.decision, 'pending')
                FROM change_requests AS request
                JOIN artifact_records AS artifact
                  ON artifact.artifact_id = request.change_request_id
                 AND artifact.project_id = request.project_id
                 AND artifact.artifact_type = 'ChangeRequest'
                LEFT JOIN LATERAL (
                    SELECT decision
                    FROM change_request_review_events
                    WHERE change_request_id = request.change_request_id
                      AND project_id = request.project_id
                    ORDER BY created_at DESC, review_event_id DESC
                    LIMIT 1
                ) AS review ON true
                WHERE request.project_id = %s
                ORDER BY request.submitted_at DESC, request.change_request_id DESC
                LIMIT %s
                """,
                (project_id, limit),
            )
            rows = cursor.fetchall()
        return tuple(
            {
                "change_request_id": str(row[0]),
                "analysis_case_id": str(row[1]) if row[1] is not None else None,
                "input_mode": str(row[2]),
                "submitted_by": str(row[3]),
                "submitted_at": row[4].isoformat(),
                "requirement_text": str(row[5]) if row[5] is not None else None,
                "document_review_status": str(row[6]),
            }
            for row in rows
        )

    def document_diff(self, request_id: str, *, limit: int = 200) -> dict[str, object]:
        if not 1 <= limit <= 200:
            raise ValueError("Document diff limit must be between 1 and 200")
        request = self.get_change_request(request_id)
        case_id = request["analysis_case_id"]
        if case_id is None:
            return {"change_request": request, "changes": [], "total": 0}
        project_id = str(request["project_id"])
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT artifact_id
                FROM artifact_records
                WHERE project_id = %s AND analysis_case_id = %s
                  AND artifact_type = 'StructuredChange'
                ORDER BY created_at, artifact_id
                LIMIT %s
                """,
                (project_id, case_id, limit),
            )
            artifact_ids = tuple(str(row[0]) for row in cursor.fetchall())
        changes: list[dict[str, Any]] = []
        for artifact_id in artifact_ids:
            artifact = self._artifacts.get(artifact_id)
            if artifact is None or artifact.get("artifact_type") != "StructuredChange":
                raise PersistenceConflictError("Document diff Artifact ledger is incomplete")
            changes.append(artifact)
        return {"change_request": request, "changes": changes, "total": len(changes)}

    def record_document_review(
        self,
        *,
        event_id: str,
        request_id: str,
        project_id: str,
        decision: str,
        actor: str,
        note: str | None,
    ) -> DocumentReviewRecord:
        if decision not in {"confirmed", "revision_requested"}:
            raise ValueError("Document review decision is invalid")
        if any(not value.strip() for value in (event_id, request_id, project_id, actor)):
            raise ValueError("Document review identity fields must not be blank")
        payload = {
            "event_id": event_id,
            "change_request_id": request_id,
            "project_id": project_id,
            "review_step": "document_diff",
            "decision": decision,
            "actor": actor,
            "note": note,
        }
        digest = hashlib.sha256(_json(payload).encode()).hexdigest()
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT analysis_case_id
                FROM change_requests
                WHERE change_request_id = %s AND project_id = %s
                FOR SHARE
                """,
                (request_id, project_id),
            )
            request = cursor.fetchone()
            if request is None:
                raise ValueError("Change Request does not exist in requested Project")
            if decision == "confirmed":
                if request[0] is None:
                    raise ValueError("Document diff cannot be confirmed before Case binding")
                cursor.execute(
                    """
                    SELECT 1 FROM artifact_records
                    WHERE project_id = %s AND analysis_case_id = %s
                      AND artifact_type = 'StructuredChange'
                    LIMIT 1
                    """,
                    (project_id, request[0]),
                )
                if cursor.fetchone() is None:
                    raise ValueError("Document diff cannot be confirmed without changes")
            cursor.execute(
                """
                INSERT INTO change_request_review_events (
                    review_event_id, change_request_id, project_id, review_step,
                    decision, actor, note, payload_digest
                ) VALUES (%s, %s, %s, 'document_diff', %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (event_id, request_id, project_id, decision, actor, note, digest),
            )
            created = cursor.rowcount == 1
            cursor.execute(
                """
                SELECT decision, actor, note, payload_digest, created_at
                FROM change_request_review_events
                WHERE review_event_id = %s
                """,
                (event_id,),
            )
            row = cursor.fetchone()
        if row is None or (str(row[0]), str(row[1]), row[2], str(row[3])) != (
            decision,
            actor,
            note,
            digest,
        ):
            raise PersistenceConflictError(
                "Document review event identity has different immutable content"
            )
        return DocumentReviewRecord(event_id, decision, actor, row[4], created)

    def require_confirmed_document_review(
        self, *, request_id: str, project_id: str, case_id: str
    ) -> None:
        request = self.get_change_request(request_id)
        if request["project_id"] != project_id or request["analysis_case_id"] != case_id:
            raise ValueError("Change Request does not belong to the requested Project and Case")
        review = request["document_review"]
        if not isinstance(review, dict) or review.get("status") != "confirmed":
            raise ValueError("Document diff must be confirmed before the next step")

    def case_workspace(self, *, project_id: str, case_id: str) -> dict[str, object]:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT analysis_case.status AS case_status,
                       analysis_case.updated_at,
                       revision.commit_sha AS base_revision,
                       report.impact_report_id, report.status AS report_status,
                       confirmation.confirmation_id, confirmation.confirmed_by,
                       packet.edit_packet_id, packet.status AS packet_status,
                       grant_record.approval_grant_id, grant_record.expires_at,
                       edit_result.edit_result_id, edit_result.status AS edit_status,
                       edit_result.result_repository_revision,
                       edit_result.command_evidence_status,
                       plan.ui_execution_plan_id, plan.status AS plan_status,
                       run.ui_execution_run_id, run.status AS run_status,
                       validation.verification_result_id,
                       validation.status AS validation_status
                FROM analysis_cases AS analysis_case
                JOIN repository_revisions AS revision
                  ON revision.repository_revision_id = analysis_case.repository_revision_id
                LEFT JOIN LATERAL (
                    SELECT * FROM impact_reports
                    WHERE project_id = analysis_case.project_id
                      AND analysis_case_id = analysis_case.analysis_case_id
                    ORDER BY created_at DESC, impact_report_id DESC LIMIT 1
                ) AS report ON true
                LEFT JOIN impact_confirmations AS confirmation
                  ON confirmation.impact_report_id = report.impact_report_id
                 AND confirmation.project_id = report.project_id
                LEFT JOIN LATERAL (
                    SELECT * FROM edit_packets
                    WHERE project_id = analysis_case.project_id
                      AND analysis_case_id = analysis_case.analysis_case_id
                    ORDER BY (status = 'active') DESC, created_at DESC LIMIT 1
                ) AS packet ON true
                LEFT JOIN approval_grants AS grant_record
                  ON grant_record.edit_packet_id = packet.edit_packet_id
                 AND grant_record.project_id = packet.project_id
                LEFT JOIN LATERAL (
                    SELECT * FROM edit_results
                    WHERE project_id = analysis_case.project_id
                      AND analysis_case_id = analysis_case.analysis_case_id
                    ORDER BY recorded_at DESC, edit_result_id DESC LIMIT 1
                ) AS edit_result ON true
                LEFT JOIN LATERAL (
                    SELECT * FROM ui_execution_plans
                    WHERE project_id = analysis_case.project_id
                      AND analysis_case_id = analysis_case.analysis_case_id
                    ORDER BY created_at DESC, ui_execution_plan_id DESC LIMIT 1
                ) AS plan ON true
                LEFT JOIN LATERAL (
                    SELECT * FROM ui_execution_runs
                    WHERE project_id = analysis_case.project_id
                      AND ui_execution_plan_id = plan.ui_execution_plan_id
                    ORDER BY started_at DESC, ui_execution_run_id DESC LIMIT 1
                ) AS run ON true
                LEFT JOIN LATERAL (
                    SELECT * FROM change_validations
                    WHERE project_id = analysis_case.project_id
                      AND analysis_case_id = analysis_case.analysis_case_id
                    ORDER BY created_at DESC, verification_result_id DESC LIMIT 1
                ) AS validation ON true
                WHERE analysis_case.project_id = %s
                  AND analysis_case.analysis_case_id = %s
                """,
                (project_id, case_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise ValueError("Analysis Case does not exist in requested Project")
        return {
            "project_id": project_id,
            "analysis_case_id": case_id,
            "case_status": str(row["case_status"]),
            "updated_at": row["updated_at"].isoformat(),
            "base_revision": str(row["base_revision"]),
            "impact_report": _entity(row, "impact_report_id", "report_status"),
            "confirmation": {
                "id": _optional(row["confirmation_id"]),
                "confirmed_by": _optional(row["confirmed_by"]),
            },
            "edit_packet": _entity(row, "edit_packet_id", "packet_status"),
            "approval_grant": {
                "id": _optional(row["approval_grant_id"]),
                "expires_at": (
                    row["expires_at"].isoformat() if row["expires_at"] is not None else None
                ),
            },
            "edit_result": {
                "id": _optional(row["edit_result_id"]),
                "status": _optional(row["edit_status"]),
                "result_revision": _optional(row["result_repository_revision"]),
                "command_evidence_status": _optional(row["command_evidence_status"]),
            },
            "ui_plan": _entity(row, "ui_execution_plan_id", "plan_status"),
            "ui_run": _entity(row, "ui_execution_run_id", "run_status"),
            "validation": _entity(row, "verification_result_id", "validation_status"),
        }

    def impact_report(self, *, project_id: str, case_id: str) -> dict[str, object] | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT impact_report_id
                FROM impact_reports
                WHERE project_id = %s AND analysis_case_id = %s
                  AND status IN ('awaiting_confirmation', 'confirmed', 'blocked')
                ORDER BY created_at DESC, impact_report_id DESC LIMIT 1
                """,
                (project_id, case_id),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        artifact = self._artifacts.get(str(row[0]))
        if artifact is None or artifact.get("artifact_type") != "ImpactReport":
            raise PersistenceConflictError("Impact Report Artifact ledger is incomplete")
        return artifact

    def evidence(self, *, project_id: str, case_id: str) -> dict[str, object]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT result.command_execution_id, result.status, result.exit_code,
                       result.stdout_digest, result.stderr_digest,
                       result.started_at, result.completed_at
                FROM command_execution_results AS result
                JOIN command_execution_requests AS request
                  ON request.command_execution_id = result.command_execution_id
                 AND request.project_id = result.project_id
                WHERE request.project_id = %s AND request.analysis_case_id = %s
                ORDER BY result.completed_at DESC, result.command_execution_id DESC
                LIMIT 100
                """,
                (project_id, case_id),
            )
            commands = cursor.fetchall()
            cursor.execute(
                """
                SELECT evidence.evidence_id, evidence.scenario_id,
                       evidence.evidence_type, evidence.evidence_ref,
                       evidence.content_digest, evidence.created_at
                FROM ui_execution_evidence AS evidence
                JOIN ui_execution_runs AS run
                  ON run.ui_execution_run_id = evidence.ui_execution_run_id
                 AND run.project_id = evidence.project_id
                JOIN ui_execution_plans AS plan
                  ON plan.ui_execution_plan_id = run.ui_execution_plan_id
                 AND plan.project_id = run.project_id
                WHERE plan.project_id = %s AND plan.analysis_case_id = %s
                ORDER BY evidence.created_at DESC, evidence.evidence_id DESC
                LIMIT 100
                """,
                (project_id, case_id),
            )
            ui_evidence = cursor.fetchall()
        return {
            "command_results": [
                {
                    "command_execution_id": str(row[0]),
                    "status": str(row[1]),
                    "exit_code": row[2],
                    "stdout_sha256": str(row[3]),
                    "stderr_sha256": str(row[4]),
                    "started_at": row[5].isoformat(),
                    "completed_at": row[6].isoformat(),
                }
                for row in commands
            ],
            "ui_evidence": [
                {
                    "evidence_id": str(row[0]),
                    "scenario_id": str(row[1]),
                    "evidence_type": str(row[2]),
                    "evidence_ref": str(row[3]),
                    "sha256": str(row[4]),
                    "created_at": row[5].isoformat(),
                }
                for row in ui_evidence
            ],
        }


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _optional(value: object) -> str | None:
    return str(value) if value is not None else None


def _entity(row: dict[str, Any], id_key: str, status_key: str) -> dict[str, str | None]:
    return {"id": _optional(row[id_key]), "status": _optional(row[status_key])}
