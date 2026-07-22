"""CLI for explicitly closing an interrupted approved command reservation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import psycopg

from operamind.application import (
    CommandExecutionRecoveryRequest,
    CommandExecutionRecoveryService,
)
from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres import PersistenceConflictError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mark one stale approved command reservation as interrupted"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--recovery-id", required=True)
    parser.add_argument("--command-execution-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--stale-before", type=_timestamp, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        root = args.root.resolve()
        with psycopg.connect(database_url) as connection:
            record = CommandExecutionRecoveryService(
                connection=connection,
                contracts=ContractCatalog.load(root / "contracts"),
            ).run(
                CommandExecutionRecoveryRequest(
                    recovery_id=args.recovery_id,
                    command_execution_id=args.command_execution_id,
                    project_id=args.project_id,
                    actor=args.actor,
                    reason=args.reason,
                    stale_before=args.stale_before,
                )
            )
        print(
            json.dumps(
                {
                    "command_execution_id": record.command_execution_id,
                    "created": record.created,
                    "status": record.status,
                    "result_started_at": record.started_at.isoformat(),
                    "result_completed_at": record.completed_at.isoformat(),
                    "recovery_id": record.recovery_id,
                    "recovery_actor": record.recovery_actor,
                    "recovery_reason": record.recovery_reason,
                    "recovery_stale_before": (
                        record.recovery_stale_before.isoformat()
                        if record.recovery_stale_before is not None
                        else None
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (OSError, ValueError, PersistenceConflictError, psycopg.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
