"""Versioned current Build persistence for Profile-derived document relations."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any, cast

from psycopg import Connection, Cursor

from operamind.domain import (
    DocumentRelationFact,
    DocumentRelationPlan,
    PlannedDocumentRelation,
    RelationUnresolvedReason,
    UnresolvedDocumentRelation,
)
from operamind.infrastructure.postgres.errors import PersistenceConflictError
from operamind.infrastructure.postgres.profile_repository import (
    validate_profile_payload_identity,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DocumentRelationBuildStatus(StrEnum):
    """Published relation Build lifecycle states."""

    READY = "ready"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class DocumentRelationBuildSpec:
    """Immutable Build identity and exact Profile/Snapshot scope."""

    build_id: str
    project_id: str
    snapshot_id: str
    profile_version_id: str

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (
                self.build_id,
                self.project_id,
                self.snapshot_id,
                self.profile_version_id,
            )
        ):
            raise ValueError("Document relation Build fields must not be blank")


@dataclass(frozen=True, slots=True)
class DocumentRelationBuildState:
    """Persisted Build state and completeness ledger counts."""

    spec: DocumentRelationBuildSpec
    status: DocumentRelationBuildStatus
    relation_count: int
    unresolved_count: int
    plan_digest: str | None
    is_current: bool
    completed_at: datetime


@dataclass(frozen=True, slots=True)
class DocumentRelationBuildResult:
    """Idempotent publication result."""

    created: bool
    state: DocumentRelationBuildState


class DocumentRelationRepository:
    """Load relation inputs and atomically publish immutable derived Builds."""

    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def load_facts(
        self,
        *,
        project_id: str,
        snapshot_id: str,
    ) -> tuple[DocumentRelationFact, ...]:
        """Load structured Canonical Fact values attached to indexable Slice IDs."""

        if not project_id.strip() or not snapshot_id.strip():
            raise ValueError("Document relation input scope must not be blank")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT n.document_node_id,
                       v.document_id,
                       membership.profile_version_id,
                       profile.profile_type,
                       profile.profile_id,
                       profile.semantic_version,
                       profile.payload,
                       profile.payload_digest,
                       fact.fact_type,
                       fact.values_json
                FROM document_nodes AS n
                JOIN document_facts AS fact
                  ON fact.project_id = n.project_id
                 AND fact.document_snapshot_id = n.document_snapshot_id
                 AND fact.document_version_id = n.document_version_id
                 AND n.business_keys @> jsonb_build_array(fact.stable_key)
                JOIN document_versions AS v
                  ON v.project_id = n.project_id
                 AND v.document_version_id = n.document_version_id
                JOIN snapshot_memberships AS membership
                  ON membership.project_id = n.project_id
                 AND membership.document_snapshot_id = n.document_snapshot_id
                 AND membership.document_version_id = n.document_version_id
                JOIN profile_versions AS profile
                  ON profile.profile_version_id = membership.profile_version_id
                 AND profile.profile_type = 'DocumentConventionProfile'
                WHERE n.project_id = %s
                  AND n.document_snapshot_id = %s
                  AND n.index_eligible
                  AND n.node_type = 'slice'
                ORDER BY n.document_node_id
                """,
                (project_id, snapshot_id),
            )
            rows = cursor.fetchall()
        facts: list[DocumentRelationFact] = []
        for row in rows:
            profile = validate_profile_payload_identity(
                profile_version_id=str(row[2]),
                row=tuple(row[3:8]),
                expected_profile_type="DocumentConventionProfile",
            )
            document_type = str(profile.get("document_type", ""))
            if not document_type.strip():
                raise PersistenceConflictError(
                    f"Document Convention Profile has no document_type: {row[2]}"
                )
            facts.append(
                DocumentRelationFact(
                    node_id=str(row[0]),
                    document_id=str(row[1]),
                    document_type=document_type,
                    fact_type=str(row[8]),
                    values=cast(dict[str, str], row[9]),
                )
            )
        return tuple(facts)

    def publish(
        self,
        *,
        spec: DocumentRelationBuildSpec,
        plan: DocumentRelationPlan,
    ) -> DocumentRelationBuildResult:
        """Publish one current Build; exact replay never reactivates a stale Build."""

        expected_relations = tuple(
            sorted(
                (
                    document_relation_id(
                        project_id=spec.project_id,
                        snapshot_id=spec.snapshot_id,
                        relation=relation,
                    ),
                    relation.rule_id,
                    relation.match_key_digest,
                    relation.source_node_id,
                    relation.target_node_id,
                    relation.relation_label,
                )
                for relation in plan.relations
            )
        )
        expected_unresolved = tuple(
            sorted(
                (
                    unresolved_relation_id(spec.build_id, item),
                    item.rule_id,
                    item.source_node_id,
                    item.match_key_digest,
                    item.candidate_target_count,
                    item.reason.value,
                )
                for item in plan.unresolved
            )
        )
        plan_digest = _relation_plan_digest(expected_relations, expected_unresolved)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            self._lock_snapshot(cursor, spec)
            existing = self._load_build(cursor, spec.build_id)
            if existing is not None:
                self._validate_replay(
                    cursor,
                    existing=existing,
                    spec=spec,
                    expected_relations=expected_relations,
                    expected_unresolved=expected_unresolved,
                    plan_digest=plan_digest,
                )
                return DocumentRelationBuildResult(created=False, state=existing)

            cursor.execute(
                """
                INSERT INTO document_relation_builds (
                    document_relation_build_id,
                    project_id,
                    document_snapshot_id,
                    relation_profile_version_id,
                    status,
                    relation_count,
                    unresolved_count,
                    plan_digest,
                    is_current
                ) VALUES (%s, %s, %s, %s, 'ready', %s, %s, %s, false)
                """,
                (
                    spec.build_id,
                    spec.project_id,
                    spec.snapshot_id,
                    spec.profile_version_id,
                    len(plan.relations),
                    len(plan.unresolved),
                    plan_digest,
                ),
            )
            for relation in plan.relations:
                relation_id = document_relation_id(
                    project_id=spec.project_id,
                    snapshot_id=spec.snapshot_id,
                    relation=relation,
                )
                self._store_relation(cursor, spec=spec, relation_id=relation_id, relation=relation)
                cursor.execute(
                    """
                    INSERT INTO document_relation_entries (
                        document_relation_build_id,
                        project_id,
                        document_snapshot_id,
                        document_relation_id,
                        rule_id,
                        match_key_digest
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        spec.build_id,
                        spec.project_id,
                        spec.snapshot_id,
                        relation_id,
                        relation.rule_id,
                        relation.match_key_digest,
                    ),
                )
            for item in plan.unresolved:
                cursor.execute(
                    """
                    INSERT INTO document_relation_unresolved (
                        unresolved_relation_id,
                        document_relation_build_id,
                        project_id,
                        document_snapshot_id,
                        rule_id,
                        source_node_id,
                        match_key_digest,
                        candidate_target_count,
                        reason
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        unresolved_relation_id(spec.build_id, item),
                        spec.build_id,
                        spec.project_id,
                        spec.snapshot_id,
                        item.rule_id,
                        item.source_node_id,
                        item.match_key_digest,
                        item.candidate_target_count,
                        item.reason.value,
                    ),
                )
            cursor.execute(
                """
                UPDATE document_relation_builds
                SET status = 'stale', is_current = false
                WHERE project_id = %s
                  AND document_snapshot_id = %s
                  AND is_current
                  AND document_relation_build_id <> %s
                """,
                (spec.project_id, spec.snapshot_id, spec.build_id),
            )
            cursor.execute(
                """
                UPDATE document_relation_builds
                SET is_current = true
                WHERE document_relation_build_id = %s
                """,
                (spec.build_id,),
            )
            cursor.execute(
                """
                UPDATE search_index_builds
                SET status = 'stale',
                    is_current = false,
                    completed_at = COALESCE(completed_at, now())
                WHERE project_id = %s
                  AND document_snapshot_id = %s
                  AND status IN ('building', 'ready')
                  AND document_relation_build_id IS DISTINCT FROM %s
                """,
                (spec.project_id, spec.snapshot_id, spec.build_id),
            )
            state = self._load_build(cursor, spec.build_id)
            if state is None or not state.is_current:
                raise PersistenceConflictError("Document relation Build publication failed")
            self._validate_build_integrity(cursor, state)
        return DocumentRelationBuildResult(created=True, state=state)

    def get_build(self, build_id: str) -> DocumentRelationBuildState | None:
        """Load one immutable Build state."""

        if not build_id.strip():
            raise ValueError("Document relation build_id must not be blank")
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
    ) -> DocumentRelationBuildState | None:
        """Load the single current/ready relation Build for a Snapshot."""

        if not project_id.strip() or not snapshot_id.strip():
            raise ValueError("Current relation Build scope must not be blank")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT document_relation_build_id
                FROM document_relation_builds
                WHERE project_id = %s
                  AND document_snapshot_id = %s
                  AND status = 'ready'
                  AND is_current
                """,
                (project_id, snapshot_id),
            )
            row = cursor.fetchone()
            state = self._load_build(cursor, str(row[0])) if row is not None else None
            if state is not None:
                self._validate_build_integrity(cursor, state)
            return state

    @staticmethod
    def _lock_snapshot(cursor: Cursor[Any], spec: DocumentRelationBuildSpec) -> None:
        cursor.execute(
            """
            SELECT status
            FROM document_snapshots
            WHERE project_id = %s AND document_snapshot_id = %s
            FOR UPDATE
            """,
            (spec.project_id, spec.snapshot_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError("Document relation Snapshot does not exist in project")
        if str(row[0]) != "committed":
            raise ValueError("Document relations require a committed Snapshot")

    @staticmethod
    def _store_relation(
        cursor: Cursor[Any],
        *,
        spec: DocumentRelationBuildSpec,
        relation_id: str,
        relation: PlannedDocumentRelation,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO document_relations (
                document_relation_id,
                project_id,
                document_snapshot_id,
                source_node_id,
                target_node_id,
                relation_label
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                relation_id,
                spec.project_id,
                spec.snapshot_id,
                relation.source_node_id,
                relation.target_node_id,
                relation.relation_label,
            ),
        )
        cursor.execute(
            """
            SELECT project_id,
                   document_snapshot_id,
                   source_node_id,
                   target_node_id,
                   relation_label
            FROM document_relations
            WHERE document_relation_id = %s
            """,
            (relation_id,),
        )
        row = cursor.fetchone()
        expected = (
            spec.project_id,
            spec.snapshot_id,
            relation.source_node_id,
            relation.target_node_id,
            relation.relation_label,
        )
        if row is None or tuple(row) != expected:
            raise PersistenceConflictError(
                f"Document relation identity has different content: {relation_id}"
            )

    @staticmethod
    def _validate_replay(
        cursor: Cursor[Any],
        *,
        existing: DocumentRelationBuildState,
        spec: DocumentRelationBuildSpec,
        expected_relations: tuple[tuple[str, str, str, str, str, str], ...],
        expected_unresolved: tuple[tuple[str, str, str, str | None, int, str], ...],
        plan_digest: str,
    ) -> None:
        if (
            existing.spec != spec
            or existing.relation_count != len(expected_relations)
            or existing.unresolved_count != len(expected_unresolved)
            or existing.plan_digest != plan_digest
        ):
            raise PersistenceConflictError(
                f"Document relation Build ID has different content: {spec.build_id}"
            )
        relations = DocumentRelationRepository._load_relation_rows(cursor, spec.build_id)
        unresolved = DocumentRelationRepository._load_unresolved_rows(cursor, spec.build_id)
        if relations != expected_relations or unresolved != expected_unresolved:
            raise PersistenceConflictError(
                f"Document relation Build ID has different entries: {spec.build_id}"
            )

    @staticmethod
    def _validate_build_integrity(
        cursor: Cursor[Any],
        state: DocumentRelationBuildState,
    ) -> None:
        relations = DocumentRelationRepository._load_relation_rows(
            cursor,
            state.spec.build_id,
        )
        unresolved = DocumentRelationRepository._load_unresolved_rows(
            cursor,
            state.spec.build_id,
        )
        if len(relations) != state.relation_count or len(unresolved) != state.unresolved_count:
            raise PersistenceConflictError(
                f"Document relation Build ledger count differs: {state.spec.build_id}"
            )
        for relation_row in relations:
            relation = PlannedDocumentRelation(
                rule_id=relation_row[1],
                match_key_digest=relation_row[2],
                source_node_id=relation_row[3],
                target_node_id=relation_row[4],
                relation_label=relation_row[5],
            )
            if relation_row[0] != document_relation_id(
                project_id=state.spec.project_id,
                snapshot_id=state.spec.snapshot_id,
                relation=relation,
            ):
                raise PersistenceConflictError(
                    f"Document relation semantic identity differs: {relation_row[0]}"
                )
        for unresolved_row in unresolved:
            item = UnresolvedDocumentRelation(
                rule_id=unresolved_row[1],
                source_node_id=unresolved_row[2],
                match_key_digest=unresolved_row[3],
                candidate_target_count=unresolved_row[4],
                reason=RelationUnresolvedReason(unresolved_row[5]),
            )
            if unresolved_row[0] != unresolved_relation_id(state.spec.build_id, item):
                raise PersistenceConflictError(
                    f"Unresolved relation semantic identity differs: {unresolved_row[0]}"
                )
        actual_digest = _relation_plan_digest(relations, unresolved)
        if state.plan_digest is None or not _SHA256.fullmatch(state.plan_digest):
            raise PersistenceConflictError(
                f"Document relation Build requires a versioned plan digest: {state.spec.build_id}"
            )
        if actual_digest != state.plan_digest:
            raise PersistenceConflictError(
                f"Document relation Build plan digest differs: {state.spec.build_id}"
            )

    @staticmethod
    def _load_relation_rows(
        cursor: Cursor[Any],
        build_id: str,
    ) -> tuple[tuple[str, str, str, str, str, str], ...]:
        cursor.execute(
            """
            SELECT entry.document_relation_id,
                   entry.rule_id,
                   entry.match_key_digest,
                   relation.source_node_id,
                   relation.target_node_id,
                   relation.relation_label
            FROM document_relation_entries AS entry
            JOIN document_relations AS relation
              ON relation.project_id = entry.project_id
             AND relation.document_snapshot_id = entry.document_snapshot_id
             AND relation.document_relation_id = entry.document_relation_id
            WHERE entry.document_relation_build_id = %s
            ORDER BY entry.document_relation_id, entry.rule_id, entry.match_key_digest
            """,
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
            )
            for row in cursor.fetchall()
        )

    @staticmethod
    def _load_unresolved_rows(
        cursor: Cursor[Any],
        build_id: str,
    ) -> tuple[tuple[str, str, str, str | None, int, str], ...]:
        cursor.execute(
            """
            SELECT unresolved_relation_id,
                   rule_id,
                   source_node_id,
                   match_key_digest,
                   candidate_target_count,
                   reason
            FROM document_relation_unresolved
            WHERE document_relation_build_id = %s
            ORDER BY unresolved_relation_id
            """,
            (build_id,),
        )
        return tuple(
            (
                str(row[0]),
                str(row[1]),
                str(row[2]),
                str(row[3]) if row[3] is not None else None,
                int(cast(int, row[4])),
                str(row[5]),
            )
            for row in cursor.fetchall()
        )

    @staticmethod
    def _load_build(cursor: Cursor[Any], build_id: str) -> DocumentRelationBuildState | None:
        cursor.execute(
            """
            SELECT project_id,
                   document_snapshot_id,
                   relation_profile_version_id,
                   status,
                   relation_count,
                   unresolved_count,
                   is_current,
                   completed_at,
                   plan_digest
            FROM document_relation_builds
            WHERE document_relation_build_id = %s
            """,
            (build_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return DocumentRelationBuildState(
            spec=DocumentRelationBuildSpec(
                build_id=build_id,
                project_id=str(row[0]),
                snapshot_id=str(row[1]),
                profile_version_id=str(row[2]),
            ),
            status=DocumentRelationBuildStatus(str(row[3])),
            relation_count=int(cast(int, row[4])),
            unresolved_count=int(cast(int, row[5])),
            plan_digest=str(row[8]) if row[8] is not None else None,
            is_current=bool(row[6]),
            completed_at=cast(datetime, row[7]),
        )


def document_relation_id(
    *,
    project_id: str,
    snapshot_id: str,
    relation: PlannedDocumentRelation,
) -> str:
    """Return a deterministic semantic edge identity independent of Build ID."""

    material = "\x00".join(
        (
            project_id,
            snapshot_id,
            relation.source_node_id,
            relation.target_node_id,
            relation.relation_label,
        )
    )
    return f"document-relation-{sha256(material.encode()).hexdigest()[:24]}"


def unresolved_relation_id(build_id: str, item: UnresolvedDocumentRelation) -> str:
    """Return a deterministic unresolved ledger identity within one Build."""

    material = "\x00".join((build_id, item.rule_id, item.source_node_id))
    return f"unresolved-relation-{sha256(material.encode()).hexdigest()[:24]}"


def _relation_plan_digest(
    relations: tuple[tuple[str, str, str, str, str, str], ...],
    unresolved: tuple[tuple[str, str, str, str | None, int, str], ...],
) -> str:
    payload = {
        "relations": [list(row) for row in relations],
        "unresolved": [list(row) for row in unresolved],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(f"document-relation-plan-v1\x00{canonical}".encode()).hexdigest()
