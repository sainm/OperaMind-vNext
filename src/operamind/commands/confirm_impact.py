"""CLI for appending one complete human Impact Confirmation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres import ImpactRepository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Confirm every item in one Impact Report")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--confirmation-id", required=True)
    parser.add_argument("--impact-report-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--analysis-case-id", required=True)
    parser.add_argument("--confirmed-by", required=True)
    parser.add_argument("--approved-item-id", action="append", default=[])
    parser.add_argument("--rejected-item-id", action="append", default=[])
    parser.add_argument("--user-note", default="")
    parser.add_argument("--confirmed-at")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        root = args.root.resolve()
        confirmed_at = args.confirmed_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
        artifact = {
            "artifact_type": "ImpactConfirmation",
            "schema_version": "v1",
            "confirmation_id": args.confirmation_id,
            "impact_report_id": args.impact_report_id,
            "confirmed_by": args.confirmed_by,
            "approved_item_ids": args.approved_item_id,
            "rejected_item_ids": args.rejected_item_id,
            "user_note": args.user_note,
            "confirmed_at": confirmed_at,
        }
        with psycopg.connect(database_url) as connection:
            result = ImpactRepository(
                connection,
                ContractCatalog.load(root / "contracts"),
            ).confirm(
                project_id=args.project_id,
                analysis_case_id=args.analysis_case_id,
                artifact=artifact,
            )
        print(
            json.dumps(
                {
                    "confirmation_id": result.confirmation_id,
                    "impact_report_id": result.impact_report_id,
                    "report_status": result.report_status,
                    "created": result.created,
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
