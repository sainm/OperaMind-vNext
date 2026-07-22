"""Advance immutable ingestion evidence and Analysis Case RAG readiness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg import Connection

from operamind.contracts import ContractCatalog
from operamind.domain import ChangeReviewStatus
from operamind.infrastructure.postgres import (
    ArtifactRepository,
    DocumentIngestionResultEvent,
    DocumentIngestionResultRepository,
    DocumentIngestionStatus,
    ProfileRepository,
    SearchIndexBuildState,
    SearchIndexBuildStatus,
    SearchIndexRepository,
    StructuredChangeReviewRepository,
)
from operamind.profiles import ProfileCatalog


class RagReadinessBlockedError(ValueError):
    """Raised when evidence cannot prove formal RAG readiness."""


@dataclass(frozen=True, slots=True)
class RagReadinessRequest:
    """Optimistic event identity and exact Build/Profile scope."""

    event_id: str
    project_id: str
    ingestion_batch_id: str
    analysis_case_id: str
    expected_previous_event_id: str
    search_index_build_id: str
    embedding_profile_binding_key: str

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (
                self.event_id,
                self.project_id,
                self.ingestion_batch_id,
                self.analysis_case_id,
                self.expected_previous_event_id,
                self.search_index_build_id,
                self.embedding_profile_binding_key,
            )
        ):
            raise ValueError("RAG readiness request fields must not be blank")


@dataclass(frozen=True, slots=True)
class RagReadinessResult:
    """New effective ingestion Artifact and control-plane status."""

    created: bool
    event: DocumentIngestionResultEvent
    analysis_case_status: str


class RagReadinessService:
    """Require current/full index, active Profile, and reviewed changes."""

    def __init__(
        self,
        *,
        connection: Connection[Any],
        contracts: ContractCatalog,
        profiles: ProfileCatalog,
    ) -> None:
        self._connection = connection
        self._contracts = contracts
        self._profiles = profiles
        self._artifacts = ArtifactRepository(connection, contracts)
        self._ingestion_results = DocumentIngestionResultRepository(connection, contracts)
        self._profile_repository = ProfileRepository(connection, profiles)
        self._index_repository = SearchIndexRepository(connection)
        self._review_repository = StructuredChangeReviewRepository(connection)

    def run(self, request: RagReadinessRequest) -> RagReadinessResult:
        """Append new evidence and advance/invalidate the Analysis Case atomically."""

        existing = self._ingestion_results.get_event(request.event_id)
        if existing is not None:
            expected = (
                request.project_id,
                request.ingestion_batch_id,
                request.analysis_case_id,
                request.expected_previous_event_id,
                request.search_index_build_id,
                request.embedding_profile_binding_key,
            )
            actual = (
                existing.project_id,
                existing.ingestion_batch_id,
                existing.analysis_case_id,
                existing.previous_event_id,
                existing.search_index_build_id,
                existing.artifact.get("embedding_profile_binding_key"),
            )
            if actual != expected:
                raise RagReadinessBlockedError(
                    "RAG readiness event ID has different persisted content"
                )
            return RagReadinessResult(
                created=False,
                event=existing,
                analysis_case_status=self._get_case_status(request.analysis_case_id),
            )
        with self._connection.transaction():
            self._lock_case_scope(
                project_id=request.project_id,
                analysis_case_id=request.analysis_case_id,
            )
            return self._run_new(request)

    def _run_new(self, request: RagReadinessRequest) -> RagReadinessResult:
        """Validate and append a new event while the Analysis Case is locked."""

        latest = self._ingestion_results.get_latest(
            project_id=request.project_id,
            ingestion_batch_id=request.ingestion_batch_id,
        )
        if latest is None:
            raise RagReadinessBlockedError("Document ingestion result event does not exist")
        if latest.analysis_case_id != request.analysis_case_id:
            raise RagReadinessBlockedError("Analysis Case does not match the ingestion batch")
        if latest.event_id != request.expected_previous_event_id:
            raise RagReadinessBlockedError(
                "Stale RAG readiness request: expected previous event is not current"
            )
        self._lock_evidence_scope(request, latest)
        build = self._index_repository.get_build(request.search_index_build_id)
        if build is None:
            raise RagReadinessBlockedError("Search Index build does not exist")
        if (
            build.spec.project_id != request.project_id
            or build.spec.snapshot_id != latest.artifact["target_snapshot_id"]
        ):
            raise RagReadinessBlockedError("Search Index build is outside ingestion scope")
        if build.status is not SearchIndexBuildStatus.READY or not build.is_current:
            raise RagReadinessBlockedError("Search Index build is not current and ready")
        if build.indexed_target_count != build.eligible_target_count:
            raise RagReadinessBlockedError("Search Index coverage is incomplete")
        if build.eligible_target_count != latest.artifact["eligible_index_target_count"]:
            raise RagReadinessBlockedError("Search Index eligibility count drifted from ingestion")

        active = self._profile_repository.get_active(
            project_id=request.project_id,
            binding_key=request.embedding_profile_binding_key,
        )
        if active is None or active.profile_version_id != build.spec.profile_version_id:
            raise RagReadinessBlockedError("Search Index EmbeddingProfile is not active")
        self._profiles.validate_profile(active.profile)
        if active.profile.get("profile_type") != "EmbeddingProfile":
            raise RagReadinessBlockedError("Active index binding is not an EmbeddingProfile")
        expected_profile_build = (
            int(active.profile["expected_dimensions"]),
            str(active.profile["preprocessing_version"]),
            str(active.profile["ranking_policy_version"]),
        )
        actual_profile_build = (
            build.spec.dimensions,
            build.spec.preprocessing_version,
            build.spec.ranking_policy_version,
        )
        if actual_profile_build != expected_profile_build:
            raise RagReadinessBlockedError("Active EmbeddingProfile drifted from Search Index")

        artifact = self._build_artifact(request, latest, build, active.profile)
        self._contracts.validate_artifact(artifact)
        status = DocumentIngestionStatus(str(artifact["status"]))
        self._artifacts.store(
            artifact_id=request.event_id,
            project_id=request.project_id,
            analysis_case_id=request.analysis_case_id,
            artifact=artifact,
        )
        created = self._ingestion_results.append(
            event_id=request.event_id,
            project_id=request.project_id,
            ingestion_batch_id=request.ingestion_batch_id,
            analysis_case_id=request.analysis_case_id,
            expected_previous_event_id=request.expected_previous_event_id,
            artifact_id=request.event_id,
            search_index_build_id=request.search_index_build_id,
            status=status,
        )
        case_status = self._advance_case(
            request.analysis_case_id,
            readiness_status=status,
            created=created,
        )
        event = self._ingestion_results.get_latest(
            project_id=request.project_id,
            ingestion_batch_id=request.ingestion_batch_id,
        )
        if event is None or event.event_id != request.event_id:
            raise RagReadinessBlockedError("RAG readiness event was not persisted")
        return RagReadinessResult(
            created=created,
            event=event,
            analysis_case_status=case_status,
        )

    def _lock_case_scope(self, *, project_id: str, analysis_case_id: str) -> None:
        with self._connection.cursor() as cursor:
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
            raise RagReadinessBlockedError("Analysis Case does not exist")
        if str(row[0]) != project_id:
            raise RagReadinessBlockedError("Analysis Case does not belong to the RAG project")

    def _lock_evidence_scope(
        self,
        request: RagReadinessRequest,
        latest: DocumentIngestionResultEvent,
    ) -> None:
        """Freeze the Build, active binding, and reviews used by this decision."""

        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT search_index_build_id
                FROM search_index_builds
                WHERE search_index_build_id = %s
                FOR SHARE
                """,
                (request.search_index_build_id,),
            )
            cursor.fetchone()
            cursor.execute(
                """
                SELECT active_profile_version_id
                FROM project_profile_bindings
                WHERE project_id = %s AND binding_key = %s
                FOR SHARE
                """,
                (request.project_id, request.embedding_profile_binding_key),
            )
            cursor.fetchone()
            source_snapshot_id = str(latest.artifact.get("source_snapshot_id", ""))
            if source_snapshot_id:
                cursor.execute(
                    """
                    SELECT structured_change_id
                    FROM structured_changes
                    WHERE project_id = %s
                      AND source_snapshot_id = %s
                      AND target_snapshot_id = %s
                    ORDER BY structured_change_id
                    FOR SHARE
                    """,
                    (
                        request.project_id,
                        source_snapshot_id,
                        str(latest.artifact["target_snapshot_id"]),
                    ),
                )
                cursor.fetchall()

    def _build_artifact(
        self,
        request: RagReadinessRequest,
        latest: DocumentIngestionResultEvent,
        build: SearchIndexBuildState,
        embedding_profile: dict[str, Any],
    ) -> dict[str, Any]:
        artifact = dict(latest.artifact)
        source_snapshot_id = str(artifact.get("source_snapshot_id", ""))
        structured_change_count = int(artifact.get("structured_change_count", 0))
        review_states = (
            self._review_repository.list_states(
                project_id=latest.project_id,
                source_snapshot_id=source_snapshot_id,
                target_snapshot_id=str(artifact["target_snapshot_id"]),
            )
            if structured_change_count > 0 and source_snapshot_id
            else ()
        )
        if len(review_states) != structured_change_count:
            raise RagReadinessBlockedError(
                "StructuredChange count does not match ingestion review scope"
            )
        statuses = {state.status for state in review_states}
        if ChangeReviewStatus.REJECTED in statuses:
            status = DocumentIngestionStatus.BLOCKED
            blocking_reasons = ["structured_changes_rejected"]
        elif ChangeReviewStatus.NEEDS_REVIEW in statuses:
            status = DocumentIngestionStatus.NEEDS_REVIEW
            blocking_reasons = ["structured_changes_require_review"]
        else:
            status = DocumentIngestionStatus.READY_FOR_IMPACT
            blocking_reasons = []
        artifact.update(
            {
                "ingestion_result_event_id": request.event_id,
                "search_index_build_id": build.spec.build_id,
                "embedding_profile_version_id": build.spec.profile_version_id,
                "embedding_profile_binding_key": request.embedding_profile_binding_key,
                "embedding_profile_ref": (
                    f"{embedding_profile['profile_id']}@{embedding_profile['profile_version']}"
                ),
                "embedding_index_status": "ready",
                "indexed_target_count": build.indexed_target_count,
                "status": status.value,
                "blocking_reasons": blocking_reasons,
            }
        )
        return artifact

    def _advance_case(
        self,
        analysis_case_id: str,
        *,
        readiness_status: DocumentIngestionStatus,
        created: bool,
    ) -> str:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status
                FROM analysis_cases
                WHERE analysis_case_id = %s
                FOR UPDATE
                """,
                (analysis_case_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise RagReadinessBlockedError("Analysis Case disappeared")
            current = str(row[0])
            if not created:
                return current
            if readiness_status is DocumentIngestionStatus.READY_FOR_IMPACT:
                target = (
                    "ready_for_impact"
                    if current in {"ingesting", "indexing_rag", "ready_for_impact"}
                    else current
                )
            elif current in {"ingesting", "indexing_rag"}:
                target = "indexing_rag"
            elif current in {"failed", "reanalysis_required"}:
                target = current
            else:
                target = "reanalysis_required"
            if target != current:
                cursor.execute(
                    """
                    UPDATE analysis_cases
                    SET status = %s, updated_at = now()
                    WHERE analysis_case_id = %s
                    """,
                    (target, analysis_case_id),
                )
            return target

    def _get_case_status(self, analysis_case_id: str) -> str:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT status FROM analysis_cases WHERE analysis_case_id = %s",
                (analysis_case_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise RagReadinessBlockedError("Analysis Case does not exist")
        return str(row[0])
