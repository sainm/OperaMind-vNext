"""Review an immutable draft UI Knowledge Snapshot into a new version."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg import Connection

from operamind.domain import UiKnowledgeSnapshot
from operamind.infrastructure.postgres import (
    UiKnowledgeRepository,
    UiKnowledgeReviewRecord,
)


@dataclass(frozen=True, slots=True)
class UiKnowledgeReviewRequest:
    project_id: str
    source_snapshot_id: str
    result_snapshot_id: str
    result_snapshot_version: str
    review_event_id: str
    decision: str
    reviewed_by: str
    activate: bool = False
    reason: str | None = None

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (
                self.project_id,
                self.source_snapshot_id,
                self.result_snapshot_id,
                self.result_snapshot_version,
                self.review_event_id,
                self.reviewed_by,
            )
        ):
            raise ValueError("UI Knowledge Review request fields must not be blank")
        if self.decision not in {"approved", "rejected"}:
            raise ValueError("UI Knowledge Review decision is invalid")
        if self.activate and self.decision != "approved":
            raise ValueError("Only approved UI Knowledge may become active")
        if self.reason is not None and not self.reason.strip():
            raise ValueError("UI Knowledge Review reason must not be blank")


@dataclass(frozen=True, slots=True)
class UiKnowledgeReviewServiceResult:
    record: UiKnowledgeReviewRecord
    snapshot: UiKnowledgeSnapshot


class UiKnowledgeReviewService:
    def __init__(self, *, connection: Connection[Any]) -> None:
        self._knowledge = UiKnowledgeRepository(connection)

    def review(
        self,
        request: UiKnowledgeReviewRequest,
    ) -> UiKnowledgeReviewServiceResult:
        record = self._knowledge.review(
            project_id=request.project_id,
            source_snapshot_id=request.source_snapshot_id,
            result_snapshot_id=request.result_snapshot_id,
            result_snapshot_version=request.result_snapshot_version,
            review_event_id=request.review_event_id,
            decision=request.decision,
            reviewed_by=request.reviewed_by,
            activate=request.activate,
            reason=request.reason,
        )
        snapshot = self._knowledge.load(
            project_id=request.project_id,
            snapshot_id=request.result_snapshot_id,
        )
        return UiKnowledgeReviewServiceResult(record=record, snapshot=snapshot)
