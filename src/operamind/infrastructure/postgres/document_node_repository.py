"""Canonical DocumentNode persistence in PostgreSQL."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

from psycopg import Connection, Cursor

from operamind.domain import DocumentNode, DocumentNodeType
from operamind.infrastructure.postgres.errors import PersistenceConflictError
from operamind.infrastructure.postgres.profile_repository import (
    validate_profile_payload_identity,
)


class DocumentExpansionReason(StrEnum):
    """Bounded graph expansion reasons supported by ContextPackage v1."""

    ADJACENT = "adjacent"
    RELATED = "related"
    CROSS_DOCUMENT = "cross_document"


@dataclass(frozen=True, slots=True)
class DocumentNodeRecord:
    """Canonical node plus its stable logical document identity."""

    node: DocumentNode
    document_id: str


@dataclass(frozen=True, slots=True)
class DocumentNodeExpansion:
    """One neighborhood node and the seed that justified expansion."""

    record: DocumentNodeRecord
    reason: DocumentExpansionReason
    source_node_id: str


class DocumentNodeRepository:
    """Idempotently store and scope Canonical Section/Slice nodes."""

    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def store_nodes(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        nodes: tuple[DocumentNode, ...],
    ) -> tuple[str, ...]:
        """Store a self-contained node tree and reject immutable conflicts."""

        if not project_id.strip() or not snapshot_id.strip():
            raise ValueError("Document node scope fields must not be blank")
        node_ids = {node.node_id for node in nodes}
        if len(node_ids) != len(nodes):
            raise ValueError("Document node batch contains duplicate node IDs")
        for node in nodes:
            if node.snapshot_id != snapshot_id:
                raise ValueError("Document node does not belong to the requested snapshot")
            if node.parent_node_id is not None and node.parent_node_id not in node_ids:
                raise ValueError("Document node parent must be present in the same batch")

        ordered = tuple(
            sorted(
                nodes,
                key=lambda node: (
                    node.parent_node_id is not None,
                    node.node_type.value,
                    node.ordinal,
                    node.node_id,
                ),
            )
        )
        with self._connection.transaction(), self._connection.cursor() as cursor:
            for node in ordered:
                self._store_node(cursor, project_id=project_id, node=node)
        return tuple(node.node_id for node in nodes)

    def get_node(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        node_id: str,
    ) -> DocumentNode | None:
        """Load one node only within its Project and Snapshot scope."""

        required = (project_id, snapshot_id, node_id)
        if any(not value.strip() for value in required):
            raise ValueError("Document node identity fields must not be blank")
        with self._connection.cursor() as cursor:
            cursor.execute(
                _NODE_SELECT
                + """
                WHERE project_id = %s
                  AND document_snapshot_id = %s
                  AND document_node_id = %s
                """,
                (project_id, snapshot_id, node_id),
            )
            row = cursor.fetchone()
        return _node_from_row(row) if row is not None else None

    def list_indexable(
        self,
        *,
        project_id: str,
        snapshot_id: str,
    ) -> tuple[DocumentNode, ...]:
        """Return only index-eligible nodes for one exact Project/Snapshot."""

        if not project_id.strip() or not snapshot_id.strip():
            raise ValueError("Document node list scope fields must not be blank")
        with self._connection.cursor() as cursor:
            cursor.execute(
                _NODE_SELECT
                + """
                WHERE project_id = %s
                  AND document_snapshot_id = %s
                  AND index_eligible
                ORDER BY document_node_id
                """,
                (project_id, snapshot_id),
            )
            rows = cursor.fetchall()
        return tuple(_node_from_row(row) for row in rows)

    def find_by_business_key(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        business_key: str,
    ) -> tuple[DocumentNodeRecord, ...]:
        """Find exact target Slice nodes for one StructuredChange Stable Key."""

        required = (project_id, snapshot_id, business_key)
        if any(not value.strip() for value in required):
            raise ValueError("Business-key node lookup fields must not be blank")
        with self._connection.cursor() as cursor:
            cursor.execute(
                _NODE_RECORD_SELECT
                + """
                WHERE n.project_id = %s
                  AND n.document_snapshot_id = %s
                  AND n.index_eligible
                  AND n.business_keys @> %s::jsonb
                ORDER BY n.document_node_id
                """,
                (project_id, snapshot_id, _canonical_json([business_key])),
            )
            rows = cursor.fetchall()
        return tuple(_node_record_from_row(row) for row in rows)

    def get_nodes_with_documents(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        node_ids: tuple[str, ...],
    ) -> tuple[DocumentNodeRecord, ...]:
        """Batch rehydrate exact node IDs without crossing Project/Snapshot scope."""

        _validate_node_id_batch(node_ids)
        if not node_ids:
            return ()
        if not project_id.strip() or not snapshot_id.strip():
            raise ValueError("Document node batch scope fields must not be blank")
        with self._connection.cursor() as cursor:
            cursor.execute(
                _NODE_RECORD_SELECT
                + """
                WHERE n.project_id = %s
                  AND n.document_snapshot_id = %s
                  AND n.document_node_id = ANY(%s)
                ORDER BY n.document_node_id
                """,
                (project_id, snapshot_id, list(node_ids)),
            )
            rows = cursor.fetchall()
        return tuple(_node_record_from_row(row) for row in rows)

    def expand_neighborhood(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        seed_node_ids: tuple[str, ...],
        adjacent_distance: int = 1,
    ) -> tuple[DocumentNodeExpansion, ...]:
        """Return bounded adjacent and explicit relation neighbors for seed Slices."""

        _validate_node_id_batch(seed_node_ids)
        if not seed_node_ids:
            return ()
        if not project_id.strip() or not snapshot_id.strip():
            raise ValueError("Document neighborhood scope fields must not be blank")
        if not 0 <= adjacent_distance <= 10:
            raise ValueError("adjacent_distance must be between 0 and 10")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                WITH seed AS (
                    SELECT document_node_id,
                           document_version_id,
                           parent_node_id,
                           ordinal
                    FROM document_nodes
                    WHERE project_id = %s
                      AND document_snapshot_id = %s
                      AND document_node_id = ANY(%s)
                ), expanded AS (
                    SELECT sibling.document_node_id,
                           'adjacent'::text AS reason,
                           seed.document_node_id AS source_node_id
                    FROM seed
                    JOIN document_nodes AS sibling
                      ON sibling.project_id = %s
                     AND sibling.document_snapshot_id = %s
                     AND sibling.parent_node_id = seed.parent_node_id
                     AND sibling.document_node_id <> seed.document_node_id
                     AND abs(sibling.ordinal - seed.ordinal) <= %s
                    WHERE %s > 0
                    UNION ALL
                    SELECT related.document_node_id,
                           CASE
                               WHEN related.document_version_id <> seed.document_version_id
                               THEN 'cross_document'
                               ELSE 'related'
                           END AS reason,
                           seed.document_node_id AS source_node_id
                    FROM seed
                    JOIN document_relation_builds AS relation_build
                      ON relation_build.project_id = %s
                     AND relation_build.document_snapshot_id = %s
                     AND relation_build.status = 'ready'
                     AND relation_build.is_current
                    JOIN document_relation_entries AS relation_entry
                      ON relation_entry.document_relation_build_id =
                         relation_build.document_relation_build_id
                     AND relation_entry.project_id = relation_build.project_id
                     AND relation_entry.document_snapshot_id =
                         relation_build.document_snapshot_id
                    JOIN document_relations AS relation
                      ON relation.document_relation_id = relation_entry.document_relation_id
                     AND relation.project_id = relation_entry.project_id
                     AND relation.document_snapshot_id = relation_entry.document_snapshot_id
                     AND (
                         relation.source_node_id = seed.document_node_id
                         OR relation.target_node_id = seed.document_node_id
                     )
                    JOIN document_nodes AS related
                      ON related.project_id = relation.project_id
                     AND related.document_snapshot_id = relation.document_snapshot_id
                     AND related.document_node_id = CASE
                         WHEN relation.source_node_id = seed.document_node_id
                         THEN relation.target_node_id
                         ELSE relation.source_node_id
                     END
                )
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
                       v.document_id,
                       expanded.reason,
                       expanded.source_node_id
                FROM expanded
                JOIN document_nodes AS n
                  ON n.project_id = %s
                 AND n.document_snapshot_id = %s
                 AND n.document_node_id = expanded.document_node_id
                JOIN document_versions AS v
                  ON v.project_id = n.project_id
                 AND v.document_version_id = n.document_version_id
                ORDER BY n.document_node_id, expanded.reason, expanded.source_node_id
                """,
                (
                    project_id,
                    snapshot_id,
                    list(seed_node_ids),
                    project_id,
                    snapshot_id,
                    adjacent_distance,
                    adjacent_distance,
                    project_id,
                    snapshot_id,
                    project_id,
                    snapshot_id,
                ),
            )
            rows = cursor.fetchall()
        return tuple(
            DocumentNodeExpansion(
                record=_node_record_from_row(row[:14]),
                reason=DocumentExpansionReason(str(row[14])),
                source_node_id=str(row[15]),
            )
            for row in rows
        )

    def list_document_profile_refs(
        self,
        *,
        project_id: str,
        snapshot_id: str,
    ) -> tuple[str, ...]:
        """Return semantic Document Convention Profile refs for one Snapshot."""

        if not project_id.strip() or not snapshot_id.strip():
            raise ValueError("Document Profile scope fields must not be blank")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT membership.profile_version_id,
                                v.profile_type,
                                v.profile_id,
                                v.semantic_version,
                                v.payload,
                                v.payload_digest
                FROM snapshot_memberships AS membership
                JOIN profile_versions AS v
                  ON v.profile_version_id = membership.profile_version_id
                WHERE membership.project_id = %s
                  AND membership.document_snapshot_id = %s
                  AND v.profile_type = 'DocumentConventionProfile'
                ORDER BY membership.profile_version_id
                """,
                (project_id, snapshot_id),
            )
            rows = cursor.fetchall()
        profiles = tuple(
            validate_profile_payload_identity(
                profile_version_id=str(row[0]),
                row=tuple(row[1:]),
                expected_profile_type="DocumentConventionProfile",
            )
            for row in rows
        )
        return tuple(
            sorted(
                {f"{profile['profile_id']}@{profile['profile_version']}" for profile in profiles}
            )
        )

    @staticmethod
    def _store_node(
        cursor: Cursor[Any],
        *,
        project_id: str,
        node: DocumentNode,
    ) -> None:
        heading_path = list(node.heading_path)
        business_keys = list(node.business_keys)
        source_refs = list(node.source_refs)
        cursor.execute(
            """
            INSERT INTO document_nodes (
                document_node_id,
                project_id,
                document_snapshot_id,
                document_version_id,
                parent_node_id,
                node_type,
                ordinal,
                heading_path,
                business_keys,
                summary,
                content,
                source_refs,
                index_eligible,
                content_digest
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s::jsonb, %s::jsonb, %s, %s, %s::jsonb, %s, %s
            )
            ON CONFLICT DO NOTHING
            """,
            (
                node.node_id,
                project_id,
                node.snapshot_id,
                node.document_version_id,
                node.parent_node_id,
                node.node_type.value,
                node.ordinal,
                _canonical_json(heading_path),
                _canonical_json(business_keys),
                node.summary,
                node.content,
                _canonical_json(source_refs),
                node.index_eligible,
                node.content_digest,
            ),
        )
        cursor.execute(
            """
            SELECT project_id,
                   document_snapshot_id,
                   document_version_id,
                   parent_node_id,
                   node_type,
                   ordinal,
                   heading_path,
                   business_keys,
                   summary,
                   content,
                   source_refs,
                   index_eligible,
                   content_digest
            FROM document_nodes
            WHERE document_node_id = %s
            """,
            (node.node_id,),
        )
        row = cursor.fetchone()
        expected = (
            project_id,
            node.snapshot_id,
            node.document_version_id,
            node.parent_node_id,
            node.node_type.value,
            node.ordinal,
            heading_path,
            business_keys,
            node.summary,
            node.content,
            source_refs,
            node.index_eligible,
            node.content_digest,
        )
        if row is None or tuple(row) != expected:
            raise PersistenceConflictError(
                f"Document node identity has different content: {node.node_id}"
            )


_NODE_SELECT = """
    SELECT document_node_id,
           document_snapshot_id,
           document_version_id,
           parent_node_id,
           node_type,
           ordinal,
           heading_path,
           business_keys,
           summary,
           content,
           source_refs,
           index_eligible,
           content_digest
    FROM document_nodes
"""

_NODE_RECORD_SELECT = """
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
           v.document_id
    FROM document_nodes AS n
    JOIN document_versions AS v
      ON v.project_id = n.project_id
     AND v.document_version_id = n.document_version_id
"""


def _node_from_row(row: tuple[object, ...]) -> DocumentNode:
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
        raise PersistenceConflictError(f"Document node content digest differs: {node.node_id}")
    return node


def _node_record_from_row(row: tuple[object, ...]) -> DocumentNodeRecord:
    return DocumentNodeRecord(node=_node_from_row(row[:13]), document_id=str(row[13]))


def _validate_node_id_batch(node_ids: tuple[str, ...]) -> None:
    if len(node_ids) > 1_000:
        raise ValueError("Document node batch must not exceed 1000 IDs")
    if any(not node_id.strip() for node_id in node_ids):
        raise ValueError("Document node IDs must not be blank")
    if len(node_ids) != len(set(node_ids)):
        raise ValueError("Document node IDs must be unique")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
