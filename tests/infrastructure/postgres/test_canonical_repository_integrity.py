import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from psycopg import Connection

from operamind.contracts import ContractCatalog
from operamind.domain import (
    CanonicalDocumentNodeBuilder,
    CanonicalFact,
    CanonicalSnapshot,
    SnapshotFact,
)
from operamind.infrastructure.postgres import CanonicalRepository, PersistenceConflictError

ROOT = Path(__file__).parents[3]


def _artifact() -> dict[str, Any]:
    value: object = json.loads(
        (ROOT / "contracts/examples/structured-change.v1.example.json").read_text(encoding="utf-8")
    )
    assert isinstance(value, dict)
    return value


def _normalized_row(artifact: dict[str, Any]) -> tuple[object, ...]:
    before = cast(dict[str, object], artifact["before"])
    after = cast(dict[str, object], artifact["after"])
    return (
        artifact["project_id"],
        artifact["source_snapshot_id"],
        artifact["target_snapshot_id"],
        artifact["stable_key"],
        artifact["fact_type"],
        artifact["domain"],
        artifact["change_type"],
        artifact["summary"],
        artifact["source_refs"],
        artifact["confidence"],
        artifact["review_status"],
        artifact["unknowns"],
        before["fact_ref"],
        before["values"],
        before["source_refs"],
        after["fact_ref"],
        after["values"],
        after["source_refs"],
    )


def _repository(row: tuple[object, ...], persisted: dict[str, Any] | None) -> CanonicalRepository:
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchone.return_value = row
    repository = CanonicalRepository(
        cast(Connection[Any], connection),
        ContractCatalog.load(ROOT / "contracts"),
    )
    repository._artifacts = MagicMock()
    repository._artifacts.get.return_value = persisted
    return repository


def test_change_read_requires_exact_immutable_artifact() -> None:
    artifact = _artifact()
    repository = _repository(_normalized_row(artifact), artifact)

    assert repository.get_change_artifact(str(artifact["change_id"])) == artifact


def test_change_read_rejects_missing_or_drifted_artifact() -> None:
    artifact = _artifact()
    missing = _repository(_normalized_row(artifact), None)
    with pytest.raises(PersistenceConflictError, match="Artifact is missing"):
        missing.get_change_artifact(str(artifact["change_id"]))

    drifted = dict(artifact)
    drifted["summary"] = "Different schema-valid summary"
    repository = _repository(_normalized_row(drifted), artifact)
    with pytest.raises(PersistenceConflictError, match="differ from Artifact"):
        repository.get_change_artifact(str(artifact["change_id"]))


def _snapshot() -> CanonicalSnapshot:
    return CanonicalSnapshot(
        snapshot_id="snapshot-001",
        facts=(
            SnapshotFact(
                fact_ref="fact-001",
                fact=CanonicalFact(
                    fact_type="screen_element",
                    stable_key="screen_element:expense/status",
                    values={"default_value": "All", "element_id": "status"},
                    source_refs=("screen.xlsx#items!A2",),
                    field_evidence=(),
                ),
            ),
        ),
    )


def _snapshot_repository(
    *,
    values: dict[str, str] | None = None,
    include_nodes: bool = True,
) -> CanonicalRepository:
    snapshot = _snapshot()
    fact = snapshot.facts[0]
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (1,)
    cursor.fetchall.return_value = [
        (
            fact.fact_ref,
            fact.fact.stable_key,
            fact.fact.fact_type,
            values or dict(fact.fact.values),
            list(fact.fact.source_refs),
            [],
            "document-version-001",
        )
    ]
    repository = CanonicalRepository(
        cast(Connection[Any], connection),
        ContractCatalog.load(ROOT / "contracts"),
    )
    repository._nodes = MagicMock()
    repository._nodes.list_indexable.return_value = (
        CanonicalDocumentNodeBuilder().build(
            snapshot=snapshot,
            document_version_id="document-version-001",
            logical_name="screen.xlsx",
            document_type="screen_design",
        )[1:]
        if include_nodes
        else ()
    )
    return repository


def test_snapshot_read_requires_exact_digest_validated_slice_coverage() -> None:
    snapshot = _snapshot()
    repository = _snapshot_repository()

    assert (
        repository.get_snapshot(
            project_id="project-001",
            snapshot_id=snapshot.snapshot_id,
        )
        == snapshot
    )


def test_snapshot_read_rejects_missing_or_drifted_slices() -> None:
    with pytest.raises(PersistenceConflictError, match="coverage differs"):
        _snapshot_repository(include_nodes=False).get_snapshot(
            project_id="project-001",
            snapshot_id="snapshot-001",
        )

    with pytest.raises(PersistenceConflictError, match="differs from Document Slice"):
        _snapshot_repository(
            values={"default_value": "Tampered", "element_id": "status"}
        ).get_snapshot(
            project_id="project-001",
            snapshot_id="snapshot-001",
        )
