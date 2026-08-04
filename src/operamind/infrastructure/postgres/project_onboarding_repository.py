"""Durable, lease-protected Project Onboarding stage persistence."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from psycopg import Connection

from operamind.infrastructure.postgres.errors import PersistenceConflictError

ProjectOnboardingAction = Literal["initialize", "rescan", "reindex", "relearn"]
ProjectOnboardingStage = Literal["discover", "learn", "documents", "index", "complete"]


@dataclass(frozen=True, slots=True)
class ProjectOnboardingRecord:
    onboarding_run_id: str
    project_id: str
    settings_revision: int
    requested_action: str
    status: str
    current_stage: str
    requested_by: str
    requested_at: datetime
    updated_at: datetime
    attempt_count: int
    document_snapshot_id: str | None
    document_count: int | None
    search_index_build_id: str | None
    generated_vector_count: int | None
    learning_run_id: str | None
    failure_reason: str | None

    def public_view(self) -> dict[str, object]:
        return {
            "onboarding_run_id": self.onboarding_run_id,
            "project_id": self.project_id,
            "settings_revision": self.settings_revision,
            "requested_action": self.requested_action,
            "status": self.status,
            "current_stage": self.current_stage,
            "requested_at": self.requested_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "attempt_count": self.attempt_count,
            "document_snapshot_id": self.document_snapshot_id,
            "document_count": self.document_count,
            "search_index_build_id": self.search_index_build_id,
            "generated_vector_count": self.generated_vector_count,
            "learning_run_id": self.learning_run_id,
            "failure_reason": self.failure_reason,
        }


@dataclass(frozen=True, slots=True)
class ProjectOnboardingClaim:
    record: ProjectOnboardingRecord
    lease_token: str


class ProjectOnboardingRepository:
    """Advance one recoverable Onboarding stage at a time."""

    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def enqueue(
        self,
        *,
        onboarding_run_id: str,
        project_id: str,
        settings_revision: int,
        requested_action: ProjectOnboardingAction,
        requested_by: str,
        document_snapshot_id: str | None = None,
        document_count: int | None = None,
    ) -> ProjectOnboardingRecord:
        required = (onboarding_run_id, project_id, requested_by)
        if any(not value.strip() for value in required):
            raise ValueError("Project Onboarding identity must not be blank")
        if settings_revision <= 0:
            raise ValueError("Project settings revision must be positive")
        if requested_action == "reindex" and (
            document_snapshot_id is None or document_count is None
        ):
            raise ValueError("Reindex requires a ready document snapshot")
        stage: ProjectOnboardingStage = "index" if requested_action == "reindex" else "discover"
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO project_onboarding_runs (
                    onboarding_run_id, project_id, settings_revision,
                    requested_action, current_stage, requested_by,
                    document_snapshot_id, document_count
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    onboarding_run_id,
                    project_id,
                    settings_revision,
                    requested_action,
                    stage,
                    requested_by,
                    document_snapshot_id,
                    document_count,
                ),
            )
            record = self._load(cursor, onboarding_run_id)
        if record is None:
            raise RuntimeError("Project Onboarding run disappeared after enqueue")
        expected = (
            project_id,
            settings_revision,
            requested_action,
            stage,
            requested_by,
            document_snapshot_id,
            document_count,
        )
        actual = (
            record.project_id,
            record.settings_revision,
            record.requested_action,
            record.current_stage,
            record.requested_by,
            record.document_snapshot_id,
            record.document_count,
        )
        if actual != expected:
            raise PersistenceConflictError("Project Onboarding run identity differs")
        return record

    def latest(self, project_id: str) -> ProjectOnboardingRecord | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT onboarding_run_id
                FROM project_onboarding_runs
                WHERE project_id = %s
                ORDER BY requested_at DESC, onboarding_run_id DESC
                LIMIT 1
                """,
                (project_id,),
            )
            row = cursor.fetchone()
            return self._load(cursor, str(row[0])) if row is not None else None

    def latest_ready(self, project_id: str) -> ProjectOnboardingRecord | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT onboarding_run_id
                FROM project_onboarding_runs
                WHERE project_id = %s AND status = 'ready'
                ORDER BY completed_at DESC, onboarding_run_id DESC
                LIMIT 1
                """,
                (project_id,),
            )
            row = cursor.fetchone()
            return self._load(cursor, str(row[0])) if row is not None else None

    def retry(self, *, onboarding_run_id: str, actor: str) -> ProjectOnboardingRecord:
        if not actor.strip():
            raise ValueError("Project Onboarding retry actor must not be blank")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE project_onboarding_runs
                SET status = 'queued', failure_reason = NULL, updated_at = clock_timestamp(),
                    requested_by = %s, completed_at = NULL
                WHERE onboarding_run_id = %s AND status = 'failed'
                """,
                (actor, onboarding_run_id),
            )
            record = self._load(cursor, onboarding_run_id)
        if record is None:
            raise ValueError("Project Onboarding run does not exist")
        if record.status != "queued":
            raise ValueError("Only a failed Project Onboarding run can be retried")
        return record

    def claim_next(
        self,
        *,
        owner: str,
        lease_seconds: int = 300,
    ) -> ProjectOnboardingClaim | None:
        if not owner.strip() or not 30 <= lease_seconds <= 3600:
            raise ValueError("Project Onboarding lease configuration is invalid")
        token = secrets.token_urlsafe(32)
        digest = _token_digest(token)
        expires_at = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE project_onboarding_runs AS run
                SET status = 'superseded', updated_at = clock_timestamp(),
                    completed_at = clock_timestamp(), lease_owner = NULL,
                    lease_token_digest = NULL, lease_expires_at = NULL,
                    heartbeat_at = NULL,
                    failure_reason = 'Project settings changed before this run completed'
                FROM project_workspaces AS workspace
                WHERE workspace.project_id = run.project_id
                  AND run.status IN ('queued', 'running', 'waiting_for_profile')
                  AND run.settings_revision <> workspace.settings_revision
                """
            )
            cursor.execute(
                """
                SELECT run.onboarding_run_id
                FROM project_onboarding_runs AS run
                JOIN project_workspaces AS workspace ON workspace.project_id = run.project_id
                WHERE (
                    run.status = 'queued'
                    OR (run.status = 'running' AND run.lease_expires_at <= clock_timestamp())
                )
                  AND run.settings_revision = workspace.settings_revision
                ORDER BY run.requested_at, run.onboarding_run_id
                FOR UPDATE OF run SKIP LOCKED
                LIMIT 1
                """
            )
            row = cursor.fetchone()
            if row is None:
                return None
            run_id = str(row[0])
            cursor.execute(
                """
                UPDATE project_onboarding_runs
                SET status = 'running', lease_owner = %s, lease_token_digest = %s,
                    lease_expires_at = %s, heartbeat_at = clock_timestamp(),
                    started_at = COALESCE(started_at, clock_timestamp()),
                    updated_at = clock_timestamp(), attempt_count = attempt_count + 1,
                    failure_reason = NULL
                WHERE onboarding_run_id = %s
                """,
                (owner, digest, expires_at, run_id),
            )
            record = self._load(cursor, run_id)
        if record is None or record.status != "running":
            raise PersistenceConflictError("Project Onboarding claim was not persisted")
        return ProjectOnboardingClaim(record=record, lease_token=token)

    def wait_for_learning(
        self,
        *,
        claim: ProjectOnboardingClaim,
        learning_run_id: str,
    ) -> ProjectOnboardingRecord:
        """Release the worker lease while Copilot and a user confirm the Profile."""

        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE project_onboarding_runs
                SET status = 'waiting_for_profile', current_stage = 'learn',
                    learning_run_id = %s, updated_at = clock_timestamp(),
                    lease_owner = NULL, lease_token_digest = NULL,
                    lease_expires_at = NULL, heartbeat_at = NULL,
                    failure_reason = NULL
                WHERE onboarding_run_id = %s AND status = 'running'
                  AND lease_token_digest = %s
                """,
                (
                    learning_run_id,
                    claim.record.onboarding_run_id,
                    _token_digest(claim.lease_token),
                ),
            )
            if cursor.rowcount != 1:
                raise PersistenceConflictError("Project Onboarding lease was lost")
            record = self._load(cursor, claim.record.onboarding_run_id)
        if record is None:
            raise RuntimeError("Project Onboarding run disappeared while waiting for learning")
        return record

    def resume_after_learning(
        self,
        *,
        onboarding_run_id: str,
        learning_run_id: str,
        settings_revision: int,
    ) -> ProjectOnboardingRecord:
        """Resume Canonical ingestion only for the confirmed learning run."""

        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE project_onboarding_runs
                SET status = 'queued', current_stage = 'documents',
                    updated_at = clock_timestamp(), failure_reason = NULL
                WHERE onboarding_run_id = %s
                  AND learning_run_id = %s
                  AND settings_revision = %s
                  AND status = 'waiting_for_profile'
                  AND current_stage = 'learn'
                """,
                (onboarding_run_id, learning_run_id, settings_revision),
            )
            if cursor.rowcount != 1:
                raise PersistenceConflictError(
                    "Project Onboarding is not waiting for this learning run"
                )
            record = self._load(cursor, onboarding_run_id)
        if record is None:
            raise RuntimeError("Project Onboarding run disappeared after learning")
        return record

    def advance(
        self,
        *,
        claim: ProjectOnboardingClaim,
        next_stage: ProjectOnboardingStage,
        document_snapshot_id: str | None = None,
        document_count: int | None = None,
        search_index_build_id: str | None = None,
        generated_vector_count: int | None = None,
    ) -> ProjectOnboardingRecord:
        ready = next_stage == "complete"
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE project_onboarding_runs
                SET status = %s, current_stage = %s, updated_at = clock_timestamp(),
                    completed_at = CASE WHEN %s THEN clock_timestamp() ELSE NULL END,
                    document_snapshot_id = COALESCE(%s, document_snapshot_id),
                    document_count = COALESCE(%s, document_count),
                    search_index_build_id = COALESCE(%s, search_index_build_id),
                    generated_vector_count = COALESCE(%s, generated_vector_count),
                    lease_owner = NULL, lease_token_digest = NULL,
                    lease_expires_at = NULL, heartbeat_at = NULL, failure_reason = NULL
                WHERE onboarding_run_id = %s AND status = 'running'
                  AND lease_token_digest = %s
                """,
                (
                    "ready" if ready else "queued",
                    next_stage,
                    ready,
                    document_snapshot_id,
                    document_count,
                    search_index_build_id,
                    generated_vector_count,
                    claim.record.onboarding_run_id,
                    _token_digest(claim.lease_token),
                ),
            )
            if cursor.rowcount != 1:
                raise PersistenceConflictError("Project Onboarding lease was lost")
            record = self._load(cursor, claim.record.onboarding_run_id)
        if record is None:
            raise RuntimeError("Project Onboarding run disappeared after stage completion")
        return record

    def heartbeat(
        self,
        *,
        claim: ProjectOnboardingClaim,
        lease_seconds: int = 300,
    ) -> bool:
        """Extend only the currently owned running stage lease."""

        if not 30 <= lease_seconds <= 3600:
            raise ValueError("Project Onboarding heartbeat lease is invalid")
        expires_at = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE project_onboarding_runs
                SET heartbeat_at = clock_timestamp(), lease_expires_at = %s,
                    updated_at = clock_timestamp()
                WHERE onboarding_run_id = %s AND status = 'running'
                  AND lease_token_digest = %s
                """,
                (
                    expires_at,
                    claim.record.onboarding_run_id,
                    _token_digest(claim.lease_token),
                ),
            )
            return cursor.rowcount == 1

    def fail(self, *, claim: ProjectOnboardingClaim, reason: str) -> ProjectOnboardingRecord:
        normalized = reason.strip()[:4000]
        if not normalized:
            normalized = "Project Onboarding stage failed"
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE project_onboarding_runs
                SET status = 'failed', failure_reason = %s, updated_at = clock_timestamp(),
                    completed_at = clock_timestamp(), lease_owner = NULL,
                    lease_token_digest = NULL, lease_expires_at = NULL, heartbeat_at = NULL
                WHERE onboarding_run_id = %s AND status = 'running'
                  AND lease_token_digest = %s
                """,
                (
                    normalized,
                    claim.record.onboarding_run_id,
                    _token_digest(claim.lease_token),
                ),
            )
            if cursor.rowcount != 1:
                raise PersistenceConflictError("Project Onboarding lease was lost")
            record = self._load(cursor, claim.record.onboarding_run_id)
        if record is None:
            raise RuntimeError("Project Onboarding run disappeared after failure")
        return record

    @staticmethod
    def _load(cursor: Any, onboarding_run_id: str) -> ProjectOnboardingRecord | None:
        cursor.execute(
            """
            SELECT onboarding_run_id, project_id, settings_revision, requested_action,
                   status, current_stage, requested_by, requested_at, updated_at,
                   attempt_count, document_snapshot_id, document_count,
                   search_index_build_id, generated_vector_count, learning_run_id,
                   failure_reason
            FROM project_onboarding_runs
            WHERE onboarding_run_id = %s
            """,
            (onboarding_run_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return ProjectOnboardingRecord(
            onboarding_run_id=str(row[0]),
            project_id=str(row[1]),
            settings_revision=int(row[2]),
            requested_action=str(row[3]),
            status=str(row[4]),
            current_stage=str(row[5]),
            requested_by=str(row[6]),
            requested_at=cast(datetime, row[7]),
            updated_at=cast(datetime, row[8]),
            attempt_count=int(row[9]),
            document_snapshot_id=str(row[10]) if row[10] is not None else None,
            document_count=int(row[11]) if row[11] is not None else None,
            search_index_build_id=str(row[12]) if row[12] is not None else None,
            generated_vector_count=int(row[13]) if row[13] is not None else None,
            learning_run_id=str(row[14]) if row[14] is not None else None,
            failure_reason=str(row[15]) if row[15] is not None else None,
        )


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
