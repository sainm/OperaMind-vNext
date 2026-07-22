"""Normalized Canonical Snapshot, Fact, and StructuredChange persistence."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

from psycopg import Connection, Cursor

from operamind.contracts import ContractCatalog
from operamind.domain import (
    CanonicalFact,
    CanonicalFieldEvidence,
    CanonicalSnapshot,
    SnapshotFact,
    StructuredChange,
)
from operamind.infrastructure.postgres.artifact_repository import ArtifactRepository
from operamind.infrastructure.postgres.document_node_repository import DocumentNodeRepository
from operamind.infrastructure.postgres.errors import PersistenceConflictError

SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SnapshotStatus(StrEnum):
    """Persistence lifecycle for a Canonical document snapshot."""

    DRAFT = "draft"
    NEEDS_REVIEW = "needs_review"
    COMMITTED = "committed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class DocumentSnapshotWrite:
    """Document identity and Profile provenance needed to persist one snapshot."""

    project_id: str
    document_id: str
    document_version_id: str
    logical_name: str
    source_ref: str
    content_digest: str
    extractor_ref: str
    profile_version_id: str
    selected_variant_id: str
    status: SnapshotStatus
    snapshot: CanonicalSnapshot

    def __post_init__(self) -> None:
        required = (
            self.project_id,
            self.document_id,
            self.document_version_id,
            self.logical_name,
            self.source_ref,
            self.extractor_ref,
            self.profile_version_id,
            self.selected_variant_id,
        )
        if any(not value.strip() for value in required):
            raise ValueError("Document Snapshot persistence fields must not be blank")
        if not SHA256.fullmatch(self.content_digest):
            raise ValueError("content_digest must be a lowercase SHA-256 digest")


class CanonicalRepository:
    """Idempotent immutable persistence for P1 normalized Canonical data."""

    def __init__(self, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._connection = connection
        self._contracts = contracts
        self._artifacts = ArtifactRepository(connection, contracts)
        self._nodes = DocumentNodeRepository(connection)

    def store_snapshot(self, write: DocumentSnapshotWrite) -> None:
        """Store document/version/membership/facts atomically and reject content conflicts."""

        with self._connection.transaction(), self._connection.cursor() as cursor:
            self._store_document(cursor, write)
            self._store_document_version(cursor, write)
            self._store_snapshot_identity(cursor, write)
            self._store_membership(cursor, write)
            for snapshot_fact in write.snapshot.facts:
                self._store_fact(cursor, write, snapshot_fact)

    def get_snapshot(self, *, project_id: str, snapshot_id: str) -> CanonicalSnapshot | None:
        """Rehydrate one Canonical Snapshot from normalized Fact rows."""

        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM document_snapshots
                WHERE project_id = %s AND document_snapshot_id = %s
                """,
                (project_id, snapshot_id),
            )
            if cursor.fetchone() is None:
                return None
            cursor.execute(
                """
                SELECT document_fact_id, stable_key, fact_type,
                       values_json, source_refs, field_evidence,
                       document_version_id
                FROM document_facts
                WHERE project_id = %s AND document_snapshot_id = %s
                ORDER BY stable_key
                """,
                (project_id, snapshot_id),
            )
            rows = cursor.fetchall()

        facts = tuple(
            SnapshotFact(
                fact_ref=str(row[0]),
                fact=CanonicalFact(
                    stable_key=str(row[1]),
                    fact_type=str(row[2]),
                    values=cast(dict[str, str], row[3]),
                    source_refs=tuple(str(item) for item in cast(list[object], row[4])),
                    field_evidence=_field_evidence_from_json(row[5]),
                ),
            )
            for row in rows
        )
        snapshot = CanonicalSnapshot(snapshot_id=snapshot_id, facts=facts)
        nodes = self._nodes.list_indexable(
            project_id=project_id,
            snapshot_id=snapshot_id,
        )
        if len(nodes) != len(rows):
            raise PersistenceConflictError(
                f"Canonical Snapshot Fact/Node coverage differs: {snapshot_id}"
            )
        nodes_by_business_key = {
            node.business_keys[0]: node for node in nodes if len(node.business_keys) == 1
        }
        if len(nodes_by_business_key) != len(nodes):
            raise PersistenceConflictError(
                f"Canonical Snapshot Slice business keys are invalid: {snapshot_id}"
            )
        for row, snapshot_fact in zip(rows, facts, strict=True):
            fact = snapshot_fact.fact
            node = nodes_by_business_key.get(fact.stable_key)
            expected_content = "\n".join(
                f"{field}: {value}" for field, value in fact.values.items()
            )
            expected = (
                str(row[6]),
                (fact.stable_key,),
                fact.fact_type,
                f"{fact.fact_type} {fact.stable_key}",
                expected_content,
                tuple(sorted(fact.source_refs)),
            )
            actual = (
                node.document_version_id if node is not None else None,
                node.business_keys if node is not None else None,
                node.heading_path[-1] if node is not None else None,
                node.summary if node is not None else None,
                node.content if node is not None else None,
                node.source_refs if node is not None else None,
            )
            if actual != expected:
                raise PersistenceConflictError(
                    f"Canonical Fact differs from Document Slice: {snapshot_fact.fact_ref}"
                )
        return snapshot

    def store_changes(self, changes: tuple[StructuredChange, ...]) -> tuple[str, ...]:
        """Validate and idempotently store normalized StructuredChange rows."""

        stored_ids: list[str] = []
        with self._connection.transaction(), self._connection.cursor() as cursor:
            for change in changes:
                artifact = change.to_artifact()
                self._contracts.validate_artifact(artifact)
                cursor.execute(
                    """
                    INSERT INTO structured_changes (
                        structured_change_id,
                        project_id,
                        source_snapshot_id,
                        target_snapshot_id,
                        stable_key,
                        fact_type,
                        domain,
                        change_type,
                        before_fact_id,
                        after_fact_id,
                        summary,
                        source_refs,
                        confidence,
                        review_status,
                        unknowns
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s::jsonb, %s, %s, %s::jsonb
                    )
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        change.change_id,
                        change.project_id,
                        change.source_snapshot_id,
                        change.target_snapshot_id,
                        change.stable_key,
                        change.fact_type,
                        change.domain,
                        change.change_type.value,
                        change.before.fact_ref if change.before is not None else None,
                        change.after.fact_ref if change.after is not None else None,
                        change.summary,
                        _canonical_json(list(change.source_refs)),
                        change.confidence.value,
                        change.review_status.value,
                        _canonical_json(list(change.unknowns)),
                    ),
                )
                stored = self._load_change_artifact(cursor, change.change_id)
                if stored != artifact:
                    raise PersistenceConflictError(
                        f"StructuredChange identity has different content: {change.change_id}"
                    )
                stored_ids.append(change.change_id)
        return tuple(stored_ids)

    def get_change_artifact(self, change_id: str) -> dict[str, Any] | None:
        """Rehydrate a Change and require its immutable Artifact to match exactly."""

        with self._connection.cursor() as cursor:
            artifact = self._load_change_artifact(cursor, change_id)
        if artifact is not None:
            self._contracts.validate_artifact(artifact)
            persisted = self._artifacts.get(change_id)
            if persisted is None:
                raise PersistenceConflictError(f"StructuredChange Artifact is missing: {change_id}")
            if persisted != artifact:
                raise PersistenceConflictError(
                    f"StructuredChange normalized rows differ from Artifact: {change_id}"
                )
        return artifact

    @staticmethod
    def _store_document(cursor: Cursor[Any], write: DocumentSnapshotWrite) -> None:
        cursor.execute(
            """
            INSERT INTO documents (document_id, project_id, logical_name)
            VALUES (%s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (write.document_id, write.project_id, write.logical_name),
        )
        cursor.execute(
            "SELECT project_id, logical_name FROM documents WHERE document_id = %s",
            (write.document_id,),
        )
        row = cursor.fetchone()
        if row is None or tuple(row) != (write.project_id, write.logical_name):
            raise PersistenceConflictError(
                f"Document identity has different content: {write.document_id}"
            )

    @staticmethod
    def _store_document_version(cursor: Cursor[Any], write: DocumentSnapshotWrite) -> None:
        cursor.execute(
            """
            INSERT INTO document_versions (
                document_version_id,
                project_id,
                document_id,
                source_ref,
                content_digest,
                extractor_ref
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                write.document_version_id,
                write.project_id,
                write.document_id,
                write.source_ref,
                write.content_digest,
                write.extractor_ref,
            ),
        )
        cursor.execute(
            """
            SELECT project_id, document_id, source_ref, content_digest, extractor_ref
            FROM document_versions
            WHERE document_version_id = %s
            """,
            (write.document_version_id,),
        )
        row = cursor.fetchone()
        expected = (
            write.project_id,
            write.document_id,
            write.source_ref,
            write.content_digest,
            write.extractor_ref,
        )
        if row is None or tuple(row) != expected:
            raise PersistenceConflictError(
                f"Document version identity has different content: {write.document_version_id}"
            )

    @staticmethod
    def _store_snapshot_identity(cursor: Cursor[Any], write: DocumentSnapshotWrite) -> None:
        cursor.execute(
            """
            INSERT INTO document_snapshots (
                document_snapshot_id,
                project_id,
                status,
                committed_at
            ) VALUES (
                %s,
                %s,
                %s,
                CASE WHEN %s = 'committed' THEN now() ELSE NULL END
            )
            ON CONFLICT DO NOTHING
            """,
            (
                write.snapshot.snapshot_id,
                write.project_id,
                write.status.value,
                write.status.value,
            ),
        )
        cursor.execute(
            """
            SELECT project_id, status
            FROM document_snapshots
            WHERE document_snapshot_id = %s
            """,
            (write.snapshot.snapshot_id,),
        )
        row = cursor.fetchone()
        if row is None or tuple(row) != (write.project_id, write.status.value):
            raise PersistenceConflictError(
                f"Document Snapshot identity has different content: {write.snapshot.snapshot_id}"
            )

    @staticmethod
    def _store_membership(cursor: Cursor[Any], write: DocumentSnapshotWrite) -> None:
        cursor.execute(
            """
            INSERT INTO snapshot_memberships (
                project_id,
                document_snapshot_id,
                document_version_id,
                profile_version_id,
                selected_variant_id
            ) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                write.project_id,
                write.snapshot.snapshot_id,
                write.document_version_id,
                write.profile_version_id,
                write.selected_variant_id,
            ),
        )
        cursor.execute(
            """
            SELECT project_id, profile_version_id, selected_variant_id
            FROM snapshot_memberships
            WHERE document_snapshot_id = %s AND document_version_id = %s
            """,
            (write.snapshot.snapshot_id, write.document_version_id),
        )
        row = cursor.fetchone()
        expected = (write.project_id, write.profile_version_id, write.selected_variant_id)
        if row is None or tuple(row) != expected:
            raise PersistenceConflictError(
                "Snapshot membership identity has different content: "
                f"{write.snapshot.snapshot_id}/{write.document_version_id}"
            )

    @staticmethod
    def _store_fact(
        cursor: Cursor[Any], write: DocumentSnapshotWrite, snapshot_fact: SnapshotFact
    ) -> None:
        fact = snapshot_fact.fact
        values_json = dict(fact.values)
        source_refs = list(fact.source_refs)
        evidence = _field_evidence_to_json(fact.field_evidence)
        cursor.execute(
            """
            INSERT INTO document_facts (
                document_fact_id,
                project_id,
                document_snapshot_id,
                document_version_id,
                stable_key,
                fact_type,
                values_json,
                source_refs,
                field_evidence
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)
            ON CONFLICT DO NOTHING
            """,
            (
                snapshot_fact.fact_ref,
                write.project_id,
                write.snapshot.snapshot_id,
                write.document_version_id,
                fact.stable_key,
                fact.fact_type,
                _canonical_json(values_json),
                _canonical_json(source_refs),
                _canonical_json(evidence),
            ),
        )
        cursor.execute(
            """
            SELECT project_id, document_snapshot_id, document_version_id,
                   stable_key, fact_type, values_json, source_refs, field_evidence
            FROM document_facts
            WHERE document_fact_id = %s
            """,
            (snapshot_fact.fact_ref,),
        )
        row = cursor.fetchone()
        expected = (
            write.project_id,
            write.snapshot.snapshot_id,
            write.document_version_id,
            fact.stable_key,
            fact.fact_type,
            values_json,
            source_refs,
            evidence,
        )
        if row is None or tuple(row) != expected:
            raise PersistenceConflictError(
                f"Document Fact identity has different content: {snapshot_fact.fact_ref}"
            )

    @staticmethod
    def _load_change_artifact(cursor: Cursor[Any], change_id: str) -> dict[str, Any] | None:
        cursor.execute(
            """
            SELECT c.project_id,
                   c.source_snapshot_id,
                   c.target_snapshot_id,
                   c.stable_key,
                   c.fact_type,
                   c.domain,
                   c.change_type,
                   c.summary,
                   c.source_refs,
                   c.confidence,
                   c.review_status,
                   c.unknowns,
                   bf.document_fact_id,
                   bf.values_json,
                   bf.source_refs,
                   af.document_fact_id,
                   af.values_json,
                   af.source_refs
            FROM structured_changes AS c
            LEFT JOIN document_facts AS bf ON bf.document_fact_id = c.before_fact_id
            LEFT JOIN document_facts AS af ON af.document_fact_id = c.after_fact_id
            WHERE c.structured_change_id = %s
            """,
            (change_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        before = _fact_state_artifact(row[12], row[13], row[14])
        after = _fact_state_artifact(row[15], row[16], row[17])
        return {
            "artifact_type": "StructuredChange",
            "schema_version": "v1",
            "change_id": change_id,
            "project_id": str(row[0]),
            "source_snapshot_id": str(row[1]),
            "target_snapshot_id": str(row[2]),
            "stable_key": str(row[3]),
            "fact_type": str(row[4]),
            "domain": str(row[5]),
            "change_type": str(row[6]),
            "before": before,
            "after": after,
            "summary": str(row[7]),
            "source_refs": cast(list[str], row[8]),
            "confidence": str(row[9]),
            "review_status": str(row[10]),
            "unknowns": cast(list[str], row[11]),
        }


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _field_evidence_to_json(
    evidence: tuple[CanonicalFieldEvidence, ...],
) -> list[dict[str, object]]:
    return [
        {
            "canonical_field": item.canonical_field,
            "source_aliases": list(item.source_aliases),
            "source_refs": list(item.source_refs),
        }
        for item in evidence
    ]


def _field_evidence_from_json(value: object) -> tuple[CanonicalFieldEvidence, ...]:
    rows = cast(list[dict[str, object]], value)
    return tuple(
        CanonicalFieldEvidence(
            canonical_field=str(row["canonical_field"]),
            source_aliases=tuple(str(item) for item in cast(list[object], row["source_aliases"])),
            source_refs=tuple(str(item) for item in cast(list[object], row["source_refs"])),
        )
        for row in rows
    )


def _fact_state_artifact(
    fact_ref: object, values: object, source_refs: object
) -> dict[str, Any] | None:
    if fact_ref is None:
        return None
    return {
        "fact_ref": str(fact_ref),
        "values": cast(dict[str, str], values),
        "source_refs": cast(list[str], source_refs),
    }
