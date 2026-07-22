"""CLI for publishing browser Route evidence and a runtime-enriched Code Graph."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg

from operamind.application import RuntimeRouteReconciler, RuntimeRouteReconcileRequest
from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres import (
    CodeGraphSnapshotRepository,
    PersistenceConflictError,
    RuntimeRouteEvidenceRepository,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Merge sanitized browser Route observations into one static Code Graph"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="OperaMind root")
    parser.add_argument("--input", type=Path, required=True, help="sanitized network summary JSON")
    parser.add_argument("--code-graph-snapshot-id", required=True)
    parser.add_argument("--runtime-route-evidence-id", required=True)
    parser.add_argument("--merged-code-graph-snapshot-id", required=True)
    parser.add_argument("--browser-run-id", required=True)
    parser.add_argument("--captured-at", required=True, help="ISO-8601 timestamp with timezone")
    parser.add_argument("--source-evidence-ref", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        root = args.root.resolve()
        capture = _load_object(_resolve(root, args.input))
        contracts = ContractCatalog.load(root / "contracts")
        captured_at = datetime.fromisoformat(str(args.captured_at).replace("Z", "+00:00"))
        with psycopg.connect(database_url) as connection:
            graphs = CodeGraphSnapshotRepository(connection, contracts)
            base = graphs.get(args.code_graph_snapshot_id)
            binding = graphs.get_publication_binding(args.code_graph_snapshot_id)
            if base is None or binding is None:
                raise ValueError("Base Code Graph Snapshot does not exist")
            result = RuntimeRouteReconciler(contracts).reconcile(
                request=RuntimeRouteReconcileRequest(
                    runtime_route_evidence_id=args.runtime_route_evidence_id,
                    merged_code_graph_snapshot_id=args.merged_code_graph_snapshot_id,
                    browser_run_id=args.browser_run_id,
                    captured_at=captured_at,
                    source_evidence_ref=args.source_evidence_ref,
                ),
                base_graph=base,
                capture=capture,
            )
            with connection.transaction():
                evidence = RuntimeRouteEvidenceRepository(connection, contracts).publish(
                    result.evidence_artifact
                )
                graph = graphs.publish(
                    artifact=result.graph_artifact,
                    repository_revision_id=binding[0],
                    profile_version_ids=binding[1],
                )
        print(
            json.dumps(
                {
                    "runtime_route_evidence_id": evidence.runtime_route_evidence_id,
                    "evidence_created": evidence.created,
                    "code_graph_snapshot_id": graph.code_graph_snapshot_id,
                    "graph_created": graph.created,
                    "scan_mode": graph.scan_mode,
                    "observation_count": result.observation_count,
                    "resolved_count": result.resolved_count,
                    "unresolved_count": result.unresolved_count,
                    "remaining_unresolved_edge_count": graph.unresolved_edge_count,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        PersistenceConflictError,
        psycopg.Error,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _resolve(root: Path, path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _load_object(path: Path) -> dict[str, Any]:
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return raw


if __name__ == "__main__":
    raise SystemExit(main())
