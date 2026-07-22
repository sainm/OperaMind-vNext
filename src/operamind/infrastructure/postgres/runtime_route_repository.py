"""Immutable normalized persistence for runtime Route evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from psycopg import Connection, Cursor

from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres.artifact_repository import ArtifactRepository
from operamind.infrastructure.postgres.code_graph_repository import CodeGraphSnapshotRepository
from operamind.infrastructure.postgres.errors import PersistenceConflictError
from operamind.infrastructure.postgres.unresolved_evidence_repository import (
    UnresolvedEvidenceRepository,
)


@dataclass(frozen=True, slots=True)
class RuntimeRouteEvidencePublishResult:
    runtime_route_evidence_id: str
    created: bool
    observation_count: int
    resolved_count: int


class RuntimeRouteEvidenceRepository:
    """Persist sanitized observations and their fail-closed reconciliation decisions."""

    def __init__(self, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._connection = connection
        self._contracts = contracts
        self._artifacts = ArtifactRepository(connection, contracts)
        self._graphs = CodeGraphSnapshotRepository(connection, contracts)

    def publish(self, artifact: dict[str, Any]) -> RuntimeRouteEvidencePublishResult:
        self._contracts.validate_artifact(artifact)
        evidence_id, observations, resolutions = _validate_artifact(artifact)
        graph = self._graphs.get(str(artifact["code_graph_snapshot_id"]))
        if graph is None:
            raise ValueError("Runtime Route Evidence base Code Graph does not exist")
        expected_scope = (
            artifact["project_id"],
            artifact["repository_id"],
            artifact["repository_revision"],
        )
        actual_scope = (graph["project_id"], graph["repository_id"], graph["repository_revision"])
        if expected_scope != actual_scope:
            raise ValueError("Runtime Route Evidence scope differs from its base Code Graph")
        UnresolvedEvidenceRepository(self._connection, self._contracts).ensure_for_graph(graph)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            existing = self._load_result(cursor, evidence_id)
            if existing is not None:
                if self._artifacts.get_for_share(evidence_id) != artifact:
                    raise PersistenceConflictError(
                        f"Runtime Route Evidence identity has different content: {evidence_id}"
                    )
                self._validate_rows(cursor, artifact)
                return existing
            self._artifacts.store(
                artifact_id=evidence_id,
                project_id=str(artifact["project_id"]),
                analysis_case_id=None,
                artifact=artifact,
            )
            cursor.execute(
                """
                INSERT INTO runtime_route_evidence (
                    runtime_route_evidence_id, project_id, repository_id,
                    repository_revision, code_graph_snapshot_id, browser_run_id,
                    captured_at, source_evidence_refs, observation_count, resolved_count
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                """,
                (
                    evidence_id,
                    artifact["project_id"],
                    artifact["repository_id"],
                    artifact["repository_revision"],
                    artifact["code_graph_snapshot_id"],
                    artifact["browser_run_id"],
                    artifact["captured_at"],
                    _json(artifact["source_evidence_refs"]),
                    len(observations),
                    sum(item["status"] == "resolved" for item in resolutions),
                ),
            )
            for observation in observations:
                cursor.execute(
                    """
                    INSERT INTO runtime_route_observations (
                        runtime_route_evidence_id, project_id, observation_id,
                        scenario_id, event_kind, method, path, source_action_id,
                        source_route_ref, evidence_ref
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        evidence_id,
                        artifact["project_id"],
                        observation["observation_id"],
                        observation["scenario_id"],
                        observation["event_kind"],
                        observation["method"],
                        observation["path"],
                        observation.get("source_action_id"),
                        observation.get("source_route_ref"),
                        observation["evidence_ref"],
                    ),
                )
            for resolution in resolutions:
                cursor.execute(
                    """
                    INSERT INTO runtime_route_resolutions (
                        runtime_route_evidence_id, project_id, observation_id,
                        status, reason, source_route_ref, endpoint_ref,
                        candidate_endpoint_refs
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        evidence_id,
                        artifact["project_id"],
                        resolution["observation_id"],
                        resolution["status"],
                        resolution["reason"],
                        resolution.get("source_route_ref"),
                        resolution.get("endpoint_ref"),
                        _json(resolution["candidate_endpoint_refs"]),
                    ),
                )
            self._validate_rows(cursor, artifact)
            result = self._load_result(cursor, evidence_id)
            if result is None:
                raise RuntimeError("Runtime Route Evidence disappeared during publication")
            return RuntimeRouteEvidencePublishResult(
                runtime_route_evidence_id=result.runtime_route_evidence_id,
                created=True,
                observation_count=result.observation_count,
                resolved_count=result.resolved_count,
            )

    def get(self, runtime_route_evidence_id: str) -> dict[str, Any] | None:
        if not runtime_route_evidence_id.strip():
            raise ValueError("runtime_route_evidence_id must not be blank")
        with self._connection.cursor() as cursor:
            if self._load_result(cursor, runtime_route_evidence_id) is None:
                return None
        artifact = self._artifacts.get(runtime_route_evidence_id)
        if artifact is None:
            raise PersistenceConflictError("Runtime Route Evidence Artifact disappeared")
        with self._connection.cursor() as cursor:
            self._validate_rows(cursor, artifact)
        return artifact

    @staticmethod
    def _load_result(
        cursor: Cursor[Any], evidence_id: str
    ) -> RuntimeRouteEvidencePublishResult | None:
        cursor.execute(
            """
            SELECT runtime_route_evidence_id, observation_count, resolved_count
            FROM runtime_route_evidence
            WHERE runtime_route_evidence_id = %s
            """,
            (evidence_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return RuntimeRouteEvidencePublishResult(
            runtime_route_evidence_id=str(row[0]),
            created=False,
            observation_count=int(row[1]),
            resolved_count=int(row[2]),
        )

    @staticmethod
    def _validate_rows(cursor: Cursor[Any], artifact: dict[str, Any]) -> None:
        evidence_id = str(artifact["runtime_route_evidence_id"])
        observations = cast(list[dict[str, Any]], artifact["observations"])
        resolutions = cast(list[dict[str, Any]], artifact["resolutions"])
        cursor.execute(
            """
            SELECT project_id, repository_id, repository_revision,
                   code_graph_snapshot_id, browser_run_id, captured_at,
                   source_evidence_refs, observation_count, resolved_count
            FROM runtime_route_evidence
            WHERE runtime_route_evidence_id = %s
            """,
            (evidence_id,),
        )
        header = cursor.fetchone()
        if header is None:
            raise PersistenceConflictError("Runtime Route Evidence header disappeared")
        if (
            str(header[0]),
            str(header[1]),
            str(header[2]),
            str(header[3]),
            str(header[4]),
            header[5],
            tuple(str(value) for value in cast(list[object], header[6])),
            int(header[7]),
            int(header[8]),
        ) != (
            str(artifact["project_id"]),
            str(artifact["repository_id"]),
            str(artifact["repository_revision"]),
            str(artifact["code_graph_snapshot_id"]),
            str(artifact["browser_run_id"]),
            datetime.fromisoformat(str(artifact["captured_at"]).replace("Z", "+00:00")),
            tuple(str(value) for value in cast(list[object], artifact["source_evidence_refs"])),
            len(observations),
            sum(item["status"] == "resolved" for item in resolutions),
        ):
            raise PersistenceConflictError("Runtime Route Evidence header differs")
        cursor.execute(
            """
            SELECT observation_id, scenario_id, event_kind, method, path,
                   source_action_id, source_route_ref, evidence_ref
            FROM runtime_route_observations
            WHERE runtime_route_evidence_id = %s
            ORDER BY observation_id
            """,
            (evidence_id,),
        )
        actual_observations = tuple(tuple(row) for row in cursor.fetchall())
        expected_observations = tuple(
            sorted(
                (
                    item["observation_id"],
                    item["scenario_id"],
                    item["event_kind"],
                    item["method"],
                    item["path"],
                    item.get("source_action_id"),
                    item.get("source_route_ref"),
                    item["evidence_ref"],
                )
                for item in observations
            )
        )
        cursor.execute(
            """
            SELECT observation_id, status, reason, source_route_ref,
                   endpoint_ref, candidate_endpoint_refs
            FROM runtime_route_resolutions
            WHERE runtime_route_evidence_id = %s
            ORDER BY observation_id
            """,
            (evidence_id,),
        )
        actual_resolutions = tuple(
            (
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                tuple(str(value) for value in cast(list[object], row[5])),
            )
            for row in cursor.fetchall()
        )
        expected_resolutions = tuple(
            sorted(
                (
                    item["observation_id"],
                    item["status"],
                    item["reason"],
                    item.get("source_route_ref"),
                    item.get("endpoint_ref"),
                    tuple(str(value) for value in item["candidate_endpoint_refs"]),
                )
                for item in resolutions
            )
        )
        if (
            actual_observations != expected_observations
            or actual_resolutions != expected_resolutions
        ):
            raise PersistenceConflictError("Runtime Route Evidence normalized rows differ")


def _validate_artifact(
    artifact: dict[str, Any],
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    if artifact.get("artifact_type") != "RuntimeRouteEvidence":
        raise ValueError("Expected RuntimeRouteEvidence")
    evidence_id = str(artifact["runtime_route_evidence_id"])
    observations = cast(list[dict[str, Any]], artifact["observations"])
    resolutions = cast(list[dict[str, Any]], artifact["resolutions"])
    observation_ids = [str(item["observation_id"]) for item in observations]
    resolution_ids = [str(item["observation_id"]) for item in resolutions]
    if len(observation_ids) != len(set(observation_ids)):
        raise ValueError("Runtime Route observation IDs must be unique")
    if sorted(observation_ids) != sorted(resolution_ids):
        raise ValueError("Every Runtime Route observation requires exactly one resolution")
    return evidence_id, observations, resolutions


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
