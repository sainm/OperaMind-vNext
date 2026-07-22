"""CLI for rebuilding and inspecting immutable Unresolved Evidence reports."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import psycopg

from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres import (
    CodeGraphSnapshotRepository,
    PersistenceConflictError,
    UnresolvedEvidenceRepository,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Recompute or inspect immutable Unresolved Evidence reports"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="OperaMind root")
    subparsers = parser.add_subparsers(dest="command", required=True)
    rebuild = subparsers.add_parser(
        "recompute", help="ensure the deterministic report for one Code Graph"
    )
    rebuild.add_argument("--code-graph-snapshot-id", required=True)
    show = subparsers.add_parser("show", help="show current reports and bounded history")
    show.add_argument("--project-id", required=True)
    show.add_argument("--history-limit", type=int, default=50)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        root = args.root.resolve()
        contracts = ContractCatalog.load(root / "contracts")
        with psycopg.connect(database_url) as connection:
            reports = UnresolvedEvidenceRepository(connection, contracts)
            if args.command == "recompute":
                graph = CodeGraphSnapshotRepository(connection, contracts).get(
                    args.code_graph_snapshot_id
                )
                if graph is None:
                    raise ValueError("Code Graph Snapshot does not exist")
                result = reports.ensure_for_graph(graph)
                payload: dict[str, object] = {
                    "unresolved_evidence_report_id": result.unresolved_evidence_report_id,
                    "code_graph_snapshot_id": result.code_graph_snapshot_id,
                    "created": result.created,
                    "open_count": result.open_count,
                    "closed_count": result.closed_count,
                }
            else:
                payload = reports.management_view(
                    project_id=args.project_id,
                    history_limit=args.history_limit,
                )
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0
    except (
        OSError,
        ValueError,
        PersistenceConflictError,
        psycopg.Error,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
