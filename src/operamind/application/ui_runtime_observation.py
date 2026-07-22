"""Observe a deployed page and persist a new draft UI Knowledge version."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg import Connection

from operamind.domain import UiRuntimeObservationResult
from operamind.infrastructure.browser import UiKnowledgeRuntimeObserver
from operamind.infrastructure.postgres import (
    UiKnowledgeRepository,
    UiLocatorObservationRepository,
    UiLocatorObservationRunRecord,
)


@dataclass(frozen=True, slots=True)
class UiRuntimeObservationRequest:
    project_id: str
    source_snapshot_id: str
    observation_run_id: str
    result_snapshot_id: str
    result_snapshot_version: str
    storage_state: Path | None = None

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (
                self.project_id,
                self.source_snapshot_id,
                self.observation_run_id,
                self.result_snapshot_id,
                self.result_snapshot_version,
            )
        ):
            raise ValueError("UI Runtime Observation request fields must not be blank")
        if self.source_snapshot_id == self.result_snapshot_id:
            raise ValueError("UI Runtime Observation must create a distinct result Snapshot")


@dataclass(frozen=True, slots=True)
class UiRuntimeObservationServiceResult:
    record: UiLocatorObservationRunRecord
    observation: UiRuntimeObservationResult


class UiRuntimeObservationService:
    def __init__(
        self,
        *,
        connection: Connection[Any],
        observer: UiKnowledgeRuntimeObserver,
    ) -> None:
        self._connection = connection
        self._observer = observer
        self._knowledge = UiKnowledgeRepository(connection)
        self._observations = UiLocatorObservationRepository(connection)

    def observe(
        self,
        request: UiRuntimeObservationRequest,
    ) -> UiRuntimeObservationServiceResult:
        source = self._knowledge.load(
            project_id=request.project_id,
            snapshot_id=request.source_snapshot_id,
        )
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT environment.base_url
                FROM ui_deployments AS deployment
                JOIN ui_environments AS environment
                  ON environment.environment_id = deployment.environment_id
                 AND environment.project_id = deployment.project_id
                WHERE deployment.project_id = %s
                  AND deployment.environment_id = %s
                  AND deployment.deployment_revision = %s
                  AND deployment.status = 'ready'
                  AND environment.status = 'active'
                """,
                (
                    source.project_id,
                    source.environment_id,
                    source.deployment_revision,
                ),
            )
            row = cursor.fetchone()
        if row is None:
            raise ValueError("Runtime Observation requires the exact active ready Deployment")
        observation = self._observer.observe(
            source=source,
            base_url=str(row[0]),
            observation_run_id=request.observation_run_id,
            result_snapshot_id=request.result_snapshot_id,
            result_snapshot_version=request.result_snapshot_version,
            storage_state=request.storage_state,
        )
        if observation.snapshot is not None:
            actual_scope = (
                observation.snapshot.snapshot_id,
                observation.snapshot.project_id,
                observation.snapshot.environment_id,
                observation.snapshot.deployment_revision,
                observation.snapshot.snapshot_version,
                observation.snapshot.review_status,
                observation.snapshot.reviewed_by,
                observation.snapshot.activate,
            )
            expected_scope = (
                request.result_snapshot_id,
                source.project_id,
                source.environment_id,
                source.deployment_revision,
                request.result_snapshot_version,
                "draft",
                None,
                False,
            )
            if actual_scope != expected_scope:
                raise ValueError("Runtime Observation result Snapshot is outside requested scope")
        with self._connection.transaction():
            current_source = self._knowledge.load(
                project_id=request.project_id,
                snapshot_id=request.source_snapshot_id,
            )
            source_payload = source.to_dict()
            source_payload.pop("activate")
            current_payload = current_source.to_dict()
            current_payload.pop("activate")
            if current_payload != source_payload:
                raise ValueError("Runtime Observation source changed during browser inspection")
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT environment.base_url
                    FROM ui_deployments AS deployment
                    JOIN ui_environments AS environment
                      ON environment.environment_id = deployment.environment_id
                     AND environment.project_id = deployment.project_id
                    WHERE deployment.project_id = %s
                      AND deployment.environment_id = %s
                      AND deployment.deployment_revision = %s
                      AND deployment.status = 'ready'
                      AND environment.status = 'active'
                    FOR SHARE OF deployment, environment
                    """,
                    (
                        source.project_id,
                        source.environment_id,
                        source.deployment_revision,
                    ),
                )
                current_deployment = cursor.fetchone()
            if current_deployment is None or str(current_deployment[0]) != str(row[0]):
                raise ValueError("Runtime Observation Deployment changed during browser inspection")
            if observation.snapshot is not None:
                self._knowledge.store(observation.snapshot)
            record = self._observations.store(
                run_id=request.observation_run_id,
                source=source,
                result=observation,
            )
        return UiRuntimeObservationServiceResult(record=record, observation=observation)
