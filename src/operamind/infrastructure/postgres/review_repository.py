"""Append-only StructuredChange review decisions in PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, cast

from psycopg import Connection, Cursor

from operamind.domain import ChangeReviewStatus
from operamind.infrastructure.postgres.errors import PersistenceConflictError


class StructuredChangeReviewDecision(StrEnum):
    """Terminal decisions allowed in a human review event."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class StructuredChangeReviewState:
    """Current effective review state without mutating the source change."""

    project_id: str
    change_id: str
    source_snapshot_id: str
    target_snapshot_id: str
    stable_key: str
    status: ChangeReviewStatus
    review_event_id: str | None
    reviewed_by: str | None
    reason: str | None
    reviewed_at: datetime | None


class StructuredChangeReviewRepository:
    """Append decisions with idempotency and optimistic concurrency control."""

    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def review(
        self,
        *,
        review_event_id: str,
        project_id: str,
        change_id: str,
        decision: StructuredChangeReviewDecision,
        reviewed_by: str,
        reason: str,
        expected_previous_review_event_id: str | None,
    ) -> bool:
        """Append a decision; exact event replay is a no-op and stale writers fail."""

        required = (review_event_id, project_id, change_id, reviewed_by, reason)
        if any(not value.strip() for value in required):
            raise ValueError("StructuredChange review fields must not be blank")
        if (
            expected_previous_review_event_id is not None
            and not expected_previous_review_event_id.strip()
        ):
            raise ValueError("expected_previous_review_event_id must not be blank")

        with self._connection.transaction(), self._connection.cursor() as cursor:
            base_status = self._lock_change(
                cursor,
                project_id=project_id,
                change_id=change_id,
            )
            existing = self._load_event_identity(cursor, review_event_id)
            expected_identity = (
                project_id,
                change_id,
                expected_previous_review_event_id,
                decision.value,
                reviewed_by,
                reason,
            )
            if existing is not None:
                if existing != expected_identity:
                    raise PersistenceConflictError(
                        f"Review event ID has different content: {review_event_id}"
                    )
                return False

            cursor.execute(
                """
                SELECT review_event_id, decision
                FROM structured_change_review_events
                WHERE project_id = %s AND structured_change_id = %s
                ORDER BY review_sequence DESC
                LIMIT 1
                """,
                (project_id, change_id),
            )
            latest = cursor.fetchone()
            current_event_id = str(latest[0]) if latest is not None else None
            current_status = (
                ChangeReviewStatus(str(latest[1])) if latest is not None else base_status
            )
            if latest is None and current_status is not ChangeReviewStatus.NEEDS_REVIEW:
                raise ValueError(
                    "StructuredChange already has a terminal status without a review event"
                )
            if expected_previous_review_event_id != current_event_id:
                raise ValueError(
                    "Stale StructuredChange review: expected previous event "
                    f"{expected_previous_review_event_id!r}, current is {current_event_id!r}"
                )
            if decision.value == current_status.value:
                raise ValueError("Review decision must change the effective status")

            cursor.execute(
                """
                INSERT INTO structured_change_review_events (
                    review_event_id,
                    project_id,
                    structured_change_id,
                    previous_review_event_id,
                    previous_review_status,
                    decision,
                    reviewed_by,
                    reason
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    review_event_id,
                    project_id,
                    change_id,
                    current_event_id,
                    current_status.value,
                    decision.value,
                    reviewed_by,
                    reason,
                ),
            )
            stored = self._load_event_identity(cursor, review_event_id)
            if stored != expected_identity:
                raise PersistenceConflictError(
                    f"Review event ID has different content: {review_event_id}"
                )
        return True

    def get_state(
        self,
        *,
        project_id: str,
        change_id: str,
    ) -> StructuredChangeReviewState | None:
        """Return one change's base or latest event-derived review state."""

        if not project_id.strip() or not change_id.strip():
            raise ValueError("Review state identity fields must not be blank")
        with self._connection.cursor() as cursor:
            cursor.execute(
                _STATE_SELECT
                + """
                WHERE c.project_id = %s AND c.structured_change_id = %s
                """,
                (project_id, change_id),
            )
            row = cursor.fetchone()
        return _state_from_row(row) if row is not None else None

    def list_states(
        self,
        *,
        project_id: str,
        source_snapshot_id: str,
        target_snapshot_id: str,
    ) -> tuple[StructuredChangeReviewState, ...]:
        """Return effective states for a snapshot pair in Stable Key order."""

        required = (project_id, source_snapshot_id, target_snapshot_id)
        if any(not value.strip() for value in required):
            raise ValueError("Review list identity fields must not be blank")
        with self._connection.cursor() as cursor:
            cursor.execute(
                _STATE_SELECT
                + """
                WHERE c.project_id = %s
                  AND c.source_snapshot_id = %s
                  AND c.target_snapshot_id = %s
                ORDER BY c.stable_key
                """,
                (project_id, source_snapshot_id, target_snapshot_id),
            )
            rows = cursor.fetchall()
        return tuple(_state_from_row(row) for row in rows)

    @staticmethod
    def _lock_change(
        cursor: Cursor[Any],
        *,
        project_id: str,
        change_id: str,
    ) -> ChangeReviewStatus:
        cursor.execute(
            """
            SELECT project_id, review_status
            FROM structured_changes
            WHERE structured_change_id = %s
            FOR UPDATE
            """,
            (change_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError(f"StructuredChange does not exist: {change_id}")
        if str(row[0]) != project_id:
            raise ValueError("StructuredChange does not belong to the review project")
        return ChangeReviewStatus(str(row[1]))

    @staticmethod
    def _load_event_identity(
        cursor: Cursor[Any], review_event_id: str
    ) -> tuple[str, str, str | None, str, str, str] | None:
        cursor.execute(
            """
            SELECT project_id,
                   structured_change_id,
                   previous_review_event_id,
                   decision,
                   reviewed_by,
                   reason
            FROM structured_change_review_events
            WHERE review_event_id = %s
            """,
            (review_event_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return (
            str(row[0]),
            str(row[1]),
            str(row[2]) if row[2] is not None else None,
            str(row[3]),
            str(row[4]),
            str(row[5]),
        )


_STATE_SELECT = """
    SELECT c.project_id,
           c.structured_change_id,
           c.source_snapshot_id,
           c.target_snapshot_id,
           c.stable_key,
           COALESCE(latest.decision, c.review_status) AS effective_status,
           latest.review_event_id,
           latest.reviewed_by,
           latest.reason,
           latest.reviewed_at
    FROM structured_changes AS c
    LEFT JOIN LATERAL (
        SELECT e.review_event_id,
               e.decision,
               e.reviewed_by,
               e.reason,
               e.reviewed_at
        FROM structured_change_review_events AS e
        WHERE e.project_id = c.project_id
          AND e.structured_change_id = c.structured_change_id
        ORDER BY e.review_sequence DESC
        LIMIT 1
    ) AS latest ON true
"""


def _state_from_row(row: tuple[object, ...]) -> StructuredChangeReviewState:
    return StructuredChangeReviewState(
        project_id=str(row[0]),
        change_id=str(row[1]),
        source_snapshot_id=str(row[2]),
        target_snapshot_id=str(row[3]),
        stable_key=str(row[4]),
        status=ChangeReviewStatus(str(row[5])),
        review_event_id=str(row[6]) if row[6] is not None else None,
        reviewed_by=str(row[7]) if row[7] is not None else None,
        reason=str(row[8]) if row[8] is not None else None,
        reviewed_at=cast(datetime | None, row[9]),
    )
