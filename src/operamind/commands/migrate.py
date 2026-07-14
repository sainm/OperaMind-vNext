"""CLI for applying immutable OperaMind PostgreSQL migrations."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

import psycopg

from operamind.infrastructure.postgres.migrations import MigrationCatalog, MigrationRunner


def build_parser() -> argparse.ArgumentParser:
    """Build the migration command parser without accepting credentials as arguments."""

    parser = argparse.ArgumentParser(description="Apply OperaMind PostgreSQL migrations")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Apply pending migrations using OPERAMIND_DATABASE_URL."""

    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required")
        return 2

    catalog = MigrationCatalog.load(args.root.resolve() / "migrations")
    with psycopg.connect(database_url) as connection:
        applied = MigrationRunner(connection, catalog).apply()
    if applied:
        print(f"Applied migrations: {', '.join(applied)}")
    else:
        print("Database schema is up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
