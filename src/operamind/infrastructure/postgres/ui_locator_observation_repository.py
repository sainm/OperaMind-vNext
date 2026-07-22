"""Append-only persistence for runtime Locator observation runs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from psycopg import Connection

from operamind.domain import UiKnowledgeSnapshot, UiRuntimeObservationResult
from operamind.infrastructure.postgres.errors import PersistenceConflictError


@dataclass(frozen=True, slots=True)
class UiLocatorObservationRunRecord:
    created: bool
    run_id: str
    status: str
    result_snapshot_id: str | None


class UiLocatorObservationRepository:
    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def store(
        self,
        *,
        run_id: str,
        source: UiKnowledgeSnapshot,
        result: UiRuntimeObservationResult,
    ) -> UiLocatorObservationRunRecord:
        if not run_id.strip():
            raise ValueError("UI Locator Observation Run ID must not be blank")
        result_snapshot_id = result.snapshot.snapshot_id if result.snapshot is not None else None
        payload = {
            "run_id": run_id,
            "source_snapshot_id": source.snapshot_id,
            "result": {
                "status": result.status,
                "result_snapshot_id": result_snapshot_id,
                "observations": [item.to_dict() for item in result.observations],
                "issues": [item.to_dict() for item in result.issues],
                "evidence": [item.to_dict() for item in result.evidence],
            },
        }
        digest = hashlib.sha256(_json(payload).encode()).hexdigest()
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT project_id, source_ui_knowledge_snapshot_id,
                       result_ui_knowledge_snapshot_id, environment_id,
                       deployment_revision, status, issues, payload_digest
                FROM ui_locator_observation_runs
                WHERE ui_locator_observation_run_id = %s
                """,
                (run_id,),
            )
            existing = cursor.fetchone()
            expected = (
                source.project_id,
                source.snapshot_id,
                result_snapshot_id,
                source.environment_id,
                source.deployment_revision,
                result.status,
                [item.to_dict() for item in result.issues],
                digest,
            )
            if existing is not None:
                if tuple(existing) != expected:
                    raise PersistenceConflictError(
                        f"UI Locator Observation Run has different content: {run_id}"
                    )
                cursor.execute(
                    """
                    SELECT ui_locator_observation_id, ui_locator_observation_run_id,
                           project_id, source_ui_knowledge_snapshot_id, target_ref,
                           locator_candidate_id, strategy, locator_value,
                           accessible_name, exact_match, status, match_count,
                           visible_count, discovered, evidence_ref
                    FROM ui_locator_observations
                    WHERE ui_locator_observation_run_id = %s AND project_id = %s
                    ORDER BY ui_locator_observation_id
                    """,
                    (run_id, source.project_id),
                )
                actual_observations = tuple(tuple(row) for row in cursor.fetchall())
                expected_observations = tuple(
                    sorted(
                        (
                            observation.observation_id,
                            run_id,
                            source.project_id,
                            source.snapshot_id,
                            observation.target_ref,
                            observation.candidate_id,
                            observation.locator.strategy.value,
                            observation.locator.value,
                            observation.locator.name,
                            observation.locator.exact,
                            observation.status.value,
                            observation.match_count,
                            observation.visible_count,
                            observation.discovered,
                            f"ui-observation://{run_id}/{observation.observation_id}",
                        )
                        for observation in result.observations
                        if observation.locator.strategy is not None
                        and observation.locator.value is not None
                    )
                )
                if actual_observations != expected_observations:
                    raise PersistenceConflictError(f"UI Locator Observation rows differ: {run_id}")
                cursor.execute(
                    """
                    SELECT evidence_id, ui_locator_observation_run_id,
                           ui_locator_observation_id, project_id,
                           source_ui_knowledge_snapshot_id, target_ref,
                           evidence_ref, content_digest, sanitized
                    FROM ui_locator_observation_evidence
                    WHERE ui_locator_observation_run_id = %s AND project_id = %s
                    ORDER BY evidence_id
                    """,
                    (run_id, source.project_id),
                )
                actual_evidence = tuple(tuple(row) for row in cursor.fetchall())
                expected_evidence = tuple(
                    sorted(
                        (
                            evidence.evidence_id,
                            run_id,
                            evidence.observation_id,
                            source.project_id,
                            source.snapshot_id,
                            evidence.target_ref,
                            evidence.evidence_ref,
                            evidence.content_digest,
                            evidence.sanitized,
                        )
                        for evidence in result.evidence
                    )
                )
                if actual_evidence != expected_evidence:
                    raise PersistenceConflictError(
                        f"UI Locator Observation Evidence rows differ: {run_id}"
                    )
                return UiLocatorObservationRunRecord(
                    False, run_id, result.status, result_snapshot_id
                )
            cursor.execute(
                """
                INSERT INTO ui_locator_observation_runs (
                    ui_locator_observation_run_id, project_id,
                    source_ui_knowledge_snapshot_id, result_ui_knowledge_snapshot_id,
                    environment_id, deployment_revision, status, issues, payload_digest
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                """,
                (
                    run_id,
                    source.project_id,
                    source.snapshot_id,
                    result_snapshot_id,
                    source.environment_id,
                    source.deployment_revision,
                    result.status,
                    _json([item.to_dict() for item in result.issues]),
                    digest,
                ),
            )
            for observation in result.observations:
                locator = observation.locator
                if locator.strategy is None or locator.value is None:
                    raise RuntimeError("Runtime Observation lost a concrete Locator")
                cursor.execute(
                    """
                    INSERT INTO ui_locator_observations (
                        ui_locator_observation_id, ui_locator_observation_run_id,
                        project_id, source_ui_knowledge_snapshot_id, target_ref,
                        locator_candidate_id, strategy, locator_value, accessible_name,
                        exact_match, status, match_count, visible_count, discovered,
                        evidence_ref
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        observation.observation_id,
                        run_id,
                        source.project_id,
                        source.snapshot_id,
                        observation.target_ref,
                        observation.candidate_id,
                        locator.strategy.value,
                        locator.value,
                        locator.name,
                        locator.exact,
                        observation.status.value,
                        observation.match_count,
                        observation.visible_count,
                        observation.discovered,
                        f"ui-observation://{run_id}/{observation.observation_id}",
                    ),
                )
            observation_targets = {
                observation.observation_id: observation.target_ref
                for observation in result.observations
            }
            for evidence in result.evidence:
                if observation_targets.get(evidence.observation_id) != evidence.target_ref:
                    raise ValueError(
                        "UI Locator Observation Evidence differs from its Observation"
                    )
                cursor.execute(
                    """
                    INSERT INTO ui_locator_observation_evidence (
                        evidence_id, ui_locator_observation_run_id,
                        ui_locator_observation_id, project_id,
                        source_ui_knowledge_snapshot_id, target_ref,
                        evidence_ref, content_digest, sanitized
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        evidence.evidence_id,
                        run_id,
                        evidence.observation_id,
                        source.project_id,
                        source.snapshot_id,
                        evidence.target_ref,
                        evidence.evidence_ref,
                        evidence.content_digest,
                        evidence.sanitized,
                    ),
                )
        return UiLocatorObservationRunRecord(True, run_id, result.status, result_snapshot_id)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
