"""CLI entry for append-only ChangeClosureResult evaluation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import psycopg

from operamind.application.change_closure_service import ChangeClosureService
from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres import MigrationCatalog, MigrationRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate code, tests, data, business coverage, and UI closure"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--orchestration-id", required=True)
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
        contracts = ContractCatalog.load(root / "contracts")
        with psycopg.connect(database_url) as connection:
            MigrationRunner(connection, MigrationCatalog.load(root / "migrations")).apply()
            result = ChangeClosureService(connection, contracts).close(
                orchestration_id=args.orchestration_id,
                actor=args.actor,
            )
        print(
            json.dumps(
                {
                    "created": result.record.created,
                    "closure_result": result.artifact,
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
