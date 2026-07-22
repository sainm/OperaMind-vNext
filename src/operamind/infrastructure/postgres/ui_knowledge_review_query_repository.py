"""Read-only review queue for UI Knowledge drafts and runtime evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from psycopg import Connection

from operamind.infrastructure.postgres.ui_knowledge_repository import (
    UiKnowledgeRepository,
)


class UiKnowledgeReviewQueryRepository:
    """Build integrity-checked review models without changing canonical state."""

    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection
        self._knowledge = UiKnowledgeRepository(connection)

    def review_queue(self, *, project_id: str) -> dict[str, object]:
        if not project_id.strip():
            raise ValueError("UI Knowledge review project must not be blank")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT snapshot.ui_knowledge_snapshot_id
                FROM ui_knowledge_snapshots AS snapshot
                WHERE snapshot.project_id = %s
                  AND snapshot.review_status = 'draft'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM ui_knowledge_review_events AS event
                      WHERE event.project_id = snapshot.project_id
                        AND event.source_ui_knowledge_snapshot_id =
                            snapshot.ui_knowledge_snapshot_id
                  )
                ORDER BY snapshot.created_at DESC,
                         snapshot.ui_knowledge_snapshot_id DESC
                """,
                (project_id,),
            )
            draft_ids = [str(row[0]) for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT snapshot.ui_knowledge_snapshot_id,
                       snapshot.snapshot_version,
                       snapshot.review_status,
                       snapshot.reviewed_by,
                       snapshot.environment_id,
                       snapshot.deployment_revision,
                       snapshot.is_active,
                       snapshot.created_at,
                       event.reason,
                       event.source_ui_knowledge_snapshot_id
                FROM ui_knowledge_snapshots AS snapshot
                LEFT JOIN ui_knowledge_review_events AS event
                  ON event.project_id = snapshot.project_id
                 AND event.result_ui_knowledge_snapshot_id =
                     snapshot.ui_knowledge_snapshot_id
                WHERE snapshot.project_id = %s
                ORDER BY snapshot.created_at DESC,
                         snapshot.ui_knowledge_snapshot_id DESC
                """,
                (project_id,),
            )
            versions = [
                {
                    "snapshot_id": str(row[0]),
                    "snapshot_version": str(row[1]),
                    "review_status": str(row[2]),
                    "reviewed_by": str(row[3]) if row[3] is not None else None,
                    "environment_id": str(row[4]),
                    "deployment_revision": str(row[5]),
                    "active": bool(row[6]),
                    "created_at": cast(datetime, row[7]).isoformat(),
                    "reason": str(row[8]) if row[8] is not None else None,
                    "source_snapshot_id": str(row[9]) if row[9] is not None else None,
                }
                for row in cursor.fetchall()
            ]
        drafts = [self._draft(project_id=project_id, snapshot_id=value) for value in draft_ids]
        return {
            "project_id": project_id,
            "drafts": drafts,
            "versions": versions,
            "draft_count": len(drafts),
        }

    def evidence(
        self, *, project_id: str, snapshot_id: str, evidence_id: str
    ) -> dict[str, str | bool]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT evidence.evidence_ref, evidence.content_digest,
                       evidence.sanitized, evidence.target_ref
                FROM ui_locator_observation_evidence AS evidence
                JOIN ui_locator_observation_runs AS run
                  ON run.ui_locator_observation_run_id =
                     evidence.ui_locator_observation_run_id
                 AND run.project_id = evidence.project_id
                WHERE evidence.project_id = %s
                  AND run.result_ui_knowledge_snapshot_id = %s
                  AND evidence.evidence_id = %s
                """,
                (project_id, snapshot_id, evidence_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise ValueError("UI Knowledge review Evidence does not exist")
        return {
            "evidence_ref": str(row[0]),
            "content_digest": str(row[1]),
            "sanitized": bool(row[2]),
            "target_ref": str(row[3]),
        }

    def _draft(self, *, project_id: str, snapshot_id: str) -> dict[str, object]:
        snapshot = self._knowledge.load(project_id=project_id, snapshot_id=snapshot_id)
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT ui_locator_observation_run_id, status, issues,
                       source_ui_knowledge_snapshot_id, created_at
                FROM ui_locator_observation_runs
                WHERE project_id = %s
                  AND result_ui_knowledge_snapshot_id = %s
                ORDER BY created_at DESC, ui_locator_observation_run_id DESC
                LIMIT 1
                """,
                (project_id, snapshot_id),
            )
            run = cursor.fetchone()
            observations: dict[tuple[str, str], dict[str, object]] = {}
            evidence: dict[str, dict[str, object]] = {}
            if run is not None:
                run_id = str(run[0])
                cursor.execute(
                    """
                    SELECT target_ref, locator_candidate_id, status,
                           match_count, visible_count, discovered, observed_at
                    FROM ui_locator_observations
                    WHERE project_id = %s
                      AND ui_locator_observation_run_id = %s
                    ORDER BY target_ref, locator_candidate_id
                    """,
                    (project_id, run_id),
                )
                observations = {
                    (str(row[0]), str(row[1])): {
                        "status": str(row[2]),
                        "match_count": int(row[3]),
                        "visible_count": int(row[4]),
                        "discovered": bool(row[5]),
                        "observed_at": cast(datetime, row[6]).isoformat(),
                    }
                    for row in cursor.fetchall()
                }
                cursor.execute(
                    """
                    SELECT evidence_id, target_ref, evidence_ref,
                           content_digest, sanitized
                    FROM ui_locator_observation_evidence
                    WHERE project_id = %s
                      AND ui_locator_observation_run_id = %s
                    ORDER BY target_ref
                    """,
                    (project_id, run_id),
                )
                evidence = {
                    str(row[1]): {
                        "evidence_id": str(row[0]),
                        "evidence_ref": str(row[2]),
                        "content_digest": str(row[3]),
                        "sanitized": bool(row[4]),
                    }
                    for row in cursor.fetchall()
                }
        targets: list[dict[str, object]] = []
        for target in snapshot.targets:
            candidates: list[dict[str, object]] = []
            for candidate in target.candidates:
                item = candidate.to_dict()
                item["observation"] = observations.get(
                    (target.target_ref, candidate.candidate_id)
                )
                candidates.append(item)
            targets.append(
                {
                    "target_ref": target.target_ref,
                    "business_name": target.business_name,
                    "screen_name": target.screen_name,
                    "trigger_path": target.trigger_path,
                    "source_fact_refs": list(target.source_fact_refs),
                    "candidates": candidates,
                    "evidence": evidence.get(target.target_ref),
                }
            )
        observation = None
        if run is not None:
            observation = {
                "run_id": str(run[0]),
                "status": str(run[1]),
                "issues": cast(list[object], run[2]),
                "source_snapshot_id": str(run[3]),
                "created_at": cast(datetime, run[4]).isoformat(),
            }
        return {
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_version": snapshot.snapshot_version,
            "environment_id": snapshot.environment_id,
            "deployment_revision": snapshot.deployment_revision,
            "review_status": snapshot.review_status,
            "observation": observation,
            "targets": targets,
        }
