"""PostgreSQL/pgvector persistence for exact-scope document search indexes."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any, cast

from psycopg import Connection, Cursor

from operamind.domain import DocumentEmbeddingInput, DocumentNode, DocumentNodeType
from operamind.infrastructure.postgres.errors import PersistenceConflictError
from operamind.infrastructure.postgres.profile_repository import (
    validate_profile_payload_identity,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")

type SearchIndexEntryLedgerRow = tuple[
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    int,
    str,
    str,
    str,
    str,
    int,
    str,
    str,
]


class SearchIndexBuildStatus(StrEnum):
    """Derived index lifecycle states."""

    BUILDING = "building"
    READY = "ready"
    FAILED = "failed"
    STALE = "stale"


class SearchIndexFailureKind(StrEnum):
    """Audited terminal failure classifications for a started build."""

    EMBEDDING_GENERATION = "embedding_generation"
    PUBLISH_VALIDATION = "publish_validation"
    PUBLISH_EXECUTION = "publish_execution"
    STALE_RECOVERY = "stale_recovery"
    LEGACY_UNVERSIONED = "legacy_unversioned"


@dataclass(frozen=True, slots=True)
class SearchIndexTarget:
    """Canonical node plus deterministic input context loaded from the DB."""

    node: DocumentNode
    document_type: str
    relation_labels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SearchIndexBuildSpec:
    """Immutable build identity bound to Snapshot and runtime model."""

    build_id: str
    project_id: str
    snapshot_id: str
    profile_version_id: str
    model: str
    dimensions: int
    preprocessing_version: str
    ranking_policy_version: str
    relation_build_id: str | None = None

    def __post_init__(self) -> None:
        required = (
            self.build_id,
            self.project_id,
            self.snapshot_id,
            self.profile_version_id,
            self.model,
            self.preprocessing_version,
            self.ranking_policy_version,
        )
        if any(not value.strip() for value in required):
            raise ValueError("Search Index build identity fields must not be blank")
        if self.relation_build_id is not None and not self.relation_build_id.strip():
            raise ValueError("relation_build_id must not be blank when supplied")
        if not 1 <= self.dimensions <= 16_000:
            raise ValueError("Search Index dimensions must be between 1 and 16000")


@dataclass(frozen=True, slots=True)
class SearchIndexBuildState:
    """Persisted Search Index build state."""

    spec: SearchIndexBuildSpec
    status: SearchIndexBuildStatus
    eligible_target_count: int
    indexed_target_count: int
    reused_vector_count: int
    entry_ledger_digest: str | None
    is_current: bool
    failure_event_id: str | None
    failure_kind: SearchIndexFailureKind | None
    failure_actor: str | None
    failure_reason: str | None
    failure_stale_before: datetime | None
    started_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class SearchIndexBuildStartResult:
    """Whether this caller created the building record plus its persisted state."""

    created: bool
    state: SearchIndexBuildState


@dataclass(frozen=True, slots=True)
class SearchIndexEntryWrite:
    """One target-to-cache mapping ready for finalization."""

    embedding_input: DocumentEmbeddingInput
    vector_cache_id: str


@dataclass(frozen=True, slots=True)
class RankedSearchHit:
    """One channel-specific target rank without Canonical content."""

    target_node_id: str
    rank: int
    score: float


class SearchIndexRepository:
    """Manage index build lifecycle, reusable vectors, and scoped entries."""

    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def load_targets(
        self,
        *,
        project_id: str,
        snapshot_id: str,
    ) -> tuple[SearchIndexTarget, ...]:
        """Load eligible nodes and Convention document type for one exact scope."""

        if not project_id.strip() or not snapshot_id.strip():
            raise ValueError("Search target scope fields must not be blank")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT n.document_node_id,
                       n.document_snapshot_id,
                       n.document_version_id,
                       n.parent_node_id,
                       n.node_type,
                       n.ordinal,
                       n.heading_path,
                       n.business_keys,
                       n.summary,
                       n.content,
                       n.source_refs,
                       n.index_eligible,
                       n.content_digest,
                       m.profile_version_id,
                       p.profile_type,
                       p.profile_id,
                       p.semantic_version,
                       p.payload,
                       p.payload_digest,
                       COALESCE(
                           (
                               SELECT array_agg(DISTINCT r.relation_label ORDER BY r.relation_label)
                               FROM document_relation_builds AS rb
                               JOIN document_relation_entries AS re
                                 ON re.document_relation_build_id =
                                    rb.document_relation_build_id
                                AND re.project_id = rb.project_id
                                AND re.document_snapshot_id = rb.document_snapshot_id
                               JOIN document_relations AS r
                                 ON r.document_relation_id = re.document_relation_id
                                AND r.project_id = re.project_id
                                AND r.document_snapshot_id = re.document_snapshot_id
                               WHERE rb.project_id = n.project_id
                                 AND rb.document_snapshot_id = n.document_snapshot_id
                                 AND rb.status = 'ready'
                                 AND rb.is_current
                                 AND (
                                     r.source_node_id = n.document_node_id
                                     OR r.target_node_id = n.document_node_id
                                 )
                           ),
                           ARRAY[]::text[]
                       ) AS relation_labels
                FROM document_nodes AS n
                JOIN snapshot_memberships AS m
                  ON m.project_id = n.project_id
                 AND m.document_snapshot_id = n.document_snapshot_id
                 AND m.document_version_id = n.document_version_id
                JOIN profile_versions AS p
                  ON p.profile_version_id = m.profile_version_id
                 AND p.profile_type = 'DocumentConventionProfile'
                WHERE n.project_id = %s
                  AND n.document_snapshot_id = %s
                  AND n.index_eligible
                ORDER BY n.document_node_id
                """,
                (project_id, snapshot_id),
            )
            rows = cursor.fetchall()
        return tuple(_target_from_row(row) for row in rows)

    def get_build(self, build_id: str) -> SearchIndexBuildState | None:
        """Load one build by immutable ID."""

        if not build_id.strip():
            raise ValueError("build_id must not be blank")
        with self._connection.cursor() as cursor:
            state = self._load_build(cursor, build_id)
            if state is not None:
                self._validate_build_integrity(cursor, state)
        return state

    def get_current_build(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        profile_version_id: str,
    ) -> SearchIndexBuildState | None:
        """Load the single ready/current build in the exact formal query scope."""

        required = (project_id, snapshot_id, profile_version_id)
        if any(not value.strip() for value in required):
            raise ValueError("Current Search Index scope fields must not be blank")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT search_index_build_id
                FROM search_index_builds
                WHERE project_id = %s
                  AND document_snapshot_id = %s
                  AND embedding_profile_version_id = %s
                  AND status = 'ready'
                  AND is_current
                """,
                (project_id, snapshot_id, profile_version_id),
            )
            row = cursor.fetchone()
            state = self._load_build(cursor, str(row[0])) if row is not None else None
            if state is not None:
                self._validate_build_integrity(cursor, state)
        return state

    def find_current_builds_containing_targets(
        self,
        *,
        project_id: str,
        profile_version_id: str,
        target_node_ids: tuple[str, ...],
    ) -> tuple[SearchIndexBuildState, ...]:
        """Find exact ready/current builds containing every frozen Golden target."""

        if not project_id.strip() or not profile_version_id.strip():
            raise ValueError("Golden Search Index discovery scope must not be blank")
        if not target_node_ids or any(not value.strip() for value in target_node_ids):
            raise ValueError("Golden Search Index discovery targets must not be blank")
        if len(target_node_ids) != len(set(target_node_ids)):
            raise ValueError("Golden Search Index discovery targets must be unique")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT b.search_index_build_id
                FROM search_index_builds AS b
                WHERE b.project_id = %s
                  AND b.embedding_profile_version_id = %s
                  AND b.status = 'ready'
                  AND b.is_current
                  AND (
                      SELECT count(DISTINCT e.target_node_id)
                      FROM search_index_entries AS e
                      WHERE e.search_index_build_id = b.search_index_build_id
                        AND e.project_id = b.project_id
                        AND e.document_snapshot_id = b.document_snapshot_id
                        AND e.embedding_profile_version_id =
                            b.embedding_profile_version_id
                        AND e.target_node_id = ANY(%s)
                  ) = %s
                ORDER BY b.started_at DESC, b.search_index_build_id
                """,
                (
                    project_id,
                    profile_version_id,
                    list(target_node_ids),
                    len(target_node_ids),
                ),
            )
            build_ids = tuple(str(row[0]) for row in cursor.fetchall())
            states = tuple(self._load_build(cursor, build_id) for build_id in build_ids)
            for state in states:
                if state is None:
                    raise RuntimeError("Discovered Golden Search Index disappeared")
                self._validate_build_integrity(cursor, state)
        return tuple(state for state in states if state is not None)

    def find_current_builds(
        self,
        *,
        project_id: str,
        profile_version_id: str,
    ) -> tuple[SearchIndexBuildState, ...]:
        """Return integrity-checked ready/current builds for semantic target discovery."""

        if not project_id.strip() or not profile_version_id.strip():
            raise ValueError("Golden Search Index discovery scope must not be blank")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT search_index_build_id
                FROM search_index_builds
                WHERE project_id = %s
                  AND embedding_profile_version_id = %s
                  AND status = 'ready'
                  AND is_current
                ORDER BY started_at DESC, search_index_build_id
                """,
                (project_id, profile_version_id),
            )
            build_ids = tuple(str(row[0]) for row in cursor.fetchall())
            states = tuple(self._load_build(cursor, build_id) for build_id in build_ids)
            for state in states:
                if state is None:
                    raise RuntimeError("Discovered Golden Search Index disappeared")
                self._validate_build_integrity(cursor, state)
        return tuple(state for state in states if state is not None)

    def vector_search(
        self,
        *,
        state: SearchIndexBuildState,
        query_vector: tuple[float, ...],
        top_k: int,
    ) -> tuple[RankedSearchHit, ...]:
        """Run exact pgvector cosine search inside one ready build."""

        self._validate_query_state(state, top_k=top_k)
        if (
            len(query_vector) != state.spec.dimensions
            or any(not math.isfinite(value) for value in query_vector)
            or math.sqrt(sum(value * value for value in query_vector)) == 0.0
        ):
            raise ValueError("Query vector is invalid for the Search Index build")
        vector_text = _vector_text(query_vector)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            self._lock_and_validate_query_build(cursor, state)
            cursor.execute(
                """
                SELECT ranked.target_node_id,
                       ranked.distance
                FROM (
                    SELECT e.target_node_id,
                           v.embedding <=> %s::public.vector AS distance
                    FROM search_index_entries AS e
                    JOIN document_search_vectors AS v
                      ON v.vector_cache_id = e.vector_cache_id
                    WHERE e.search_index_build_id = %s
                      AND e.project_id = %s
                      AND e.document_snapshot_id = %s
                      AND e.embedding_profile_version_id = %s
                    ORDER BY distance, e.target_node_id
                    LIMIT %s
                ) AS ranked
                ORDER BY ranked.distance, ranked.target_node_id
                """,
                (
                    vector_text,
                    state.spec.build_id,
                    state.spec.project_id,
                    state.spec.snapshot_id,
                    state.spec.profile_version_id,
                    top_k,
                ),
            )
            rows = cursor.fetchall()
        return tuple(
            RankedSearchHit(
                target_node_id=str(row[0]),
                rank=index,
                score=max(0.0, min(1.0, 1.0 - float(cast(float, row[1])))),
            )
            for index, row in enumerate(rows, start=1)
        )

    def keyword_search(
        self,
        *,
        state: SearchIndexBuildState,
        query_text: str,
        top_k: int,
    ) -> tuple[RankedSearchHit, ...]:
        """Run scoped PostgreSQL full-text ranking without returning content."""

        self._validate_query_state(state, top_k=top_k)
        if not query_text.strip():
            raise ValueError("Keyword query text must not be blank")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            self._lock_and_validate_query_build(cursor, state)
            cursor.execute(
                """
                WITH query AS (
                    SELECT websearch_to_tsquery('simple', %s) AS value
                )
                SELECT e.target_node_id,
                       ts_rank_cd(e.keyword_tsv, query.value) AS score
                FROM search_index_entries AS e
                CROSS JOIN query
                WHERE e.search_index_build_id = %s
                  AND e.project_id = %s
                  AND e.document_snapshot_id = %s
                  AND e.embedding_profile_version_id = %s
                  AND e.keyword_tsv @@ query.value
                ORDER BY score DESC, e.target_node_id
                LIMIT %s
                """,
                (
                    query_text,
                    state.spec.build_id,
                    state.spec.project_id,
                    state.spec.snapshot_id,
                    state.spec.profile_version_id,
                    top_k,
                ),
            )
            rows = cursor.fetchall()
        return tuple(
            RankedSearchHit(
                target_node_id=str(row[0]),
                rank=index,
                score=max(0.0, float(cast(float, row[1]))),
            )
            for index, row in enumerate(rows, start=1)
        )

    def resolve_target_projects(self, target_node_ids: tuple[str, ...]) -> dict[str, str]:
        """Resolve actual Canonical Project ownership for retrieved target IDs."""

        if not target_node_ids:
            return {}
        if any(not value.strip() for value in target_node_ids):
            raise ValueError("Search target IDs must not be blank")
        if len(target_node_ids) != len(set(target_node_ids)):
            raise ValueError("Search target IDs must be unique")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT document_node_id, project_id
                FROM document_nodes
                WHERE document_node_id = ANY(%s)
                ORDER BY document_node_id
                """,
                (list(target_node_ids),),
            )
            rows = cursor.fetchall()
        resolved = {str(row[0]): str(row[1]) for row in rows}
        if set(resolved) != set(target_node_ids):
            raise ValueError("Retrieved Search target cannot resolve Canonical Project ownership")
        return resolved

    def start_build(
        self,
        *,
        spec: SearchIndexBuildSpec,
        eligible_target_count: int,
    ) -> SearchIndexBuildStartResult:
        """Idempotently create a building record for a committed Snapshot."""

        if eligible_target_count <= 0:
            raise ValueError("Search Index requires at least one eligible target")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status
                FROM document_snapshots
                WHERE project_id = %s AND document_snapshot_id = %s
                FOR SHARE
                """,
                (spec.project_id, spec.snapshot_id),
            )
            snapshot = cursor.fetchone()
            if snapshot is None:
                raise ValueError("Search Index Snapshot does not exist in the project")
            if str(snapshot[0]) != "committed":
                raise ValueError("Search Index requires a committed Snapshot")
            cursor.execute(
                """
                SELECT profile_type
                FROM profile_versions
                WHERE profile_version_id = %s
                """,
                (spec.profile_version_id,),
            )
            profile = cursor.fetchone()
            if profile is None or str(profile[0]) != "EmbeddingProfile":
                raise ValueError("Search Index requires a persisted EmbeddingProfile")
            cursor.execute(
                """
                SELECT document_relation_build_id
                FROM document_relation_builds
                WHERE project_id = %s
                  AND document_snapshot_id = %s
                  AND status = 'ready'
                  AND is_current
                FOR SHARE
                """,
                (spec.project_id, spec.snapshot_id),
            )
            relation_build = cursor.fetchone()
            current_relation_build_id = (
                str(relation_build[0]) if relation_build is not None else None
            )
            if current_relation_build_id != spec.relation_build_id:
                raise ValueError("Current Document Relation Build changed before indexing")
            cursor.execute(
                """
                INSERT INTO search_index_builds (
                    search_index_build_id,
                    project_id,
                    document_snapshot_id,
                    embedding_profile_version_id,
                    embedding_model,
                    dimensions,
                    preprocessing_version,
                    ranking_policy_version,
                    document_relation_build_id,
                    status,
                    eligible_target_count
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'building', %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    spec.build_id,
                    spec.project_id,
                    spec.snapshot_id,
                    spec.profile_version_id,
                    spec.model,
                    spec.dimensions,
                    spec.preprocessing_version,
                    spec.ranking_policy_version,
                    spec.relation_build_id,
                    eligible_target_count,
                ),
            )
            created = cursor.rowcount == 1
            state = self._load_build(cursor, spec.build_id)
            if (
                state is None
                or state.spec != spec
                or state.eligible_target_count != (eligible_target_count)
            ):
                raise PersistenceConflictError(
                    f"Search Index build identity has different content: {spec.build_id}"
                )
            self._validate_build_integrity(cursor, state)
        return SearchIndexBuildStartResult(created=created, state=state)

    def find_cached_vectors(
        self,
        *,
        input_digests: tuple[str, ...],
        model: str,
        dimensions: int,
        preprocessing_version: str,
    ) -> dict[str, str]:
        """Map semantic input digests to reusable vector cache IDs."""

        if not input_digests:
            return {}
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT input_digest, vector_cache_id
                FROM document_search_vectors
                WHERE input_digest = ANY(%s)
                  AND embedding_model = %s
                  AND dimensions = %s
                  AND preprocessing_version = %s
                """,
                (list(input_digests), model, dimensions, preprocessing_version),
            )
            rows = cursor.fetchall()
        cached: dict[str, str] = {}
        for row in rows:
            input_digest = str(row[0])
            cache_id = str(row[1])
            expected_cache_id = vector_cache_id(
                input_digest=input_digest,
                model=model,
                dimensions=dimensions,
                preprocessing_version=preprocessing_version,
            )
            if cache_id != expected_cache_id:
                raise PersistenceConflictError(
                    f"Vector cache semantic identity differs: {cache_id}"
                )
            cached[input_digest] = cache_id
        return cached

    def store_vector(
        self,
        *,
        vector_cache_id: str,
        input_digest: str,
        model: str,
        dimensions: int,
        preprocessing_version: str,
        vector: tuple[float, ...],
    ) -> None:
        """Idempotently store one finite pgvector cache value."""

        required = (vector_cache_id, input_digest, model, preprocessing_version)
        if any(not value.strip() for value in required):
            raise ValueError("Vector cache identity fields must not be blank")
        if not _SHA256.fullmatch(input_digest):
            raise ValueError("Vector cache input_digest must be a lowercase SHA-256")
        expected_cache_id = _vector_cache_id(
            input_digest=input_digest,
            model=model,
            dimensions=dimensions,
            preprocessing_version=preprocessing_version,
        )
        if vector_cache_id != expected_cache_id:
            raise ValueError("Vector cache ID does not match its semantic identity")
        if (
            len(vector) != dimensions
            or any(not math.isfinite(value) for value in vector)
            or math.sqrt(sum(value * value for value in vector)) == 0.0
        ):
            raise ValueError("Vector does not match dimensions or contains non-finite values")
        vector_text = _vector_text(vector)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO document_search_vectors (
                    vector_cache_id,
                    input_digest,
                    embedding_model,
                    dimensions,
                    preprocessing_version,
                    embedding
                ) VALUES (%s, %s, %s, %s, %s, %s::public.vector)
                ON CONFLICT DO NOTHING
                """,
                (
                    vector_cache_id,
                    input_digest,
                    model,
                    dimensions,
                    preprocessing_version,
                    vector_text,
                ),
            )
            cursor.execute(
                """
                SELECT input_digest,
                       embedding_model,
                       dimensions,
                       preprocessing_version,
                       embedding = %s::public.vector AS same_embedding
                FROM document_search_vectors
                WHERE vector_cache_id = %s
                """,
                (vector_text, vector_cache_id),
            )
            row = cursor.fetchone()
            expected = (input_digest, model, dimensions, preprocessing_version, True)
            if row is None or tuple(row) != expected:
                raise PersistenceConflictError(
                    f"Vector cache identity has different content: {vector_cache_id}"
                )

    def finalize_build(
        self,
        *,
        spec: SearchIndexBuildSpec,
        entries: tuple[SearchIndexEntryWrite, ...],
        reused_vector_count: int,
    ) -> SearchIndexBuildState:
        """Atomically store complete coverage and make this the current ready build."""

        if not 0 <= reused_vector_count <= len(entries):
            raise ValueError("reused_vector_count is outside the entry count")
        if len({entry.embedding_input.target_node_id for entry in entries}) != len(entries):
            raise ValueError("Search Index entries contain duplicate target nodes")
        for entry in entries:
            expected_cache_id = _vector_cache_id(
                input_digest=entry.embedding_input.input_digest,
                model=spec.model,
                dimensions=spec.dimensions,
                preprocessing_version=spec.preprocessing_version,
            )
            if entry.vector_cache_id != expected_cache_id:
                raise ValueError("Search Index entry vector cache identity differs")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status
                FROM document_snapshots
                WHERE project_id = %s AND document_snapshot_id = %s
                FOR SHARE
                """,
                (spec.project_id, spec.snapshot_id),
            )
            if cursor.fetchone() is None:
                raise ValueError("Search Index Snapshot disappeared")
            cursor.execute(
                """
                SELECT status, eligible_target_count
                FROM search_index_builds
                WHERE search_index_build_id = %s
                FOR UPDATE
                """,
                (spec.build_id,),
            )
            build = cursor.fetchone()
            if build is None:
                raise ValueError(f"Search Index build does not exist: {spec.build_id}")
            status = SearchIndexBuildStatus(str(build[0]))
            eligible_count = int(cast(int, build[1]))
            if len(entries) != eligible_count:
                raise ValueError("Search Index coverage is incomplete")
            if status not in {SearchIndexBuildStatus.BUILDING, SearchIndexBuildStatus.READY}:
                raise ValueError(f"Search Index build cannot be finalized from {status.value}")
            if status is SearchIndexBuildStatus.READY:
                ready_state = self._load_build(cursor, spec.build_id)
                if ready_state is None:
                    raise AssertionError("Locked Search Index build disappeared")
                self._validate_build_integrity(cursor, ready_state)
            for entry in entries:
                self._store_entry(cursor, spec=spec, entry=entry)
            stored_rows = self._load_entry_rows(cursor, spec.build_id)
            stored_count = len(stored_rows)
            if stored_count != eligible_count:
                raise ValueError("Persisted Search Index coverage is incomplete")
            self._validate_entry_rows(
                spec=spec,
                rows=stored_rows,
                expected_count=eligible_count,
            )
            ledger_digest = _search_index_entry_ledger_digest(stored_rows)
            cursor.execute(
                """
                UPDATE search_index_builds
                SET status = 'stale',
                    is_current = false,
                    completed_at = COALESCE(completed_at, now())
                WHERE project_id = %s
                  AND document_snapshot_id = %s
                  AND is_current
                  AND search_index_build_id <> %s
                """,
                (spec.project_id, spec.snapshot_id, spec.build_id),
            )
            cursor.execute(
                """
                UPDATE search_index_builds
                SET status = 'ready',
                    indexed_target_count = %s,
                    reused_vector_count = %s,
                    entry_ledger_digest = %s,
                    is_current = true,
                    failure_reason = NULL,
                    completed_at = COALESCE(completed_at, now())
                WHERE search_index_build_id = %s
                """,
                (stored_count, reused_vector_count, ledger_digest, spec.build_id),
            )
            state = self._load_build(cursor, spec.build_id)
            if state is None or state.spec != spec or not state.is_current:
                raise PersistenceConflictError(
                    f"Search Index build finalization conflict: {spec.build_id}"
                )
            self._validate_build_integrity(cursor, state)
        return state

    def fail_build(
        self,
        *,
        failure_event_id: str,
        build_id: str,
        kind: SearchIndexFailureKind,
        actor: str,
        reason: str,
    ) -> SearchIndexBuildState:
        """Fail a building record or replay the exact immutable failure event."""

        return self._transition_to_failed(
            failure_event_id=failure_event_id,
            build_id=build_id,
            kind=kind,
            actor=actor,
            reason=reason,
            stale_before=None,
        )

    def recover_stale_build(
        self,
        *,
        recovery_id: str,
        build_id: str,
        actor: str,
        reason: str,
        stale_before: datetime,
    ) -> SearchIndexBuildState:
        """Explicitly fail a building record older than a fixed recovery boundary."""

        if stale_before.utcoffset() is None:
            raise ValueError("Search Index recovery stale_before must include a timezone")
        return self._transition_to_failed(
            failure_event_id=recovery_id,
            build_id=build_id,
            kind=SearchIndexFailureKind.STALE_RECOVERY,
            actor=actor,
            reason=reason,
            stale_before=stale_before.astimezone(UTC),
        )

    def _transition_to_failed(
        self,
        *,
        failure_event_id: str,
        build_id: str,
        kind: SearchIndexFailureKind,
        actor: str,
        reason: str,
        stale_before: datetime | None,
    ) -> SearchIndexBuildState:
        required = (failure_event_id, build_id, actor, reason)
        if any(not value.strip() for value in required):
            raise ValueError("Search Index failure audit fields must not be blank")
        if (kind is SearchIndexFailureKind.STALE_RECOVERY) != (stale_before is not None):
            raise ValueError("Only a stale recovery may include stale_before")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status, started_at, failure_event_id, failure_kind,
                       failure_actor, failure_reason, failure_stale_before
                FROM search_index_builds
                WHERE search_index_build_id = %s
                FOR UPDATE
                """,
                (build_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError(f"Search Index build does not exist: {build_id}")
            status = SearchIndexBuildStatus(str(row[0]))
            expected_failure = (
                failure_event_id,
                kind.value,
                actor,
                reason,
                stale_before,
            )
            if status is SearchIndexBuildStatus.FAILED:
                existing_failure = (
                    str(row[2]) if row[2] is not None else None,
                    str(row[3]) if row[3] is not None else None,
                    str(row[4]) if row[4] is not None else None,
                    str(row[5]) if row[5] is not None else None,
                    cast(datetime | None, row[6]),
                )
                if existing_failure != expected_failure:
                    raise PersistenceConflictError(
                        f"Search Index build has a different failure event: {build_id}"
                    )
                state = self._load_build(cursor, build_id)
                if state is None:
                    raise AssertionError("Locked Search Index build disappeared")
                self._validate_build_integrity(cursor, state)
                return state
            if status is not SearchIndexBuildStatus.BUILDING:
                raise ValueError(f"Search Index build cannot fail from {status.value}")
            if stale_before is not None:
                started_at = cast(datetime, row[1])
                cursor.execute("SELECT %s <= clock_timestamp()", (stale_before,))
                boundary = cursor.fetchone()
                if started_at > stale_before or boundary is None or not bool(boundary[0]):
                    raise ValueError("Search Index build is newer than the recovery boundary")
            cursor.execute(
                """
                UPDATE search_index_builds
                SET status = 'failed',
                    is_current = false,
                    failure_event_id = %s,
                    failure_kind = %s,
                    failure_actor = %s,
                    failure_reason = %s,
                    failure_stale_before = %s,
                    completed_at = clock_timestamp()
                WHERE search_index_build_id = %s AND status = 'building'
                """,
                (
                    failure_event_id,
                    kind.value,
                    actor,
                    reason,
                    stale_before,
                    build_id,
                ),
            )
            state = self._load_build(cursor, build_id)
            if (
                state is None
                or state.status is not SearchIndexBuildStatus.FAILED
                or state.failure_event_id != failure_event_id
                or state.failure_kind is not kind
                or state.failure_actor != actor
                or state.failure_reason != reason
                or state.failure_stale_before != stale_before
            ):
                raise PersistenceConflictError(
                    f"Search Index failure transition conflicted: {build_id}"
                )
            self._validate_build_integrity(cursor, state)
        return state

    @staticmethod
    def _store_entry(
        cursor: Cursor[Any],
        *,
        spec: SearchIndexBuildSpec,
        entry: SearchIndexEntryWrite,
    ) -> None:
        embedding_input = entry.embedding_input
        cursor.execute(
            """
            INSERT INTO search_index_entries (
                search_index_build_id,
                project_id,
                document_snapshot_id,
                embedding_profile_version_id,
                target_node_id,
                input_digest,
                vector_cache_id,
                embedding_model,
                dimensions,
                preprocessing_version,
                keyword_text
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                spec.build_id,
                spec.project_id,
                spec.snapshot_id,
                spec.profile_version_id,
                embedding_input.target_node_id,
                embedding_input.input_digest,
                entry.vector_cache_id,
                spec.model,
                spec.dimensions,
                spec.preprocessing_version,
                embedding_input.keyword_text,
            ),
        )
        cursor.execute(
            """
            SELECT project_id,
                   document_snapshot_id,
                   embedding_profile_version_id,
                   input_digest,
                   vector_cache_id,
                   embedding_model,
                   dimensions,
                   preprocessing_version,
                   keyword_text
            FROM search_index_entries
            WHERE search_index_build_id = %s AND target_node_id = %s
            """,
            (spec.build_id, embedding_input.target_node_id),
        )
        row = cursor.fetchone()
        expected = (
            spec.project_id,
            spec.snapshot_id,
            spec.profile_version_id,
            embedding_input.input_digest,
            entry.vector_cache_id,
            spec.model,
            spec.dimensions,
            spec.preprocessing_version,
            embedding_input.keyword_text,
        )
        if row is None or tuple(row) != expected:
            raise PersistenceConflictError(
                "Search Index entry identity has different content: "
                f"{spec.build_id}/{embedding_input.target_node_id}"
            )

    @staticmethod
    def _validate_query_state(state: SearchIndexBuildState, *, top_k: int) -> None:
        if state.status is not SearchIndexBuildStatus.READY or not state.is_current:
            raise ValueError("Formal retrieval requires a current ready Search Index")
        if not 1 <= top_k <= 1_000:
            raise ValueError("Search top_k must be between 1 and 1000")

    @staticmethod
    def _lock_and_validate_query_build(
        cursor: Cursor[Any],
        expected_state: SearchIndexBuildState,
    ) -> None:
        cursor.execute(
            """
            SELECT search_index_build_id
            FROM search_index_builds
            WHERE search_index_build_id = %s
            FOR SHARE
            """,
            (expected_state.spec.build_id,),
        )
        if cursor.fetchone() is None:
            raise PersistenceConflictError(
                f"Search Index build disappeared: {expected_state.spec.build_id}"
            )
        actual_state = SearchIndexRepository._load_build(
            cursor,
            expected_state.spec.build_id,
        )
        if actual_state != expected_state:
            raise PersistenceConflictError(
                f"Search Index query state drifted: {expected_state.spec.build_id}"
            )
        SearchIndexRepository._validate_build_integrity(
            cursor,
            actual_state,
            lock_entries=True,
        )

    @staticmethod
    def _validate_build_integrity(
        cursor: Cursor[Any],
        state: SearchIndexBuildState,
        *,
        lock_entries: bool = False,
    ) -> None:
        rows = SearchIndexRepository._load_entry_rows(
            cursor,
            state.spec.build_id,
            for_share=lock_entries,
        )
        if state.status in {SearchIndexBuildStatus.BUILDING, SearchIndexBuildStatus.FAILED}:
            if rows or state.indexed_target_count != 0 or state.entry_ledger_digest is not None:
                raise PersistenceConflictError(
                    f"Unpublished Search Index build has an entry ledger: {state.spec.build_id}"
                )
            return
        SearchIndexRepository._validate_entry_rows(
            spec=state.spec,
            rows=rows,
            expected_count=state.indexed_target_count,
        )
        if state.indexed_target_count != state.eligible_target_count:
            raise PersistenceConflictError(
                f"Search Index build coverage count differs: {state.spec.build_id}"
            )
        if state.entry_ledger_digest is None or not _SHA256.fullmatch(state.entry_ledger_digest):
            raise PersistenceConflictError(
                "Search Index build requires a versioned entry ledger digest: "
                f"{state.spec.build_id}"
            )
        actual_digest = _search_index_entry_ledger_digest(rows)
        if actual_digest != state.entry_ledger_digest:
            raise PersistenceConflictError(
                f"Search Index build entry ledger digest differs: {state.spec.build_id}"
            )

    @staticmethod
    def _validate_entry_rows(
        *,
        spec: SearchIndexBuildSpec,
        rows: tuple[SearchIndexEntryLedgerRow, ...],
        expected_count: int,
    ) -> None:
        if len(rows) != expected_count:
            raise PersistenceConflictError(
                f"Search Index build ledger count differs: {spec.build_id}"
            )
        if len({row[3] for row in rows}) != len(rows):
            raise PersistenceConflictError(
                f"Search Index build contains duplicate targets: {spec.build_id}"
            )
        expected_scope = (
            spec.project_id,
            spec.snapshot_id,
            spec.profile_version_id,
            spec.model,
            spec.dimensions,
            spec.preprocessing_version,
        )
        for row in rows:
            entry_scope = (row[0], row[1], row[2], row[6], row[7], row[8])
            if entry_scope != expected_scope:
                raise PersistenceConflictError(
                    f"Search Index entry scope differs: {spec.build_id}/{row[3]}"
                )
            if not _SHA256.fullmatch(row[4]) or not _SHA256.fullmatch(row[14]):
                raise PersistenceConflictError(
                    f"Search Index entry digest is invalid: {spec.build_id}/{row[3]}"
                )
            vector_identity = (row[10], row[11], row[12], row[13])
            if vector_identity != (row[4], row[6], row[7], row[8]):
                raise PersistenceConflictError(f"Search Index vector metadata differs: {row[5]}")
            expected_cache_id = _vector_cache_id(
                input_digest=row[4],
                model=row[6],
                dimensions=row[7],
                preprocessing_version=row[8],
            )
            if row[5] != expected_cache_id:
                raise PersistenceConflictError(
                    f"Search Index vector semantic identity differs: {row[5]}"
                )

    @staticmethod
    def _load_entry_rows(
        cursor: Cursor[Any],
        build_id: str,
        *,
        for_share: bool = False,
    ) -> tuple[SearchIndexEntryLedgerRow, ...]:
        lock_clause = " FOR SHARE OF entry, cache" if for_share else ""
        cursor.execute(
            """
            SELECT entry.project_id,
                   entry.document_snapshot_id,
                   entry.embedding_profile_version_id,
                   entry.target_node_id,
                   entry.input_digest,
                   entry.vector_cache_id,
                   entry.embedding_model,
                   entry.dimensions,
                   entry.preprocessing_version,
                   entry.keyword_text,
                   cache.input_digest,
                   cache.embedding_model,
                   cache.dimensions,
                   cache.preprocessing_version,
                   cache.embedding_digest
            FROM search_index_entries AS entry
            JOIN document_search_vectors AS cache
              ON cache.vector_cache_id = entry.vector_cache_id
            WHERE entry.search_index_build_id = %s
            ORDER BY entry.target_node_id
            """
            + lock_clause,
            (build_id,),
        )
        return tuple(
            (
                str(row[0]),
                str(row[1]),
                str(row[2]),
                str(row[3]),
                str(row[4]),
                str(row[5]),
                str(row[6]),
                int(cast(int, row[7])),
                str(row[8]),
                str(row[9]),
                str(row[10]),
                str(row[11]),
                int(cast(int, row[12])),
                str(row[13]),
                str(row[14]),
            )
            for row in cursor.fetchall()
        )

    @staticmethod
    def _load_build(cursor: Cursor[Any], build_id: str) -> SearchIndexBuildState | None:
        cursor.execute(
            """
            SELECT project_id,
                   document_snapshot_id,
                   embedding_profile_version_id,
                   embedding_model,
                   dimensions,
                   preprocessing_version,
                   ranking_policy_version,
                   document_relation_build_id,
                   status,
                   eligible_target_count,
                   indexed_target_count,
                   reused_vector_count,
                   entry_ledger_digest,
                   is_current,
                   failure_event_id,
                   failure_kind,
                   failure_actor,
                   failure_reason,
                   failure_stale_before,
                   started_at,
                   completed_at
            FROM search_index_builds
            WHERE search_index_build_id = %s
            """,
            (build_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return SearchIndexBuildState(
            spec=SearchIndexBuildSpec(
                build_id=build_id,
                project_id=str(row[0]),
                snapshot_id=str(row[1]),
                profile_version_id=str(row[2]),
                model=str(row[3]),
                dimensions=int(cast(int, row[4])),
                preprocessing_version=str(row[5]),
                ranking_policy_version=str(row[6]),
                relation_build_id=str(row[7]) if row[7] is not None else None,
            ),
            status=SearchIndexBuildStatus(str(row[8])),
            eligible_target_count=int(cast(int, row[9])),
            indexed_target_count=int(cast(int, row[10])),
            reused_vector_count=int(cast(int, row[11])),
            entry_ledger_digest=str(row[12]) if row[12] is not None else None,
            is_current=bool(row[13]),
            failure_event_id=str(row[14]) if row[14] is not None else None,
            failure_kind=(SearchIndexFailureKind(str(row[15])) if row[15] is not None else None),
            failure_actor=str(row[16]) if row[16] is not None else None,
            failure_reason=str(row[17]) if row[17] is not None else None,
            failure_stale_before=cast(datetime | None, row[18]),
            started_at=cast(datetime, row[19]),
            completed_at=cast(datetime | None, row[20]),
        )


def vector_cache_id(
    *,
    input_digest: str,
    model: str,
    dimensions: int,
    preprocessing_version: str,
) -> str:
    """Return the semantic cache ID used across incremental Snapshots."""

    return _vector_cache_id(
        input_digest=input_digest,
        model=model,
        dimensions=dimensions,
        preprocessing_version=preprocessing_version,
    )


def _vector_cache_id(
    *,
    input_digest: str,
    model: str,
    dimensions: int,
    preprocessing_version: str,
) -> str:
    material = "\x00".join((model, str(dimensions), preprocessing_version, input_digest))
    return f"vector-{sha256(material.encode()).hexdigest()[:32]}"


def search_index_failure_event_id(build_id: str) -> str:
    """Return the single deterministic terminal failure event ID for a build."""

    if not build_id.strip():
        raise ValueError("Search Index build ID must not be blank")
    digest = sha256(f"operamind-search-index-failure-v1\x00{build_id}".encode()).hexdigest()
    return f"search-index-failure-{digest[:32]}"


def _search_index_entry_ledger_digest(
    rows: tuple[SearchIndexEntryLedgerRow, ...],
) -> str:
    canonical = json.dumps(
        [list(row) for row in rows],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return sha256(f"search-index-entry-ledger-v1\x00{canonical}".encode()).hexdigest()


def _target_from_row(row: tuple[object, ...]) -> SearchIndexTarget:
    node = DocumentNode(
        node_id=str(row[0]),
        snapshot_id=str(row[1]),
        document_version_id=str(row[2]),
        parent_node_id=str(row[3]) if row[3] is not None else None,
        node_type=DocumentNodeType(str(row[4])),
        ordinal=int(cast(int, row[5])),
        heading_path=tuple(str(item) for item in cast(list[object], row[6])),
        business_keys=tuple(str(item) for item in cast(list[object], row[7])),
        summary=str(row[8]),
        content=str(row[9]),
        source_refs=tuple(str(item) for item in cast(list[object], row[10])),
        index_eligible=bool(row[11]),
    )
    if str(row[12]) != node.content_digest:
        raise PersistenceConflictError(
            f"Search Index target node content digest differs: {node.node_id}"
        )
    profile = validate_profile_payload_identity(
        profile_version_id=str(row[13]),
        row=tuple(row[14:19]),
        expected_profile_type="DocumentConventionProfile",
    )
    document_type = str(profile.get("document_type", ""))
    if not document_type.strip():
        raise ValueError("Index target has no Document Convention document_type")
    return SearchIndexTarget(
        node=node,
        document_type=document_type,
        relation_labels=tuple(str(item) for item in cast(list[object], row[19])),
    )


def _vector_text(vector: tuple[float, ...]) -> str:
    return json.dumps(vector, ensure_ascii=False, separators=(",", ":"))
