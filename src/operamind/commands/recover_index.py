"""CLI for explicitly closing an interrupted Search Index build."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import datetime

import psycopg

from operamind.application import SearchIndexRecoveryRequest, SearchIndexRecoveryService
from operamind.infrastructure.postgres import PersistenceConflictError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mark one stale interrupted Search Index build as failed"
    )
    parser.add_argument("--recovery-id", required=True)
    parser.add_argument("--build-id", required=True)
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
        with psycopg.connect(database_url) as connection:
            state = SearchIndexRecoveryService(connection).run(
                SearchIndexRecoveryRequest(
                    recovery_id=args.recovery_id,
                    build_id=args.build_id,
                    actor=args.actor,
                    reason=args.reason,
                    stale_before=args.stale_before,
                )
            )
        print(
            json.dumps(
                {
                    "search_index_build_id": state.spec.build_id,
                    "status": state.status.value,
                    "failure_event_id": state.failure_event_id,
                    "failure_kind": (
                        state.failure_kind.value if state.failure_kind is not None else None
                    ),
                    "failure_actor": state.failure_actor,
                    "failure_reason": state.failure_reason,
                    "failure_stale_before": (
                        state.failure_stale_before.isoformat()
                        if state.failure_stale_before is not None
                        else None
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (ValueError, PersistenceConflictError, psycopg.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
