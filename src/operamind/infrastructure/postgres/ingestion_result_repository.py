"""Append-only DocumentIngestionResult state events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any, cast

from psycopg import Connection, Cursor

from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres.artifact_repository import ArtifactRepository
from operamind.infrastructure.postgres.errors import PersistenceConflictError


class DocumentIngestionStatus(StrEnum):
    """Contract v1 statuses allowed in the event projection."""

    INGESTING = "ingesting"
    INDEXING_RAG = "indexing_rag"
    READY_FOR_IMPACT = "ready_for_impact"
    NEEDS_REVIEW = "needs_review"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DocumentIngestionResultEvent:
    """One immutable status event and its validated Artifact payload."""

    event_id: str
    project_id: str
    ingestion_batch_id: str
    analysis_case_id: str
    previous_event_id: str | None
    previous_status: DocumentIngestionStatus | None
    artifact_id: str
    search_index_build_id: str | None
    status: DocumentIngestionStatus
    artifact: dict[str, Any]
    created_at: datetime


class DocumentIngestionResultRepository:
    """Persist a linear event chain without rewriting exchange Artifacts."""

    def __init__(self, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._connection = connection
        self._contracts = contracts
        self._artifacts = ArtifactRepository(connection, contracts)

    def append(
        self,
        *,
        event_id: str,
        project_id: str,
        ingestion_batch_id: str,
        analysis_case_id: str,
        expected_previous_event_id: str | None,
        artifact_id: str,
        search_index_build_id: str | None,
        status: DocumentIngestionStatus,
    ) -> bool:
        """Append one event; exact event replay is a no-op and stale writers fail."""

        required = (event_id, project_id, ingestion_batch_id, analysis_case_id, artifact_id)
        if any(not value.strip() for value in required):
            raise ValueError("Document ingestion event fields must not be blank")
        if expected_previous_event_id is not None and not expected_previous_event_id.strip():
            raise ValueError("expected_previous_event_id must not be blank")
        if search_index_build_id is not None and not search_index_build_id.strip():
            raise ValueError("search_index_build_id must not be blank")

        with self._connection.transaction(), self._connection.cursor() as cursor:
            self._lock_case(
                cursor,
                project_id=project_id,
                analysis_case_id=analysis_case_id,
            )
            artifact = self._artifacts.get_for_share(artifact_id)
            if artifact is None:
                raise ValueError(f"DocumentIngestionResult Artifact does not exist: {artifact_id}")
            self._validate_artifact_scope(
                artifact,
                project_id=project_id,
                ingestion_batch_id=ingestion_batch_id,
                analysis_case_id=analysis_case_id,
                status=status,
            )
            self._validate_event_artifact_binding(
                artifact,
                event_id=event_id,
                search_index_build_id=search_index_build_id,
            )
            self._validate_document_profile_bindings(
                cursor,
                event_project_id=project_id,
                artifact=artifact,
            )
            self._validate_build_artifact_binding(
                cursor,
                event_project_id=project_id,
                artifact=artifact,
                search_index_build_id=search_index_build_id,
            )
            existing = self._load_event_identity(cursor, event_id)
            expected_identity = (
                project_id,
                ingestion_batch_id,
                analysis_case_id,
                expected_previous_event_id,
                artifact_id,
                search_index_build_id,
                status.value,
            )
            if existing is not None:
                if existing != expected_identity:
                    raise PersistenceConflictError(
                        f"Ingestion result event ID has different content: {event_id}"
                    )
                return False

            cursor.execute(
                """
                SELECT ingestion_result_event_id, status
                FROM document_ingestion_result_events
                WHERE project_id = %s AND ingestion_batch_id = %s
                ORDER BY event_sequence DESC
                LIMIT 1
                """,
                (project_id, ingestion_batch_id),
            )
            latest = cursor.fetchone()
            current_event_id = str(latest[0]) if latest is not None else None
            current_status = DocumentIngestionStatus(str(latest[1])) if latest is not None else None
            if expected_previous_event_id != current_event_id:
                raise ValueError(
                    "Stale DocumentIngestionResult event: expected previous event "
                    f"{expected_previous_event_id!r}, current is {current_event_id!r}"
                )
            cursor.execute(
                """
                INSERT INTO document_ingestion_result_events (
                    ingestion_result_event_id,
                    project_id,
                    ingestion_batch_id,
                    analysis_case_id,
                    previous_event_id,
                    previous_status,
                    artifact_id,
                    search_index_build_id,
                    status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    event_id,
                    project_id,
                    ingestion_batch_id,
                    analysis_case_id,
                    current_event_id,
                    current_status.value if current_status is not None else None,
                    artifact_id,
                    search_index_build_id,
                    status.value,
                ),
            )
            stored = self._load_event_identity(cursor, event_id)
            if stored != expected_identity:
                raise PersistenceConflictError(
                    f"Ingestion result event ID has different content: {event_id}"
                )
        return True

    def get_latest(
        self,
        *,
        project_id: str,
        ingestion_batch_id: str,
    ) -> DocumentIngestionResultEvent | None:
        """Load and Contract-validate the latest event for one batch."""

        if not project_id.strip() or not ingestion_batch_id.strip():
            raise ValueError("Document ingestion event scope fields must not be blank")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                _EVENT_SELECT
                + """
                WHERE e.project_id = %s AND e.ingestion_batch_id = %s
                ORDER BY e.event_sequence DESC
                LIMIT 1
                """,
                (project_id, ingestion_batch_id),
            )
            row = cursor.fetchone()
            return self._validated_event(cursor, row) if row is not None else None

    def get_event(self, event_id: str) -> DocumentIngestionResultEvent | None:
        """Load one event by immutable ID for exact replay detection."""

        if not event_id.strip():
            raise ValueError("event_id must not be blank")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                _EVENT_SELECT
                + """
                WHERE e.ingestion_result_event_id = %s
                """,
                (event_id,),
            )
            row = cursor.fetchone()
            return self._validated_event(cursor, row) if row is not None else None

    @staticmethod
    def _lock_case(
        cursor: Cursor[Any],
        *,
        project_id: str,
        analysis_case_id: str,
    ) -> None:
        cursor.execute(
            """
            SELECT project_id
            FROM analysis_cases
            WHERE analysis_case_id = %s
            FOR UPDATE
            """,
            (analysis_case_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError(f"Analysis case does not exist: {analysis_case_id}")
        if str(row[0]) != project_id:
            raise ValueError("Analysis case does not belong to the ingestion project")

    def _validate_artifact_scope(
        self,
        artifact: dict[str, Any],
        *,
        project_id: str,
        ingestion_batch_id: str,
        analysis_case_id: str,
        status: DocumentIngestionStatus,
    ) -> None:
        self._contracts.validate_artifact(artifact)
        expected = (
            "DocumentIngestionResult",
            project_id,
            ingestion_batch_id,
            analysis_case_id,
            status.value,
        )
        actual = (
            artifact.get("artifact_type"),
            artifact.get("project_id"),
            artifact.get("ingestion_batch_id"),
            artifact.get("analysis_case_id"),
            artifact.get("status"),
        )
        if actual != expected:
            raise ValueError("DocumentIngestionResult Artifact does not match event scope")

    @staticmethod
    def _validate_event_artifact_binding(
        artifact: dict[str, Any],
        *,
        event_id: str,
        search_index_build_id: str | None,
    ) -> None:
        expected = (event_id, search_index_build_id)
        actual = (
            artifact.get("ingestion_result_event_id"),
            artifact.get("search_index_build_id"),
        )
        if actual != expected:
            raise PersistenceConflictError(
                "DocumentIngestionResult event and Artifact identities differ"
            )

    @staticmethod
    def _validate_build_artifact_binding(
        cursor: Cursor[Any],
        *,
        event_project_id: str,
        artifact: dict[str, Any],
        search_index_build_id: str | None,
    ) -> None:
        if search_index_build_id is None:
            return
        cursor.execute(
            """
            SELECT build.project_id,
                   build.document_snapshot_id,
                   build.embedding_profile_version_id,
                   profile.profile_id,
                   profile.semantic_version
            FROM search_index_builds AS build
            JOIN profile_versions AS profile
              ON profile.profile_version_id = build.embedding_profile_version_id
            WHERE build.search_index_build_id = %s
            """,
            (search_index_build_id,),
        )
        row = cursor.fetchone()
        expected = (
            event_project_id,
            artifact.get("target_snapshot_id"),
            artifact.get("embedding_profile_version_id"),
            artifact.get("embedding_profile_ref"),
        )
        actual = (
            str(row[0]) if row is not None else None,
            str(row[1]) if row is not None else None,
            str(row[2]) if row is not None else None,
            f"{row[3]}@{row[4]}" if row is not None else None,
        )
        if actual != expected:
            raise PersistenceConflictError(
                "DocumentIngestionResult Artifact drifted from Search Index Build"
            )
        cursor.execute(
            """
            SELECT 1
            FROM profile_activation_events
            WHERE project_id = %s
              AND binding_key = %s
              AND activated_profile_version_id = %s
            LIMIT 1
            """,
            (
                event_project_id,
                artifact.get("embedding_profile_binding_key"),
                artifact.get("embedding_profile_version_id"),
            ),
        )
        if cursor.fetchone() is None:
            raise PersistenceConflictError(
                "DocumentIngestionResult Embedding Profile activation is missing"
            )

    @staticmethod
    def _validate_document_profile_bindings(
        cursor: Cursor[Any],
        *,
        event_project_id: str,
        artifact: dict[str, Any],
    ) -> None:
        profile_values = cast(list[dict[str, object]], artifact["document_profiles"])
        expected_profiles = tuple(
            sorted(
                (
                    str(profile["profile_version_id"]),
                    str(profile["profile_ref"]),
                )
                for profile in profile_values
            )
        )
        if tuple(sorted(cast(list[str], artifact["document_profile_refs"]))) != tuple(
            sorted(profile_ref for _, profile_ref in expected_profiles)
        ):
            raise PersistenceConflictError(
                "DocumentIngestionResult document Profile refs differ from exact versions"
            )
        cursor.execute(
            """
            SELECT DISTINCT membership.profile_version_id,
                            profile.profile_id,
                            profile.semantic_version
            FROM snapshot_memberships AS membership
            JOIN profile_versions AS profile
              ON profile.profile_version_id = membership.profile_version_id
            WHERE membership.project_id = %s
              AND membership.document_snapshot_id = %s
            ORDER BY membership.profile_version_id
            """,
            (event_project_id, artifact["target_snapshot_id"]),
        )
        actual_profiles = tuple((str(row[0]), f"{row[1]}@{row[2]}") for row in cursor.fetchall())
        if actual_profiles != expected_profiles:
            raise PersistenceConflictError(
                "DocumentIngestionResult document Profiles drifted from Snapshot membership"
            )
        expected_activations = {
            (
                str(profile["activation_event_id"]),
                str(profile["binding_key"]),
                str(profile["profile_version_id"]),
            )
            for profile in profile_values
        }
        cursor.execute(
            """
            SELECT activation_event_id, binding_key, activated_profile_version_id
            FROM profile_activation_events
            WHERE project_id = %s
            """,
            (event_project_id,),
        )
        actual_activations = {(str(row[0]), str(row[1]), str(row[2])) for row in cursor.fetchall()}
        if not expected_activations.issubset(actual_activations):
            raise PersistenceConflictError(
                "DocumentIngestionResult document Profile activation is missing"
            )

    def _validated_event(
        self,
        cursor: Cursor[Any],
        row: tuple[object, ...],
    ) -> DocumentIngestionResultEvent:
        event = self._event_from_row(row)
        artifact = self._artifacts.get_for_share(event.artifact_id)
        if artifact is None or artifact != event.artifact:
            raise PersistenceConflictError(
                f"Ingestion event Artifact identity differs: {event.event_id}"
            )
        self._validate_artifact_scope(
            artifact,
            project_id=event.project_id,
            ingestion_batch_id=event.ingestion_batch_id,
            analysis_case_id=event.analysis_case_id,
            status=event.status,
        )
        self._validate_event_artifact_binding(
            artifact,
            event_id=event.event_id,
            search_index_build_id=event.search_index_build_id,
        )
        self._validate_document_profile_bindings(
            cursor,
            event_project_id=event.project_id,
            artifact=artifact,
        )
        self._validate_build_artifact_binding(
            cursor,
            event_project_id=event.project_id,
            artifact=artifact,
            search_index_build_id=event.search_index_build_id,
        )
        return event

    @staticmethod
    def _load_event_identity(
        cursor: Cursor[Any], event_id: str
    ) -> tuple[str, str, str, str | None, str, str | None, str] | None:
        cursor.execute(
            """
            SELECT project_id,
                   ingestion_batch_id,
                   analysis_case_id,
                   previous_event_id,
                   artifact_id,
                   search_index_build_id,
                   status
            FROM document_ingestion_result_events
            WHERE ingestion_result_event_id = %s
            """,
            (event_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3]) if row[3] is not None else None,
            str(row[4]),
            str(row[5]) if row[5] is not None else None,
            str(row[6]),
        )

    @staticmethod
    def _event_from_row(row: tuple[object, ...]) -> DocumentIngestionResultEvent:
        return DocumentIngestionResultEvent(
            event_id=str(row[0]),
            project_id=str(row[1]),
            ingestion_batch_id=str(row[2]),
            analysis_case_id=str(row[3]),
            previous_event_id=str(row[4]) if row[4] is not None else None,
            previous_status=(DocumentIngestionStatus(str(row[5])) if row[5] is not None else None),
            artifact_id=str(row[6]),
            search_index_build_id=str(row[7]) if row[7] is not None else None,
            status=DocumentIngestionStatus(str(row[8])),
            artifact=cast(dict[str, Any], row[9]),
            created_at=cast(datetime, row[10]),
        )


_EVENT_SELECT = """
    SELECT e.ingestion_result_event_id,
           e.project_id,
           e.ingestion_batch_id,
           e.analysis_case_id,
           e.previous_event_id,
           e.previous_status,
           e.artifact_id,
           e.search_index_build_id,
           e.status,
           a.payload,
           e.created_at
    FROM document_ingestion_result_events AS e
    JOIN artifact_records AS a ON a.artifact_id = e.artifact_id
"""


def initial_ingestion_event_id(*, project_id: str, ingestion_batch_id: str) -> str:
    """Return the deterministic ID used by the ingestion transaction's first event."""

    material = "\x00".join((project_id, ingestion_batch_id, "initial"))
    return f"ingestion-event-{sha256(material.encode()).hexdigest()[:24]}"
