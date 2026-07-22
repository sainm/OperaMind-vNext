"""CLI for the same resumable Change Automation used by the Web control plane."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import psycopg

from operamind.application.orchestration_task import (
    ORCHESTRATION_MAX_ACTIVE_TASKS_ENV,
    parse_orchestration_scheduling_policy,
)
from operamind.application.web_control_plane import WebControlPlaneService
from operamind.infrastructure.postgres import MigrationCatalog, MigrationRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start, inspect, or resume one Canonical change automation Run"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command", required=True)
    start = subparsers.add_parser("start")
    start.add_argument("--change-request-id", required=True)
    start.add_argument("--idempotency-key", required=True)
    start.add_argument("--actor", required=True)
    status = subparsers.add_parser("status")
    status.add_argument("--change-request-id", required=True)
    bind = subparsers.add_parser("bind-case")
    bind.add_argument("--change-request-id", required=True)
    bind.add_argument("--project-id", required=True)
    bind.add_argument("--case-id", required=True)
    bind.add_argument("--idempotency-key", required=True)
    bind.add_argument("--actor", required=True)
    resume = subparsers.add_parser("resume")
    resume.add_argument("--change-request-id", required=True)
    resume.add_argument("--run-id", required=True)
    resume.add_argument("--actor", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
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
        with psycopg.connect(database_url, autocommit=True) as connection:
            MigrationRunner(connection, MigrationCatalog.load(root / "migrations")).apply()
            service = WebControlPlaneService(
                connection=connection,
                repository_root=root,
                orchestration_scheduling_policy=scheduling_policy,
            )
            if args.command == "start":
                result = service.start_change_automation(
                    request_id=args.change_request_id,
                    idempotency_key=args.idempotency_key,
                    actor=args.actor,
                )
            elif args.command == "resume":
                result = service.resume_change_automation(
                    request_id=args.change_request_id,
                    run_id=args.run_id,
                    actor=args.actor,
                )
            elif args.command == "bind-case":
                result = service.bind_change_request_case(
                    request_id=args.change_request_id,
                    project_id=args.project_id,
                    case_id=args.case_id,
                    idempotency_key=args.idempotency_key,
                    actor=args.actor,
                )
            else:
                result = service.change_automation(args.change_request_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError, psycopg.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
