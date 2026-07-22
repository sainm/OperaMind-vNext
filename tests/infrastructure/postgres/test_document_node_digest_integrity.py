import hashlib
import json
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from psycopg import Connection

from operamind.domain import DocumentNode, DocumentNodeType
from operamind.infrastructure.postgres import (
    DocumentNodeRepository,
    DocumentRelationRepository,
    PersistenceConflictError,
    SearchIndexRepository,
)


def _node() -> DocumentNode:
    return DocumentNode(
        node_id="node-001",
        snapshot_id="snapshot-001",
        document_version_id="document-version-001",
        parent_node_id="section-001",
        node_type=DocumentNodeType.SLICE,
        ordinal=0,
        heading_path=("Expense", "screen_element"),
        business_keys=("screen_element:expense/status",),
        summary="Expense status",
        content="default_value: All",
        source_refs=("sheet:1",),
        index_eligible=True,
    )


def _node_row(node: DocumentNode, *, digest: str | None = None) -> tuple[object, ...]:
    return (
        node.node_id,
        node.snapshot_id,
        node.document_version_id,
        node.parent_node_id,
        node.node_type.value,
        node.ordinal,
        list(node.heading_path),
        list(node.business_keys),
        node.summary,
        node.content,
        list(node.source_refs),
        node.index_eligible,
        digest or node.content_digest,
    )


def _connection_with_rows(rows: list[tuple[object, ...]]) -> Connection[Any]:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = rows[0] if rows else None
    cursor.fetchall.return_value = rows
    return cast(Connection[Any], connection)


def _profile_row(*, digest: str | None = None) -> tuple[object, ...]:
    profile = {
        "profile_type": "DocumentConventionProfile",
        "profile_id": "screen-design",
        "profile_version": "1.0.0",
        "document_type": "screen_design",
    }
    canonical = json.dumps(
        profile,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return (
        "profile-version-001",
        profile["profile_type"],
        profile["profile_id"],
        profile["profile_version"],
        profile,
        digest or hashlib.sha256(canonical.encode()).hexdigest(),
    )


def test_context_node_read_revalidates_content_digest() -> None:
    node = _node()
    repository = DocumentNodeRepository(_connection_with_rows([_node_row(node)]))

    assert (
        repository.get_node(
            project_id="project-001",
            snapshot_id=node.snapshot_id,
            node_id=node.node_id,
        )
        == node
    )

    repository = DocumentNodeRepository(_connection_with_rows([_node_row(node, digest="0" * 64)]))
    with pytest.raises(PersistenceConflictError, match="content digest differs"):
        repository.get_node(
            project_id="project-001",
            snapshot_id=node.snapshot_id,
            node_id=node.node_id,
        )


def test_search_index_target_read_revalidates_content_digest() -> None:
    node = _node()
    exact_row = (*_node_row(node), *_profile_row(), ["same_screen"])
    repository = SearchIndexRepository(_connection_with_rows([exact_row]))

    targets = repository.load_targets(
        project_id="project-001",
        snapshot_id=node.snapshot_id,
    )

    assert targets[0].node == node
    assert targets[0].document_type == "screen_design"
    assert targets[0].relation_labels == ("same_screen",)

    drifted_row = (*_node_row(node, digest="0" * 64), *_profile_row(), [])
    repository = SearchIndexRepository(_connection_with_rows([drifted_row]))
    with pytest.raises(PersistenceConflictError, match="target node content digest differs"):
        repository.load_targets(
            project_id="project-001",
            snapshot_id=node.snapshot_id,
        )

    drifted_profile_row = (*_node_row(node), *_profile_row(digest="0" * 64), [])
    repository = SearchIndexRepository(_connection_with_rows([drifted_profile_row]))
    with pytest.raises(PersistenceConflictError, match="Profile version normalized identity"):
        repository.load_targets(
            project_id="project-001",
            snapshot_id=node.snapshot_id,
        )


def test_document_profile_reads_revalidate_normalized_profile_identity() -> None:
    repository = DocumentNodeRepository(_connection_with_rows([_profile_row()]))

    assert repository.list_document_profile_refs(
        project_id="project-001",
        snapshot_id="snapshot-001",
    ) == ("screen-design@1.0.0",)

    repository = DocumentNodeRepository(_connection_with_rows([_profile_row(digest="0" * 64)]))
    with pytest.raises(PersistenceConflictError, match="Profile version normalized identity"):
        repository.list_document_profile_refs(
            project_id="project-001",
            snapshot_id="snapshot-001",
        )


def test_relation_input_revalidates_document_profile_identity() -> None:
    row = (
        "node-001",
        "document-001",
        *_profile_row(),
        "screen_element",
        {"element_id": "status"},
    )
    repository = DocumentRelationRepository(_connection_with_rows([row]))

    facts = repository.load_facts(
        project_id="project-001",
        snapshot_id="snapshot-001",
    )

    assert facts[0].document_type == "screen_design"
    assert facts[0].values == {"element_id": "status"}

    drifted = (
        "node-001",
        "document-001",
        *_profile_row(digest="0" * 64),
        "screen_element",
        {"element_id": "status"},
    )
    repository = DocumentRelationRepository(_connection_with_rows([drifted]))
    with pytest.raises(PersistenceConflictError, match="Profile version normalized identity"):
        repository.load_facts(
            project_id="project-001",
            snapshot_id="snapshot-001",
        )
