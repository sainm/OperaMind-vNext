"""Durable Project-specific Document Profile learning tasks."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from psycopg import Connection

from operamind.infrastructure.postgres.errors import PersistenceConflictError


@dataclass(frozen=True, slots=True)
class DocumentProfileLearningRecord:
    learning_run_id: str
    project_id: str
    onboarding_run_id: str
    settings_revision: int
    status: str
    requested_by: str
    instruction: str | None
    source_structure: dict[str, Any]
    source_structure_digest: str
    sample_count: int
    previous_profile_version_ids: tuple[str, ...]
    claimed_by: str | None
    accepted_by: str | None
    draft_payload: dict[str, Any] | None
    covered_sample_count: int | None
    coverage_percent: float | None
    ambiguity_count: int | None
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime

    def public_view(self) -> dict[str, object]:
        return {
            "learning_run_id": self.learning_run_id,
            "project_id": self.project_id,
            "settings_revision": self.settings_revision,
            "status": self.status,
            "instruction": self.instruction,
            "sample_count": self.sample_count,
            "source_structure_digest": self.source_structure_digest,
            "previous_profile_version_ids": list(self.previous_profile_version_ids),
            "covered_sample_count": self.covered_sample_count,
            "coverage_percent": self.coverage_percent,
            "ambiguity_count": self.ambiguity_count,
            "failure_reason": self.failure_reason,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "draft": self.draft_payload,
        }


@dataclass(frozen=True, slots=True)
class DocumentProfileLearningClaim:
    record: DocumentProfileLearningRecord
    claim_token: str


class DocumentProfileLearningRepository:
    """Persist one immutable input structure and one validated Copilot draft."""

    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def create(
        self,
        *,
        learning_run_id: str,
        project_id: str,
        onboarding_run_id: str,
        settings_revision: int,
        requested_by: str,
        source_structure: dict[str, Any],
        source_structure_digest: str,
        sample_count: int,
        previous_profile_version_ids: tuple[str, ...],
        instruction: str | None = None,
    ) -> DocumentProfileLearningRecord:
        if any(
            not value.strip()
            for value in (learning_run_id, project_id, onboarding_run_id, requested_by)
        ):
            raise ValueError("Document Profile learning identity must not be blank")
        if settings_revision <= 0 or sample_count <= 0:
            raise ValueError("Document Profile learning scope is invalid")
        digest_source = cast(
            dict[str, Any], source_structure.get("structure_identity", source_structure)
        )
        canonical_structure = _canonical_json(source_structure)
        canonical_identity = _canonical_json(digest_source)
        if hashlib.sha256(canonical_identity.encode()).hexdigest() != source_structure_digest:
            raise ValueError("Document Profile learning structure digest differs")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO project_document_learning_runs (
                    learning_run_id, project_id, onboarding_run_id, settings_revision,
                    requested_by, instruction, source_structure, source_structure_digest,
                    sample_count, previous_profile_version_ids
                ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb)
                ON CONFLICT DO NOTHING
                """,
                (
                    learning_run_id,
                    project_id,
                    onboarding_run_id,
                    settings_revision,
                    requested_by,
                    instruction,
                    canonical_structure,
                    source_structure_digest,
                    sample_count,
                    json.dumps(list(previous_profile_version_ids)),
                ),
            )
            record = self._load(cursor, learning_run_id)
        if record is None:
            raise RuntimeError("Document Profile learning run disappeared")
        expected = (
            project_id,
            onboarding_run_id,
            settings_revision,
            requested_by,
            instruction,
            source_structure,
            source_structure_digest,
            sample_count,
            previous_profile_version_ids,
        )
        actual = (
            record.project_id,
            record.onboarding_run_id,
            record.settings_revision,
            record.requested_by,
            record.instruction,
            record.source_structure,
            record.source_structure_digest,
            record.sample_count,
            record.previous_profile_version_ids,
        )
        if actual != expected:
            raise PersistenceConflictError("Document Profile learning identity differs")
        return record

    def latest(self, project_id: str) -> DocumentProfileLearningRecord | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT learning_run_id
                FROM project_document_learning_runs
                WHERE project_id = %s
                ORDER BY created_at DESC, learning_run_id DESC
                LIMIT 1
                """,
                (project_id,),
            )
            row = cursor.fetchone()
            return self._load(cursor, str(row[0])) if row is not None else None

    def get(self, learning_run_id: str) -> DocumentProfileLearningRecord | None:
        with self._connection.cursor() as cursor:
            return self._load(cursor, learning_run_id)

    def latest_confirmed(self, project_id: str) -> DocumentProfileLearningRecord | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT learning_run_id
                FROM project_document_learning_runs
                WHERE project_id = %s AND status = 'confirmed'
                ORDER BY confirmed_at DESC, learning_run_id DESC
                LIMIT 1
                """,
                (project_id,),
            )
            row = cursor.fetchone()
            return self._load(cursor, str(row[0])) if row is not None else None

    def bind_confirmed_profiles(
        self,
        *,
        learning_run_id: str,
        project_id: str,
        profile_version_ids: tuple[str, ...],
    ) -> None:
        """Persist the exact Profile set used by this confirmed learning version."""

        if not profile_version_ids:
            raise ValueError("A confirmed learning run requires at least one Profile")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            for position, profile_version_id in enumerate(profile_version_ids):
                cursor.execute(
                    """
                    INSERT INTO project_document_learning_profiles (
                        learning_run_id, project_id, profile_version_id, position
                    ) VALUES (%s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (learning_run_id, project_id, profile_version_id, position),
                )
            cursor.execute(
                """
                SELECT profile_version_id
                FROM project_document_learning_profiles
                WHERE learning_run_id = %s AND project_id = %s
                ORDER BY position
                """,
                (learning_run_id, project_id),
            )
            actual = tuple(str(row[0]) for row in cursor.fetchall())
        if actual != profile_version_ids:
            raise PersistenceConflictError("Confirmed learning Profile set differs")

    def profile_version_ids(self, learning_run_id: str) -> tuple[str, ...]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT profile_version_id
                FROM project_document_learning_profiles
                WHERE learning_run_id = %s
                ORDER BY position
                """,
                (learning_run_id,),
            )
            return tuple(str(row[0]) for row in cursor.fetchall())

    def confirmed_for_structure(
        self, *, project_id: str, structure_digest: str
    ) -> DocumentProfileLearningRecord | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT learning_run_id
                FROM project_document_learning_runs
                WHERE project_id = %s
                  AND source_structure_digest = %s AND status = 'confirmed'
                ORDER BY confirmed_at DESC, learning_run_id DESC
                LIMIT 1
                """,
                (project_id, structure_digest),
            )
            row = cursor.fetchone()
            return self._load(cursor, str(row[0])) if row is not None else None

    def supersede_active(
        self, *, project_id: str, settings_revision: int, reason: str
    ) -> int:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE project_document_learning_runs
                SET status = 'superseded', failure_reason = %s,
                    claimed_by = NULL, claim_token_digest = NULL,
                    claim_expires_at = NULL, updated_at = clock_timestamp()
                WHERE project_id = %s AND settings_revision = %s
                  AND status IN ('pending', 'claimed', 'in_progress', 'draft_ready')
                """,
                (reason.strip()[:4000], project_id, settings_revision),
            )
            return cursor.rowcount

    def claim_next(
        self, *, workspace_root: str, consumer_id: str, lease_seconds: int = 300
    ) -> DocumentProfileLearningClaim | None:
        if not workspace_root.strip() or not consumer_id.strip():
            raise ValueError("Document Profile learning Bridge identity must not be blank")
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE project_document_learning_runs AS learning
                SET status = 'superseded', updated_at = clock_timestamp(),
                    claimed_by = NULL, claim_token_digest = NULL, claim_expires_at = NULL,
                    failure_reason = 'Project settings changed before learning completed'
                FROM project_workspaces AS workspace
                WHERE workspace.project_id = learning.project_id
                  AND learning.status IN ('pending', 'claimed', 'in_progress', 'draft_ready')
                  AND learning.settings_revision <> workspace.settings_revision
                """
            )
            cursor.execute(
                """
                SELECT learning.learning_run_id
                FROM project_document_learning_runs AS learning
                JOIN project_workspaces AS workspace
                  ON workspace.project_id = learning.project_id
                WHERE workspace.workspace_root = %s
                  AND learning.settings_revision = workspace.settings_revision
                  AND (
                    learning.status = 'pending'
                    OR (
                        learning.status IN ('claimed', 'in_progress')
                        AND learning.claim_expires_at <= clock_timestamp()
                    )
                  )
                ORDER BY learning.created_at, learning.learning_run_id
                FOR UPDATE OF learning SKIP LOCKED
                LIMIT 1
                """,
                (workspace_root,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            run_id = str(row[0])
            cursor.execute(
                """
                UPDATE project_document_learning_runs
                SET status = 'claimed', claimed_by = %s, claim_token_digest = %s,
                    claim_expires_at = %s, updated_at = clock_timestamp(), failure_reason = NULL
                WHERE learning_run_id = %s
                """,
                (consumer_id, _token_digest(token), expires_at, run_id),
            )
            record = self._load(cursor, run_id)
        if record is None:
            raise RuntimeError("Document Profile learning claim disappeared")
        return DocumentProfileLearningClaim(record=record, claim_token=token)

    def accept(
        self,
        *,
        learning_run_id: str,
        workspace_root: str,
        consumer_id: str,
        claim_token: str,
        actor: str,
    ) -> DocumentProfileLearningRecord:
        if not actor.strip() or not claim_token.strip():
            raise ValueError("Document Profile learning actor and claim token must not be blank")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE project_document_learning_runs AS learning
                SET status = 'in_progress', accepted_by = %s,
                    claim_expires_at = clock_timestamp() + interval '5 minutes',
                    updated_at = clock_timestamp()
                FROM project_workspaces AS workspace
                WHERE learning.learning_run_id = %s
                  AND workspace.project_id = learning.project_id
                  AND workspace.workspace_root = %s
                  AND learning.status IN ('claimed', 'in_progress')
                  AND learning.claimed_by = %s
                  AND learning.claim_token_digest = %s
                  AND learning.claim_expires_at > clock_timestamp()
                """,
                (
                    actor,
                    learning_run_id,
                    workspace_root,
                    consumer_id,
                    _token_digest(claim_token),
                ),
            )
            if cursor.rowcount != 1:
                raise PersistenceConflictError("Document Profile learning claim is unavailable")
            record = self._load(cursor, learning_run_id)
        if record is None:
            raise RuntimeError("Document Profile learning run disappeared after accept")
        return record

    def resume(
        self,
        *,
        learning_run_id: str,
        workspace_root: str,
        consumer_id: str,
        claim_token: str,
    ) -> DocumentProfileLearningRecord:
        if not claim_token.strip():
            raise ValueError("Document Profile learning claim token must not be blank")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE project_document_learning_runs AS learning
                SET claim_expires_at = clock_timestamp() + interval '5 minutes',
                    updated_at = clock_timestamp()
                FROM project_workspaces AS workspace
                WHERE learning.learning_run_id = %s
                  AND workspace.project_id = learning.project_id
                  AND workspace.workspace_root = %s
                  AND learning.status IN ('claimed', 'in_progress')
                  AND learning.claimed_by = %s
                  AND learning.claim_token_digest = %s
                  AND learning.claim_expires_at > clock_timestamp()
                """,
                (
                    learning_run_id,
                    workspace_root,
                    consumer_id,
                    _token_digest(claim_token),
                ),
            )
            renewed = cursor.rowcount == 1
            if not renewed:
                cursor.execute(
                    """
                    SELECT 1
                    FROM project_document_learning_runs AS learning
                    JOIN project_workspaces AS workspace
                      ON workspace.project_id = learning.project_id
                    WHERE learning.learning_run_id = %s
                      AND workspace.workspace_root = %s
                    """,
                    (learning_run_id, workspace_root),
                )
                if cursor.fetchone() is None:
                    raise PersistenceConflictError(
                        "Document Profile learning claim is unavailable"
                    )
            record = self._load(cursor, learning_run_id)
        if record is None:
            raise RuntimeError("Document Profile learning run disappeared while resuming")
        if not renewed and record.status in {"claimed", "in_progress"}:
            raise PersistenceConflictError("Document Profile learning claim is unavailable")
        return record

    def record_draft(
        self,
        *,
        learning_run_id: str,
        workspace_root: str,
        consumer_id: str,
        claim_token: str,
        draft: dict[str, Any],
        covered_sample_count: int,
        coverage_percent: float,
        ambiguity_count: int,
    ) -> DocumentProfileLearningRecord:
        if not consumer_id.strip() or not claim_token.strip():
            raise ValueError("Document Profile learning consumer and claim token must not be blank")
        canonical = _canonical_json(draft)
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        draft_ready = coverage_percent == 100 and ambiguity_count == 0
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE project_document_learning_runs AS learning
                SET status = %s, draft_payload = %s::jsonb, draft_digest = %s,
                    covered_sample_count = %s, coverage_percent = %s,
                    ambiguity_count = %s, updated_at = clock_timestamp(),
                    claimed_by = CASE WHEN %s THEN NULL ELSE claimed_by END,
                    claim_token_digest = CASE WHEN %s THEN NULL ELSE claim_token_digest END,
                    claim_expires_at = CASE
                        WHEN %s THEN NULL
                        ELSE clock_timestamp() + interval '5 minutes'
                    END
                FROM project_workspaces AS workspace
                WHERE learning.learning_run_id = %s
                  AND workspace.project_id = learning.project_id
                  AND workspace.workspace_root = %s
                  AND learning.status = 'in_progress'
                  AND learning.claimed_by = %s
                  AND learning.claim_token_digest = %s
                  AND learning.claim_expires_at > clock_timestamp()
                """,
                (
                    "draft_ready" if draft_ready else "in_progress",
                    canonical,
                    digest,
                    covered_sample_count,
                    coverage_percent,
                    ambiguity_count,
                    draft_ready,
                    draft_ready,
                    draft_ready,
                    learning_run_id,
                    workspace_root,
                    consumer_id,
                    _token_digest(claim_token),
                ),
            )
            if cursor.rowcount != 1:
                raise PersistenceConflictError("Document Profile learning task is not writable")
            record = self._load(cursor, learning_run_id)
        if record is None:
            raise RuntimeError("Document Profile learning draft disappeared")
        return record

    def confirm(self, *, learning_run_id: str, actor: str) -> DocumentProfileLearningRecord:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE project_document_learning_runs
                SET status = 'confirmed', confirmed_at = clock_timestamp(), confirmed_by = %s,
                    updated_at = clock_timestamp()
                WHERE learning_run_id = %s AND status = 'draft_ready'
                  AND coverage_percent = 100 AND ambiguity_count = 0
                """,
                (actor, learning_run_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Only a 100% unambiguous Profile draft can be confirmed")
            record = self._load(cursor, learning_run_id)
        if record is None:
            raise RuntimeError("Document Profile learning run disappeared after confirmation")
        return record

    def cancel(
        self,
        *,
        learning_run_id: str,
        workspace_root: str,
        consumer_id: str,
        claim_token: str,
        reason: str,
    ) -> DocumentProfileLearningRecord:
        if not claim_token.strip():
            raise ValueError("Document Profile learning claim token must not be blank")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE project_document_learning_runs AS learning
                SET status = 'cancelled', failure_reason = %s,
                    claimed_by = NULL, claim_token_digest = NULL, claim_expires_at = NULL,
                    updated_at = clock_timestamp()
                FROM project_workspaces AS workspace
                WHERE learning.learning_run_id = %s
                  AND workspace.project_id = learning.project_id
                  AND workspace.workspace_root = %s
                  AND learning.claimed_by = %s
                  AND learning.claim_token_digest = %s
                  AND learning.claim_expires_at > clock_timestamp()
                  AND learning.status IN ('claimed', 'in_progress')
                """,
                (
                    reason.strip()[:4000],
                    learning_run_id,
                    workspace_root,
                    consumer_id,
                    _token_digest(claim_token),
                ),
            )
            if cursor.rowcount != 1:
                raise PersistenceConflictError("Document Profile learning task cannot be cancelled")
            record = self._load(cursor, learning_run_id)
        if record is None:
            raise RuntimeError("Document Profile learning run disappeared after cancellation")
        return record

    @staticmethod
    def _load(cursor: Any, learning_run_id: str) -> DocumentProfileLearningRecord | None:
        cursor.execute(
            """
            SELECT learning_run_id, project_id, onboarding_run_id, settings_revision,
                   status, requested_by, instruction, source_structure,
                   source_structure_digest, sample_count, previous_profile_version_ids,
                   claimed_by, accepted_by, draft_payload, covered_sample_count,
                   coverage_percent, ambiguity_count, failure_reason, created_at, updated_at
            FROM project_document_learning_runs
            WHERE learning_run_id = %s
            """,
            (learning_run_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return DocumentProfileLearningRecord(
            learning_run_id=str(row[0]),
            project_id=str(row[1]),
            onboarding_run_id=str(row[2]),
            settings_revision=int(row[3]),
            status=str(row[4]),
            requested_by=str(row[5]),
            instruction=str(row[6]) if row[6] is not None else None,
            source_structure=cast(dict[str, Any], row[7]),
            source_structure_digest=str(row[8]),
            sample_count=int(row[9]),
            previous_profile_version_ids=tuple(
                str(value) for value in cast(list[object], row[10])
            ),
            claimed_by=str(row[11]) if row[11] is not None else None,
            accepted_by=str(row[12]) if row[12] is not None else None,
            draft_payload=cast(dict[str, Any], row[13]) if row[13] is not None else None,
            covered_sample_count=int(row[14]) if row[14] is not None else None,
            coverage_percent=(
                float(cast(Decimal, row[15])) if row[15] is not None else None
            ),
            ambiguity_count=int(row[16]) if row[16] is not None else None,
            failure_reason=str(row[17]) if row[17] is not None else None,
            created_at=cast(datetime, row[18]),
            updated_at=cast(datetime, row[19]),
        )


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
