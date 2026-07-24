"""Build a bounded, source-free Code Graph view for the Web console."""

from __future__ import annotations

from typing import Any


def build_code_graph_view(
    artifact: dict[str, Any],
    *,
    project_id: str,
    max_nodes: int = 240,
    max_edges: int = 480,
) -> dict[str, object]:
    """Return only graph metadata needed by the UI, with strict size bounds."""

    if artifact.get("project_id") != project_id:
        raise ValueError("Code Graph Snapshot does not exist in this project")
    if not 1 <= max_nodes <= 500 or not 1 <= max_edges <= 1000:
        raise ValueError("Code Graph view limits are out of range")

    nodes: list[dict[str, object]] = []
    for file in artifact.get("files", []):
        if not isinstance(file, dict) or len(nodes) >= max_nodes:
            break
        file_id = str(file.get("file_id") or "")
        if not file_id:
            continue
        nodes.append(
            {
                "id": file_id,
                "kind": "file",
                "title": str(file.get("path") or file_id),
                "path": str(file.get("path") or ""),
                "language": str(file.get("language") or ""),
                "role": str(file.get("role") or ""),
            }
        )
        for symbol in file.get("symbols", []):
            if not isinstance(symbol, dict) or len(nodes) >= max_nodes:
                break
            symbol_id = str(symbol.get("symbol_id") or "")
            if not symbol_id:
                continue
            nodes.append(
                {
                    "id": symbol_id,
                    "kind": "symbol",
                    "title": str(symbol.get("signature") or symbol.get("name") or symbol_id),
                    "path": str(file.get("path") or ""),
                    "symbol_type": str(symbol.get("symbol_type") or ""),
                    "start_line": symbol.get("start_line"),
                    "end_line": symbol.get("end_line"),
                }
            )

    visible_ids = {str(node["id"]) for node in nodes}
    edges: list[dict[str, object]] = []
    for edge in artifact.get("edges", []):
        if not isinstance(edge, dict) or len(edges) >= max_edges:
            break
        from_ref = str(edge.get("from_ref") or "")
        to_ref = str(edge.get("to_ref") or "")
        if from_ref not in visible_ids:
            continue
        if to_ref not in visible_ids:
            if len(nodes) >= max_nodes:
                continue
            nodes.append(
                {
                    "id": to_ref,
                    "kind": "external",
                    "title": to_ref,
                    "path": "",
                }
            )
            visible_ids.add(to_ref)
        location = edge.get("source_location")
        edges.append(
            {
                "id": str(edge.get("edge_id") or f"{from_ref}:{to_ref}"),
                "type": str(edge.get("edge_type") or "related"),
                "from": from_ref,
                "to": to_ref,
                "resolution": str(edge.get("resolution_status") or "unknown"),
                "confidence": str(edge.get("confidence") or "unknown"),
                "source_location": location if isinstance(location, dict) else None,
            }
        )

    total_nodes = sum(
        1 + len(file.get("symbols", []))
        for file in artifact.get("files", [])
        if isinstance(file, dict)
    )
    total_edges = len(artifact.get("edges", []))
    return {
        "code_graph_snapshot_id": artifact.get("code_graph_snapshot_id"),
        "project_id": project_id,
        "repository_id": artifact.get("repository_id"),
        "repository_revision": artifact.get("repository_revision"),
        "scan_status": artifact.get("scan_status"),
        "nodes": nodes,
        "edges": edges,
        "summary": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "unresolved_edge_count": sum(
                edge["resolution"] != "resolved" for edge in edges
            ),
            "total_node_count": total_nodes,
            "total_edge_count": total_edges,
            "truncated": len(nodes) < total_nodes or len(edges) < total_edges,
        },
    }
