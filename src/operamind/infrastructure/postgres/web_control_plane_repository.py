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


@dataclass(frozen=True, slots=True)
class ProjectInitializationRecord:
    project_id: str
    name: str
    workspace_root: str
    document_roots: tuple[str, ...]
    source_control_kind: str
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

    def initialize_project(
        self,
        *,
        project_id: str,
        name: str,
        workspace_root: str,
        source_control_kind: str,
        document_roots: tuple[str, ...],
        configured_by: str,
    ) -> ProjectInitializationRecord:
        if not all(
            value.strip()
            for value in (project_id, name, workspace_root, source_control_kind, configured_by)
        ):
            raise ValueError("プロジェクト初期化の入力値を空にできません")
        if source_control_kind not in {"git", "local_files"}:
            raise ValueError("サポートされていないソース管理種別です")
        if not document_roots or any(not root.strip() for root in document_roots):
            raise ValueError("設計書の場所を一つ以上入力してください")
        if len(document_roots) != len(set(document_roots)):
            raise ValueError("設計書の場所を重複して登録できません")

        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO projects (project_id, name)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                """,
                (project_id, name),
            )
            created = cursor.rowcount == 1
            cursor.execute(
                "SELECT name FROM projects WHERE project_id = %s FOR UPDATE",
                (project_id,),
            )
            project_row = cursor.fetchone()
            if project_row is None:
                raise RuntimeError("Project disappeared during initialization")
            if str(project_row[0]) != name:
                raise PersistenceConflictError(
                    "同じプロジェクト ID が別のプロジェクト名で登録されています"
                )

            cursor.execute(
                """
                SELECT project.name, workspace.workspace_root, workspace.source_control_kind
                FROM project_workspaces AS workspace
                JOIN projects AS project ON project.project_id = workspace.project_id
                WHERE workspace.project_id = %s
                """,
                (project_id,),
            )
            workspace_row = cursor.fetchone()
            if workspace_row is not None and (
                str(workspace_row[1]), str(workspace_row[2])
            ) != (workspace_root, source_control_kind):
                raise PersistenceConflictError(
                    "同じプロジェクト ID が別の Workspace 設定で登録されています"
                )

            cursor.execute(
                """
                SELECT root_path
                FROM project_document_roots
                WHERE project_id = %s
                ORDER BY position
                """,
                (project_id,),
            )
            stored_roots = tuple(str(row[0]) for row in cursor.fetchall())
            if stored_roots and stored_roots != document_roots:
                raise PersistenceConflictError(
                    "同じプロジェクト ID が別の設計書の場所で登録されています"
                )

            if workspace_row is None:
                cursor.execute(
                    """
                    INSERT INTO project_workspaces (
                        project_id, workspace_root, source_control_kind, configured_by
                    ) VALUES (%s, %s, %s, %s)
                    """,
                    (project_id, workspace_root, source_control_kind, configured_by),
                )
            if not stored_roots:
                cursor.executemany(
                    """
                    INSERT INTO project_document_roots (
                        document_root_id, project_id, root_path, position
                    ) VALUES (%s, %s, %s, %s)
                    """,
                    [
                        (
                            _project_document_root_id(project_id, root),
                            project_id,
                            root,
                            position,
                        )
                        for position, root in enumerate(document_roots)
                    ],
                )

        return ProjectInitializationRecord(
            project_id=project_id,
            name=name,
            workspace_root=workspace_root,
            document_roots=document_roots,
            source_control_kind=source_control_kind,
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

    def list_projects(self, *, limit: int = 50) -> tuple[dict[str, object], ...]:
        if not 1 <= limit <= 50:
            raise ValueError("Project limit must be between 1 and 50")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT project.project_id, project.name,
                       count(DISTINCT analysis_case.analysis_case_id) AS case_count,
                       count(DISTINCT request.change_request_id) AS request_count,
                       workspace.workspace_root, workspace.source_control_kind,
                       COALESCE(
                           (
                               SELECT jsonb_agg(root.root_path ORDER BY root.position)
                               FROM project_document_roots AS root
                               WHERE root.project_id = project.project_id
                           ),
                           '[]'::jsonb
                       ) AS document_roots
                FROM projects AS project
                LEFT JOIN project_workspaces AS workspace
                  ON workspace.project_id = project.project_id
                LEFT JOIN analysis_cases AS analysis_case
                  ON analysis_case.project_id = project.project_id
                LEFT JOIN change_requests AS request
                  ON request.project_id = project.project_id
                GROUP BY project.project_id, project.name,
                         workspace.workspace_root, workspace.source_control_kind
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
                "workspace_root": str(row[4]) if row[4] is not None else None,
                "source_control_kind": str(row[5]) if row[5] is not None else None,
                "document_roots": [str(value) for value in row[6]],
            }
            for row in rows
        )

    def project_workspace_root(self, project_id: str) -> str | None:
        if not project_id.strip():
            raise ValueError("Project ID must not be blank")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT workspace_root
                FROM repositories
                WHERE project_id = %s AND workspace_root IS NOT NULL
                ORDER BY created_at, repository_id
                """,
                (project_id,),
            )
            roots = tuple(str(row[0]) for row in cursor.fetchall())
        unique = tuple(dict.fromkeys(roots))
        if not unique:
            return None
        if len(unique) != 1:
            raise ValueError(
                "Project must have exactly one registered Workspace for automatic Change Tasks"
            )
        return unique[0]

    def project_workspace_registration(self, project_id: str) -> dict[str, str] | None:
        """Return the initialized Workspace and its source-control mode."""

        if not project_id.strip():
            raise ValueError("Project ID must not be blank")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT project.name, workspace.workspace_root, workspace.source_control_kind
                FROM project_workspaces AS workspace
                JOIN projects AS project ON project.project_id = workspace.project_id
                WHERE workspace.project_id = %s
                """,
                (project_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return {
            "project_name": str(row[0]),
            "workspace_root": str(row[1]),
            "source_control_kind": str(row[2]),
        }

    def register_repository(
        self,
        *,
        repository_id: str,
        project_id: str,
        remote_url: str,
        workspace_root: str,
    ) -> None:
        """Register the Git identity discovered for a Web-initialized project."""

        if not all(
            value.strip() for value in (repository_id, project_id, remote_url, workspace_root)
        ):
            raise ValueError("Repository registration fields must not be blank")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO repositories (repository_id, project_id, remote_url, workspace_root)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (repository_id, project_id, remote_url, workspace_root),
            )
            cursor.execute(
                """
                SELECT project_id, remote_url, workspace_root
                FROM repositories
                WHERE repository_id = %s
                """,
                (repository_id,),
            )
            row = cursor.fetchone()
        if row is None or tuple(str(value) for value in row) != (
            project_id,
            remote_url,
            workspace_root,
        ):
            raise PersistenceConflictError("Repository identity differs from initialized Workspace")

    def project_repository_registration(self, project_id: str) -> dict[str, str] | None:
        """Return one unambiguous Repository registration for automatic Case creation."""

        if not project_id.strip():
            raise ValueError("Project ID must not be blank")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT project.name, repository.repository_id,
                       repository.remote_url, repository.workspace_root
                FROM projects AS project
                JOIN repositories AS repository
                  ON repository.project_id = project.project_id
                WHERE project.project_id = %s
                  AND repository.workspace_root IS NOT NULL
                ORDER BY repository.created_at, repository.repository_id
                """,
                (project_id,),
            )
            rows = cursor.fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise ValueError(
                "Project must have exactly one registered Repository for automatic Analysis Case"
            )
        row = rows[0]
        return {
            "project_name": str(row[0]),
            "repository_id": str(row[1]),
            "remote_url": str(row[2]),
            "workspace_root": str(row[3]),
        }

    def repository_revision_id(self, *, repository_id: str, commit_sha: str) -> str | None:
        if not repository_id.strip() or not commit_sha.strip():
            raise ValueError("Repository revision lookup scope must not be blank")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT repository_revision_id
                FROM repository_revisions
                WHERE repository_id = %s AND commit_sha = %s
                """,
                (repository_id, commit_sha),
            )
            row = cursor.fetchone()
        return str(row[0]) if row is not None else None

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
                       edit_result.validation_mode AS edit_validation_mode,
                       edit_result.tests_passed AS edit_tests_passed,
                       edit_result.result_repository_revision,
                       edit_result.command_evidence_status
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
                "validation_mode": _optional(row["edit_validation_mode"]),
                "tests_passed": row["edit_tests_passed"],
                "result_revision": _optional(row["result_repository_revision"]),
                "command_evidence_status": _optional(row["command_evidence_status"]),
            },
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


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _project_document_root_id(project_id: str, root_path: str) -> str:
    digest = hashlib.sha256(f"{project_id}\0{root_path}".encode()).hexdigest()[:24]
    return f"project-document-root-{digest}"


def _optional(value: object) -> str | None:
    return str(value) if value is not None else None


def _entity(row: dict[str, Any], id_key: str, status_key: str) -> dict[str, str | None]:
    return {"id": _optional(row[id_key]), "status": _optional(row[status_key])}
