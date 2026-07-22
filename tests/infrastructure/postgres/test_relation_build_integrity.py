from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from psycopg import Cursor

from operamind.domain import (
    PlannedDocumentRelation,
    RelationUnresolvedReason,
    UnresolvedDocumentRelation,
)
from operamind.infrastructure.postgres import (
    DocumentRelationBuildSpec,
    DocumentRelationBuildState,
    DocumentRelationBuildStatus,
    DocumentRelationRepository,
    PersistenceConflictError,
    document_relation_id,
    unresolved_relation_id,
)
from operamind.infrastructure.postgres.relation_repository import _relation_plan_digest


def _rows() -> (
    tuple[tuple[str, str, str, str, str, str], ...],
    tuple[tuple[str, str, str, str | None, int, str], ...],
):
    relation = PlannedDocumentRelation(
        rule_id="screen-to-api",
        relation_label="calls_api",
        source_node_id="screen-node-001",
        target_node_id="api-node-001",
        match_key_digest="1" * 64,
    )
    relation_row = (
        document_relation_id(
            project_id="project-001",
            snapshot_id="snapshot-001",
            relation=relation,
        ),
        relation.rule_id,
        relation.match_key_digest,
        relation.source_node_id,
        relation.target_node_id,
        relation.relation_label,
    )
    unresolved = UnresolvedDocumentRelation(
        rule_id="screen-to-api",
        source_node_id="screen-node-002",
        match_key_digest="2" * 64,
        candidate_target_count=0,
        reason=RelationUnresolvedReason.NO_TARGET,
    )
    unresolved_row = (
        unresolved_relation_id("relation-build-001", unresolved),
        unresolved.rule_id,
        unresolved.source_node_id,
        unresolved.match_key_digest,
        unresolved.candidate_target_count,
        unresolved.reason.value,
    )
    return (relation_row,), (unresolved_row,)


def _state(*, plan_digest: str | None = None) -> DocumentRelationBuildState:
    relations, unresolved = _rows()
    return DocumentRelationBuildState(
        spec=DocumentRelationBuildSpec(
            build_id="relation-build-001",
            project_id="project-001",
            snapshot_id="snapshot-001",
            profile_version_id="relation-profile-001",
        ),
        status=DocumentRelationBuildStatus.READY,
        relation_count=1,
        unresolved_count=1,
        plan_digest=plan_digest or _relation_plan_digest(relations, unresolved),
        is_current=True,
        completed_at=datetime(2026, 7, 16, tzinfo=UTC),
    )


def _cursor(
    relations: tuple[tuple[str, str, str, str, str, str], ...],
    unresolved: tuple[tuple[str, str, str, str | None, int, str], ...],
) -> Cursor[Any]:
    cursor = MagicMock()
    cursor.fetchall.side_effect = [list(relations), list(unresolved)]
    return cast(Cursor[Any], cursor)


def test_relation_build_read_validates_full_plan_digest() -> None:
    relations, unresolved = _rows()

    DocumentRelationRepository._validate_build_integrity(
        _cursor(relations, unresolved),
        _state(),
    )


def test_relation_build_read_rejects_legacy_or_drifted_ledgers() -> None:
    relations, unresolved = _rows()
    with pytest.raises(PersistenceConflictError, match="requires a versioned plan digest"):
        DocumentRelationRepository._validate_build_integrity(
            _cursor(relations, unresolved),
            replace(_state(), plan_digest=None),
        )

    with pytest.raises(PersistenceConflictError, match="plan digest differs"):
        DocumentRelationRepository._validate_build_integrity(
            _cursor(relations, unresolved),
            _state(plan_digest="0" * 64),
        )

    with pytest.raises(PersistenceConflictError, match="ledger count differs"):
        DocumentRelationRepository._validate_build_integrity(
            _cursor((), unresolved),
            _state(),
        )

    with pytest.raises(PersistenceConflictError, match="semantic identity differs"):
        drifted = (("different-relation-id", *relations[0][1:]),)
        DocumentRelationRepository._validate_build_integrity(
            _cursor(drifted, unresolved),
            _state(plan_digest=_relation_plan_digest(drifted, unresolved)),
        )
