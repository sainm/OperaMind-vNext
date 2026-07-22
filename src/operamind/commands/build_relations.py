"""CLI for publishing one current Profile-derived Document Relation Build."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import psycopg

from operamind.application import DocumentRelationBuildRequest, DocumentRelationBuildService
from operamind.infrastructure.postgres import PersistenceConflictError
from operamind.profiles import ProfileCatalog


def build_parser() -> argparse.ArgumentParser:
    """Build the relation command parser without accepting DB credentials."""

    parser = argparse.ArgumentParser(
        description="Publish exact Profile-derived relations for one committed Snapshot"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--profile-version-id", required=True)
    parser.add_argument("--profile-binding-key", default="relation:document_graph")
    parser.add_argument("--profile-activation-event-id", required=True)
    parser.add_argument("--activated-by", required=True)
    parser.add_argument("--activation-reason", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Build relations using OPERAMIND_DATABASE_URL and no network dependency."""

    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        root = args.root.resolve()
        profile = _load_object(_resolve(root, args.profile))
        with psycopg.connect(database_url) as connection:
            result = DocumentRelationBuildService(
                connection=connection,
                profiles=ProfileCatalog.load(root / "profiles"),
            ).run(
                DocumentRelationBuildRequest(
                    build_id=args.build_id,
                    project_id=args.project_id,
                    snapshot_id=args.snapshot_id,
                    profile_version_id=args.profile_version_id,
                    profile_binding_key=args.profile_binding_key,
                    profile_activation_event_id=args.profile_activation_event_id,
                    activated_by=args.activated_by,
                    activation_reason=args.activation_reason,
                ),
                profile=profile,
            )
        state = result.publication.state
        print(
            json.dumps(
                {
                    "created": result.publication.created,
                    "document_relation_build_id": state.spec.build_id,
                    "project_id": state.spec.project_id,
                    "document_snapshot_id": state.spec.snapshot_id,
                    "relation_profile_version_id": state.spec.profile_version_id,
                    "status": state.status.value,
                    "relation_count": state.relation_count,
                    "unresolved_count": state.unresolved_count,
                    "is_current": state.is_current,
                    "completed_at": state.completed_at.isoformat(),
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
