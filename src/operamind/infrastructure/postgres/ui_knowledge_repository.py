"""Immutable persistence for approved, deployment-scoped UI Knowledge."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, cast

from psycopg import Connection

from operamind.domain import UiKnowledgeSnapshot
from operamind.infrastructure.postgres.errors import PersistenceConflictError


@dataclass(frozen=True, slots=True)
class UiKnowledgeSnapshotRecord:
    created: bool
    snapshot_id: str
    review_status: str
    active: bool


@dataclass(frozen=True, slots=True)
class UiKnowledgeReviewRecord:
    created: bool
    review_event_id: str
    source_snapshot_id: str
    result_snapshot_id: str
    decision: str
    active: bool


class UiKnowledgeRepository:
    """Store reviewed target names and Locator candidates for an exact deployment."""

    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def store(self, snapshot: UiKnowledgeSnapshot) -> UiKnowledgeSnapshotRecord:
        payload = _snapshot_payload(snapshot)
        canonical = _json(payload)
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT payload_digest, review_status, is_active
                FROM ui_knowledge_snapshots
                WHERE ui_knowledge_snapshot_id = %s
                """,
                (snapshot.snapshot_id,),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if tuple(existing[:2]) != (digest, snapshot.review_status):
                    raise PersistenceConflictError(
                        "UI Knowledge Snapshot identity has different content: "
                        f"{snapshot.snapshot_id}"
                    )
                self._load_snapshot(
                    project_id=snapshot.project_id,
                    snapshot_id=snapshot.snapshot_id,
                    environment_id=snapshot.environment_id,
                    deployment_revision=snapshot.deployment_revision,
                    require_approved=False,
                )
                return UiKnowledgeSnapshotRecord(
                    False, snapshot.snapshot_id, snapshot.review_status, bool(existing[2])
                )
            cursor.execute(
                """
                SELECT 1 FROM ui_deployments
                WHERE project_id = %s AND environment_id = %s AND deployment_revision = %s
                  AND status = 'ready'
                """,
                (snapshot.project_id, snapshot.environment_id, snapshot.deployment_revision),
            )
            if cursor.fetchone() is None:
                raise ValueError("UI Knowledge requires the exact ready Deployment")
            if snapshot.activate:
                cursor.execute(
                    """
                    UPDATE ui_knowledge_snapshots SET is_active = false
                    WHERE project_id = %s AND environment_id = %s
                      AND deployment_revision = %s AND is_active
                    """,
                    (snapshot.project_id, snapshot.environment_id, snapshot.deployment_revision),
                )
            cursor.execute(
                """
                INSERT INTO ui_knowledge_snapshots (
                    ui_knowledge_snapshot_id, project_id, environment_id,
                    deployment_revision, snapshot_version, review_status,
                    reviewed_by, payload_digest, is_active
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.project_id,
                    snapshot.environment_id,
                    snapshot.deployment_revision,
                    snapshot.snapshot_version,
                    snapshot.review_status,
                    snapshot.reviewed_by,
                    digest,
                    snapshot.activate,
                ),
            )
            for target in snapshot.targets:
                cursor.execute(
                    """
                    INSERT INTO ui_knowledge_targets (
                        ui_knowledge_snapshot_id, project_id, target_ref,
                        business_name, screen_name, trigger_path, source_fact_refs
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        snapshot.snapshot_id,
                        snapshot.project_id,
                        target.target_ref,
                        target.business_name,
                        target.screen_name,
                        target.trigger_path,
                        _json(list(target.source_fact_refs)),
                    ),
                )
                for candidate in target.candidates:
                    locator = candidate.locator
                    if locator.strategy is None or locator.value is None:
                        raise RuntimeError("Validated UI Knowledge lost a concrete Locator")
                    cursor.execute(
                        """
                        INSERT INTO ui_locator_candidates (
                            locator_candidate_id, ui_knowledge_snapshot_id, project_id,
                            target_ref, strategy, locator_value, accessible_name,
                            exact_match, priority, reliability_score, source
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            candidate.candidate_id,
                            snapshot.snapshot_id,
                            snapshot.project_id,
                            target.target_ref,
                            locator.strategy.value,
                            locator.value,
                            locator.name,
                            locator.exact,
                            candidate.priority,
                            candidate.reliability_score,
                            candidate.source,
                        ),
                    )
        return UiKnowledgeSnapshotRecord(
            True, snapshot.snapshot_id, snapshot.review_status, snapshot.activate
        )

    def load_approved(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        environment_id: str | None = None,
        deployment_revision: str | None = None,
    ) -> UiKnowledgeSnapshot:
        return self._load_snapshot(
            project_id=project_id,
            snapshot_id=snapshot_id,
            environment_id=environment_id,
            deployment_revision=deployment_revision,
            require_approved=True,
        )

    def load(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        environment_id: str | None = None,
        deployment_revision: str | None = None,
    ) -> UiKnowledgeSnapshot:
        return self._load_snapshot(
            project_id=project_id,
            snapshot_id=snapshot_id,
            environment_id=environment_id,
            deployment_revision=deployment_revision,
            require_approved=False,
        )

    def review(
        self,
        *,
        project_id: str,
        source_snapshot_id: str,
        result_snapshot_id: str,
        result_snapshot_version: str,
        review_event_id: str,
        decision: str,
        reviewed_by: str,
        activate: bool = False,
        reason: str | None = None,
    ) -> UiKnowledgeReviewRecord:
        if decision not in {"approved", "rejected"}:
            raise ValueError("UI Knowledge review decision is invalid")
        if activate and decision != "approved":
            raise ValueError("Only approved UI Knowledge may become active")
        if any(
            not value.strip()
            for value in (
                project_id,
                source_snapshot_id,
                result_snapshot_id,
                result_snapshot_version,
                review_event_id,
                reviewed_by,
            )
        ):
            raise ValueError("UI Knowledge review fields must not be blank")
        if reason is not None and not reason.strip():
            raise ValueError("UI Knowledge review reason must not be blank")
        if source_snapshot_id == result_snapshot_id:
            raise ValueError("UI Knowledge review must create a new result Snapshot")
        source = self.load(project_id=project_id, snapshot_id=source_snapshot_id)
        if source.review_status != "draft":
            raise ValueError("UI Knowledge review source must be a draft Snapshot")
        result = UiKnowledgeSnapshot(
            snapshot_id=result_snapshot_id,
            project_id=source.project_id,
            environment_id=source.environment_id,
            deployment_revision=source.deployment_revision,
            snapshot_version=result_snapshot_version,
            review_status=decision,
            reviewed_by=reviewed_by,
            targets=source.targets,
            activate=activate,
        )
        payload = {
            "review_event_id": review_event_id,
            "project_id": project_id,
            "source_snapshot_id": source.snapshot_id,
            "result_snapshot_id": result.snapshot_id,
            "decision": decision,
            "reviewed_by": reviewed_by,
            "reason": reason,
            "activate": activate,
        }
        digest = hashlib.sha256(_json(payload).encode()).hexdigest()
        expected = (
            project_id,
            source.snapshot_id,
            result.snapshot_id,
            decision,
            reviewed_by,
            reason,
            digest,
        )
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT event.project_id, event.source_ui_knowledge_snapshot_id,
                       event.result_ui_knowledge_snapshot_id, event.decision,
                       event.reviewed_by, event.reason, event.payload_digest,
                       result.is_active
                FROM ui_knowledge_review_events AS event
                JOIN ui_knowledge_snapshots AS result
                  ON result.ui_knowledge_snapshot_id = event.result_ui_knowledge_snapshot_id
                 AND result.project_id = event.project_id
                WHERE event.ui_knowledge_review_event_id = %s
                """,
                (review_event_id,),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if tuple(existing[:7]) != expected:
                    raise PersistenceConflictError(
                        f"UI Knowledge Review Event has different content: {review_event_id}"
                    )
                stored_result = self.store(result)
                return UiKnowledgeReviewRecord(
                    False,
                    review_event_id,
                    source.snapshot_id,
                    result.snapshot_id,
                    decision,
                    stored_result.active,
                )
            stored = self.store(result)
            cursor.execute(
                """
                INSERT INTO ui_knowledge_review_events (
                    ui_knowledge_review_event_id, project_id,
                    source_ui_knowledge_snapshot_id,
                    result_ui_knowledge_snapshot_id, decision, reviewed_by,
                    reason, payload_digest
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    review_event_id,
                    project_id,
                    source.snapshot_id,
                    result.snapshot_id,
                    decision,
                    reviewed_by,
                    reason,
                    digest,
                ),
            )
        return UiKnowledgeReviewRecord(
            True,
            review_event_id,
            source.snapshot_id,
            result.snapshot_id,
            decision,
            stored.active,
        )

    def _load_snapshot(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        environment_id: str | None,
        deployment_revision: str | None,
        require_approved: bool,
    ) -> UiKnowledgeSnapshot:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT environment_id, deployment_revision, snapshot_version,
                       review_status, reviewed_by, is_active, payload_digest
                FROM ui_knowledge_snapshots
                WHERE ui_knowledge_snapshot_id = %s AND project_id = %s
                  AND (%s = false OR review_status = 'approved')
                """,
                (snapshot_id, project_id, require_approved),
            )
            row = cursor.fetchone()
            if row is None:
                state = "approved " if require_approved else ""
                raise ValueError(f"UI Knowledge Snapshot is not {state}in requested project")
            if environment_id is not None and str(row[0]) != environment_id:
                raise ValueError("UI Knowledge Snapshot belongs to a different Environment")
            if deployment_revision is not None and str(row[1]) != deployment_revision:
                raise ValueError("UI Knowledge Snapshot belongs to a different Deployment")
            cursor.execute(
                """
                SELECT target.target_ref, target.business_name, target.screen_name,
                       target.trigger_path, target.source_fact_refs,
                       candidate.locator_candidate_id, candidate.strategy,
                       candidate.locator_value, candidate.accessible_name,
                       candidate.exact_match, candidate.priority,
                       candidate.reliability_score, candidate.source
                FROM ui_knowledge_targets AS target
                JOIN ui_locator_candidates AS candidate
                  ON candidate.ui_knowledge_snapshot_id = target.ui_knowledge_snapshot_id
                 AND candidate.project_id = target.project_id
                 AND candidate.target_ref = target.target_ref
                WHERE target.ui_knowledge_snapshot_id = %s AND target.project_id = %s
                ORDER BY target.target_ref, candidate.priority
                """,
                (snapshot_id, project_id),
            )
            rows = cursor.fetchall()
        targets: dict[str, dict[str, object]] = {}
        for item in rows:
            target = targets.setdefault(
                str(item[0]),
                {
                    "target_ref": str(item[0]),
                    "business_name": str(item[1]),
                    "screen_name": str(item[2]),
                    "trigger_path": str(item[3]) if item[3] is not None else None,
                    "source_fact_refs": cast(list[object], item[4]),
                    "candidates": [],
                },
            )
            candidates = cast(list[object], target["candidates"])
            locator: dict[str, object] = {
                "strategy": str(item[6]),
                "value": str(item[7]),
                "exact": bool(item[9]),
            }
            if item[8] is not None:
                locator["name"] = str(item[8])
            candidates.append(
                {
                    "candidate_id": str(item[5]),
                    "locator": locator,
                    "priority": int(item[10]),
                    "reliability_score": float(item[11]),
                    "source": str(item[12]),
                }
            )
        snapshot = UiKnowledgeSnapshot.from_dict(
            {
                "snapshot_id": snapshot_id,
                "project_id": project_id,
                "environment_id": str(row[0]),
                "deployment_revision": str(row[1]),
                "snapshot_version": str(row[2]),
                "review_status": str(row[3]),
                "reviewed_by": str(row[4]) if row[4] is not None else None,
                "activate": bool(row[5]),
                "targets": list(targets.values()),
            }
        )
        payload = _snapshot_payload(snapshot)
        digest = hashlib.sha256(_json(payload).encode()).hexdigest()
        if digest != str(row[6]):
            raise PersistenceConflictError(
                f"UI Knowledge Snapshot normalized identity differs: {snapshot_id}"
            )
        return snapshot


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _snapshot_payload(snapshot: UiKnowledgeSnapshot) -> dict[str, object]:
    payload = snapshot.to_dict()
    payload.pop("activate")
    targets = cast(list[object], payload["targets"])
    normalized_targets: list[dict[str, object]] = []
    for raw_target in targets:
        target = cast(dict[str, object], raw_target)
        candidates = cast(list[object], target["candidates"])
        target["candidates"] = sorted(
            candidates,
            key=lambda raw: (
                cast(int, cast(dict[str, object], raw)["priority"]),
                str(cast(dict[str, object], raw)["candidate_id"]),
            ),
        )
        normalized_targets.append(target)
    payload["targets"] = sorted(
        normalized_targets,
        key=lambda target: str(target["target_ref"]),
    )
    return payload
