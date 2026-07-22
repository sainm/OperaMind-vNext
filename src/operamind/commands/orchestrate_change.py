"""CLI entry for the Canonical Change Request orchestration pipeline."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import psycopg

from operamind.application.change_orchestration_service import ChangeOrchestrationService
from operamind.infrastructure.postgres import MigrationCatalog, MigrationRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate code scope, tests, data, coverage, acceptance, and UI scenarios"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--change-request-id", required=True)
    parser.add_argument("--actor", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
        return 2
    root = args.root.resolve()
    try:
        with psycopg.connect(database_url) as connection:
            MigrationRunner(connection, MigrationCatalog.load(root / "migrations")).apply()
            result = ChangeOrchestrationService(
                connection=connection, repository_root=root
            ).orchestrate(
                change_request_id=args.change_request_id,
                actor=args.actor,
            )
        print(
            json.dumps(
                {
                    "created": result.created,
                    "orchestration": result.orchestration,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError, psycopg.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
