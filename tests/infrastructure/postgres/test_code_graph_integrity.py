import hashlib
import json
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from psycopg import Cursor

from operamind.infrastructure.postgres import (
    CodeGraphSnapshotRepository,
    PersistenceConflictError,
)
from operamind.infrastructure.postgres.code_graph_repository import (
    CodeGraphEdgeLedgerRow,
    _expected_graph_rows,
    _expected_test_binding_rows,
    _validate_graph_artifact,
)


def _artifact() -> dict[str, Any]:
    profile_ref = "spring-web-example@1.0.0"
    return {
        "artifact_type": "CodeGraphSnapshot",
        "schema_version": "v1",
        "code_graph_snapshot_id": "graph-001",
        "project_id": "project-001",
        "repository_id": "repository-001",
        "repository_revision": "commit-001",
        "framework_profile_refs": [profile_ref],
        "scan_roots": ["src/main", "src/test"],
        "scan_status": "complete",
        "framework_markers_found": [],
        "diagnostics": [],
        "files": [
            {
                "file_id": "file-production",
                "path": "src/main/ExpenseService.java",
                "language": "java",
                "role": "production",
                "content_hash": "sha256:production",
                "symbols": [
                    {
                        "symbol_id": "symbol-production",
                        "symbol_type": "method",
                        "name": "search",
                        "signature": "search(String)",
                        "start_line": 10,
                        "end_line": 20,
                    }
                ],
            },
            {
                "file_id": "file-test",
                "path": "src/test/ExpenseServiceTest.java",
                "language": "java",
                "role": "test",
                "content_hash": "sha256:test",
                "symbols": [
                    {
                        "symbol_id": "symbol-test",
                        "symbol_type": "method",
                        "name": "testsSearch",
                        "signature": "testsSearch()",
                        "start_line": 5,
                        "end_line": 9,
                    }
                ],
            },
        ],
        "edges": [
            {
                "edge_id": "edge-tests",
                "edge_type": "tests",
                "from_ref": "symbol-test",
                "to_ref": "symbol-production",
                "resolution_status": "resolved",
                "confidence": "high",
                "extractor": "junit_test",
                "profile_version": profile_ref,
                "source_location": {
                    "path": "src/test/ExpenseServiceTest.java",
                    "start_line": 5,
                    "end_line": 9,
                },
            }
        ],
    }


def _profile_row() -> tuple[object, ...]:
    profile: dict[str, Any] = {
        "profile_type": "CodeFrameworkProfile",
        "profile_id": "spring-web-example",
        "profile_version": "1.0.0",
    }
    canonical = json.dumps(profile, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return (
        "project-001",
        "spring-web-example@1.0.0",
        "profile-version-001",
        "CodeFrameworkProfile",
        "spring-web-example",
        "1.0.0",
        profile,
        hashlib.sha256(canonical.encode()).hexdigest(),
    )


def _cursor(
    *,
    edges: tuple[CodeGraphEdgeLedgerRow, ...] | None = None,
) -> Cursor[Any]:
    graph = _validate_graph_artifact(_artifact())
    files, symbols, expected_edges = _expected_graph_rows(graph)
    bindings = _expected_test_binding_rows(graph)
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        (
            graph.project_id,
            graph.repository_id,
            graph.repository_revision,
            "complete",
            list(graph.scan_roots),
            len(files),
            len(symbols),
            len(expected_edges),
            0,
            True,
            None,
        ),
        (
            "full",
            None,
            [],
            ["src/main/ExpenseService.java", "src/test/ExpenseServiceTest.java"],
            2,
            0,
            [],
        ),
    ]
    cursor.fetchall.side_effect = [
        [_profile_row()],
        list(files),
        list(symbols),
        list(edges if edges is not None else expected_edges),
        list(bindings),
    ]
    return cast(Cursor[Any], cursor)


def test_code_graph_read_validates_artifact_against_every_normalized_ledger() -> None:
    CodeGraphSnapshotRepository._validate_normalized_integrity(
        _cursor(),
        graph=_validate_graph_artifact(_artifact()),
    )


def test_code_graph_read_rejects_normalized_edge_drift() -> None:
    graph = _validate_graph_artifact(_artifact())
    _, _, edges = _expected_graph_rows(graph)
    drifted = (replace_tuple(edges[0], 7, "different-extractor"),)

    with pytest.raises(PersistenceConflictError, match="Edge ledger differs"):
        CodeGraphSnapshotRepository._validate_normalized_integrity(
            _cursor(edges=drifted),
            graph=graph,
        )


def replace_tuple(
    row: CodeGraphEdgeLedgerRow,
    index: int,
    value: str,
) -> CodeGraphEdgeLedgerRow:
    values = list(row)
    values[index] = value
    return cast(CodeGraphEdgeLedgerRow, tuple(values))
