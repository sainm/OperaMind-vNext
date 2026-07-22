"""Start the local OperaMind Web control plane."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import psycopg
import uvicorn

from operamind.application.orchestration_task import (
    ORCHESTRATION_MAX_ACTIVE_TASKS_ENV,
    parse_orchestration_scheduling_policy,
)
from operamind.infrastructure.postgres import MigrationCatalog, MigrationRunner
from operamind.web import create_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the OperaMind Web control plane")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
        return 2
    bridge_token = os.getenv("OPERAMIND_BRIDGE_TOKEN")
    if bridge_token is not None and not bridge_token.strip():
        print("error: OPERAMIND_BRIDGE_TOKEN must not be blank", file=sys.stderr)
        return 2
    if bridge_token and args.host not in {"127.0.0.1", "::1", "localhost"}:
        print("error: local Bridge requires a loopback Web host", file=sys.stderr)
        return 2
    try:
        scheduling_policy = parse_orchestration_scheduling_policy(
            os.getenv(ORCHESTRATION_MAX_ACTIVE_TASKS_ENV)
        )
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    root = args.root.resolve()
    try:
        with psycopg.connect(database_url) as connection:
            MigrationRunner(connection, MigrationCatalog.load(root / "migrations")).apply()
    except (OSError, ValueError, psycopg.Error) as error:
        print(f"error: failed to prepare Web database: {error}", file=sys.stderr)
        return 1
    app = create_app(
        repository_root=root,
        database_url=database_url,
        bridge_token=bridge_token,
        orchestration_scheduling_policy=scheduling_policy,
    )
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
