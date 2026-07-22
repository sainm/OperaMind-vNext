from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from psycopg import Cursor

from operamind.infrastructure.postgres import (
    PersistenceConflictError,
    SearchIndexBuildSpec,
    SearchIndexBuildState,
    SearchIndexBuildStatus,
    SearchIndexRepository,
    vector_cache_id,
)
from operamind.infrastructure.postgres.search_index_repository import (
    SearchIndexEntryLedgerRow,
    _search_index_entry_ledger_digest,
)


def _rows() -> tuple[SearchIndexEntryLedgerRow, ...]:
    input_digest = "a" * 64
    cache_id = vector_cache_id(
        input_digest=input_digest,
        model="embedding-model-v1",
        dimensions=3,
        preprocessing_version="document-embedding-input-v1",
    )
    return (
        (
            "project-001",
            "snapshot-001",
            "embedding-profile-001",
            "node-001",
            input_digest,
            cache_id,
            "embedding-model-v1",
            3,
            "document-embedding-input-v1",
            "expense status filter",
            input_digest,
            "embedding-model-v1",
            3,
            "document-embedding-input-v1",
            "b" * 64,
        ),
    )


def _state(
    *,
    entry_ledger_digest: str | None = None,
    status: SearchIndexBuildStatus = SearchIndexBuildStatus.READY,
) -> SearchIndexBuildState:
    rows = _rows()
    published = status in {SearchIndexBuildStatus.READY, SearchIndexBuildStatus.STALE}
    return SearchIndexBuildState(
        spec=SearchIndexBuildSpec(
            build_id="search-build-001",
            project_id="project-001",
            snapshot_id="snapshot-001",
            profile_version_id="embedding-profile-001",
            model="embedding-model-v1",
            dimensions=3,
            preprocessing_version="document-embedding-input-v1",
            ranking_policy_version="rrf-v1",
        ),
        status=status,
        eligible_target_count=1,
        indexed_target_count=1 if published else 0,
        reused_vector_count=0,
        entry_ledger_digest=(
            entry_ledger_digest
            if entry_ledger_digest is not None
            else (_search_index_entry_ledger_digest(rows) if published else None)
        ),
        is_current=status is SearchIndexBuildStatus.READY,
        failure_event_id=None,
        failure_kind=None,
        failure_actor=None,
        failure_reason=None,
        failure_stale_before=None,
        started_at=datetime(2026, 7, 16, tzinfo=UTC),
        completed_at=datetime(2026, 7, 16, tzinfo=UTC) if published else None,
    )


def _cursor(rows: tuple[SearchIndexEntryLedgerRow, ...]) -> Cursor[Any]:
    cursor = MagicMock()
    cursor.fetchall.return_value = list(rows)
    return cast(Cursor[Any], cursor)


def test_search_index_build_read_validates_full_entry_and_vector_ledger() -> None:
    SearchIndexRepository._validate_build_integrity(_cursor(_rows()), _state())


def test_search_index_build_read_rejects_legacy_deleted_or_drifted_ledgers() -> None:
    with pytest.raises(PersistenceConflictError, match="versioned entry ledger digest"):
        SearchIndexRepository._validate_build_integrity(
            _cursor(_rows()),
            replace(_state(), entry_ledger_digest=None),
        )

    with pytest.raises(PersistenceConflictError, match="ledger count differs"):
        SearchIndexRepository._validate_build_integrity(_cursor(()), _state())

    drifted_keyword = (replace_tuple(_rows()[0], 9, "changed keyword semantics"),)
    with pytest.raises(PersistenceConflictError, match="entry ledger digest differs"):
        SearchIndexRepository._validate_build_integrity(
            _cursor(drifted_keyword),
            _state(),
        )

    drifted_vector = (replace_tuple(_rows()[0], 14, "c" * 64),)
    with pytest.raises(PersistenceConflictError, match="entry ledger digest differs"):
        SearchIndexRepository._validate_build_integrity(
            _cursor(drifted_vector),
            _state(),
        )


def test_search_index_build_read_rejects_vector_identity_drift() -> None:
    drifted_metadata = (replace_tuple(_rows()[0], 10, "d" * 64),)
    with pytest.raises(PersistenceConflictError, match="vector metadata differs"):
        SearchIndexRepository._validate_build_integrity(
            _cursor(drifted_metadata),
            replace(
                _state(),
                entry_ledger_digest=_search_index_entry_ledger_digest(drifted_metadata),
            ),
        )

    drifted_cache_id = (replace_tuple(_rows()[0], 5, "vector-different"),)
    with pytest.raises(PersistenceConflictError, match="vector semantic identity differs"):
        SearchIndexRepository._validate_build_integrity(
            _cursor(drifted_cache_id),
            replace(
                _state(),
                entry_ledger_digest=_search_index_entry_ledger_digest(drifted_cache_id),
            ),
        )


def test_unpublished_search_index_build_must_not_hide_entries() -> None:
    with pytest.raises(PersistenceConflictError, match="Unpublished Search Index"):
        SearchIndexRepository._validate_build_integrity(
            _cursor(_rows()),
            _state(status=SearchIndexBuildStatus.BUILDING),
        )


def replace_tuple(
    row: SearchIndexEntryLedgerRow,
    index: int,
    value: str,
) -> SearchIndexEntryLedgerRow:
    values = list(row)
    values[index] = value
    return cast(SearchIndexEntryLedgerRow, tuple(values))
