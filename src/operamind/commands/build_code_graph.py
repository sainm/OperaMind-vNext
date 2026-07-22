"""CLI for scanning and publishing one revision-bound Code Graph Snapshot."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import psycopg

from operamind.application import (
    CodeGraphBuildBlockedError,
    CodeGraphBuildRequest,
    CodeGraphBuildService,
)
from operamind.contracts import ContractCatalog
from operamind.infrastructure.code_graph import WorkspaceScanLimits
from operamind.infrastructure.postgres import PersistenceConflictError
from operamind.profiles import ProfileCatalog


def build_parser() -> argparse.ArgumentParser:
    """Build the command parser; scan roots are always explicit user inputs."""

    parser = argparse.ArgumentParser(
        description="Scan a clean Git revision and publish a normalized Code Graph Snapshot"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="OperaMind root")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--code-graph-snapshot-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--repository-id", required=True)
    parser.add_argument("--repository-revision-id", required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument(
        "--scan-root",
        action="append",
        required=True,
        help="confirmed Profile scan root; repeat for multiple roots",
    )
    parser.add_argument("--profile-version-id", required=True)
    parser.add_argument("--profile-binding-key")
    parser.add_argument("--profile-activation-event-id", required=True)
    parser.add_argument("--activated-by", required=True)
    parser.add_argument("--activation-reason", required=True)
    parser.add_argument("--max-files", type=int, default=100_000)
    parser.add_argument("--max-file-bytes", type=int, default=5 * 1024 * 1024)
    parser.add_argument("--max-total-bytes", type=int, default=512 * 1024 * 1024)
    parser.add_argument(
        "--full-scan",
        action="store_true",
        help="disable Revision incremental reuse for this Snapshot",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Scan using OPERAMIND_DATABASE_URL without printing source content."""

    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        root = args.root.resolve()
        profile = _load_object(_resolve(root, args.profile))
        contracts = ContractCatalog.load(root / "contracts")
        profiles = ProfileCatalog.load(root / "profiles")
        with psycopg.connect(database_url) as connection:
            result = CodeGraphBuildService(
                connection=connection,
                contracts=contracts,
                profiles=profiles,
            ).run(
                CodeGraphBuildRequest(
                    code_graph_snapshot_id=args.code_graph_snapshot_id,
                    project_id=args.project_id,
                    repository_id=args.repository_id,
                    repository_revision_id=args.repository_revision_id,
                    workspace_root=args.workspace_root,
                    scan_roots=tuple(args.scan_root),
                    profile_version_id=args.profile_version_id,
                    profile_binding_key=(
                        args.profile_binding_key or f"code-framework:{args.repository_id}"
                    ),
                    profile_activation_event_id=args.profile_activation_event_id,
                    activated_by=args.activated_by,
                    activation_reason=args.activation_reason,
                    limits=WorkspaceScanLimits(
                        max_files=args.max_files,
                        max_file_bytes=args.max_file_bytes,
                        max_total_bytes=args.max_total_bytes,
                    ),
                    incremental=not args.full_scan,
                ),
                profile=profile,
            )
        publication = result.publication
        print(
            json.dumps(
                {
                    "created": publication.created,
                    "code_graph_snapshot_id": publication.code_graph_snapshot_id,
                    "status": publication.status,
                    "is_current": publication.is_current,
                    "file_count": publication.file_count,
                    "symbol_count": publication.symbol_count,
                    "edge_count": publication.edge_count,
                    "unresolved_edge_count": publication.unresolved_edge_count,
                    "test_binding_count": publication.test_binding_count,
                    "scan_mode": result.scan.artifact.get("scan_mode", "full"),
                    "base_code_graph_snapshot_id": result.scan.artifact.get(
                        "base_code_graph_snapshot_id"
                    ),
                    "changed_paths": result.scan.artifact.get("changed_paths", []),
                    "affected_paths": result.scan.artifact.get("affected_paths", []),
                    "scanned_file_count": result.scan.artifact.get(
                        "scanned_file_count", publication.file_count
                    ),
                    "reused_file_count": result.scan.artifact.get("reused_file_count", 0),
                    "framework_markers_found": list(result.scan.framework_markers_found),
                    "diagnostics": list(result.scan.diagnostics),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (
        CodeGraphBuildBlockedError,
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
