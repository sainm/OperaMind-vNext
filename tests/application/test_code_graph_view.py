from __future__ import annotations

import pytest

from operamind.application.code_graph_view import build_code_graph_view


def test_code_graph_view_is_project_scoped_source_free_and_bounded() -> None:
    artifact = {
        "code_graph_snapshot_id": "graph-1",
        "project_id": "project-1",
        "repository_id": "repository-1",
        "repository_revision": "abc123",
        "scan_status": "truncated",
        "files": [
            {
                "file_id": "file-1",
                "path": "src/Service.java",
                "language": "java",
                "role": "production",
                "content_hash": "secret-hash",
                "symbols": [
                    {
                        "symbol_id": "symbol-1",
                        "symbol_type": "method",
                        "name": "search",
                        "signature": "search(String status)",
                        "start_line": 10,
                        "end_line": 20,
                    }
                ],
            }
        ],
        "edges": [
            {
                "edge_id": "edge-1",
                "edge_type": "calls",
                "from_ref": "symbol-1",
                "to_ref": "unresolved:repository",
                "resolution_status": "unresolved",
                "confidence": "low",
            }
        ],
    }

    view = build_code_graph_view(
        artifact, project_id="project-1", max_nodes=3, max_edges=2
    )

    assert [node["kind"] for node in view["nodes"]] == ["file", "symbol", "external"]
    assert view["edges"][0]["resolution"] == "unresolved"
    assert view["summary"]["unresolved_edge_count"] == 1
    assert "content_hash" not in view["nodes"][0]


def test_code_graph_view_rejects_cross_project_access() -> None:
    with pytest.raises(ValueError, match="does not exist"):
        build_code_graph_view(
            {"project_id": "another-project", "files": [], "edges": []},
            project_id="project-1",
        )
