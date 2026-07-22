import hashlib
import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from psycopg import Connection

from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres import ArtifactRepository, PersistenceConflictError

ROOT = Path(__file__).parents[3]


def _artifact() -> dict[str, Any]:
    value: object = json.loads(
        (ROOT / "contracts/examples/structured-change.v1.example.json").read_text(encoding="utf-8")
    )
    assert isinstance(value, dict)
    return value


def _digest(artifact: dict[str, Any]) -> str:
    canonical = json.dumps(
        artifact,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _repository_with_row(row: tuple[object, ...]) -> ArtifactRepository:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = row
    return ArtifactRepository(
        cast(Connection[Any], connection),
        ContractCatalog.load(ROOT / "contracts"),
    )


def test_get_revalidates_normalized_artifact_identity() -> None:
    artifact = _artifact()
    repository = _repository_with_row(
        (
            artifact["artifact_type"],
            artifact["schema_version"],
            artifact["project_id"],
            None,
            artifact,
            _digest(artifact),
        )
    )

    assert repository.get(str(artifact["change_id"])) == artifact


def test_get_for_share_uses_a_database_row_lock() -> None:
    artifact = _artifact()
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (
        artifact["artifact_type"],
        artifact["schema_version"],
        artifact["project_id"],
        None,
        artifact,
        _digest(artifact),
    )
    repository = ArtifactRepository(
        cast(Connection[Any], connection),
        ContractCatalog.load(ROOT / "contracts"),
    )

    assert repository.get_for_share(str(artifact["change_id"])) == artifact
    assert "FOR SHARE" in cursor.execute.call_args.args[0]


@pytest.mark.parametrize("drifted_index", [0, 1, 2, 5])
def test_get_rejects_artifact_envelope_or_digest_drift(drifted_index: int) -> None:
    artifact = _artifact()
    row: list[object] = [
        artifact["artifact_type"],
        artifact["schema_version"],
        artifact["project_id"],
        None,
        artifact,
        _digest(artifact),
    ]
    row[drifted_index] = "different"
    repository = _repository_with_row(tuple(row))

    with pytest.raises(PersistenceConflictError, match="normalized identity differs"):
        repository.get(str(artifact["change_id"]))
