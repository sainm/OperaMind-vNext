"""CLI for appending one human StructuredChange review decision."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence

import psycopg

from operamind.infrastructure.postgres import (
    PersistenceConflictError,
    StructuredChangeReviewDecision,
    StructuredChangeReviewRepository,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the review parser without accepting database credentials."""

    parser = argparse.ArgumentParser(
        description="Append an accepted or rejected StructuredChange review event"
    )
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--change-id", required=True)
    parser.add_argument("--review-event-id", required=True)
    parser.add_argument(
        "--decision",
        required=True,
        choices=[decision.value for decision in StructuredChangeReviewDecision],
    )
    parser.add_argument("--reviewed-by", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument(
        "--expected-previous-review-event-id",
        help="required when replacing an earlier accepted/rejected decision",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Append one review event using OPERAMIND_DATABASE_URL."""

    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
        return 2

    try:
        with psycopg.connect(database_url) as connection:
            repository = StructuredChangeReviewRepository(connection)
            created = repository.review(
                review_event_id=args.review_event_id,
                project_id=args.project_id,
                change_id=args.change_id,
                decision=StructuredChangeReviewDecision(args.decision),
                reviewed_by=args.reviewed_by,
                reason=args.reason,
                expected_previous_review_event_id=args.expected_previous_review_event_id,
            )
            state = repository.get_state(
                project_id=args.project_id,
                change_id=args.change_id,
            )
            if state is None:
                raise ValueError("Reviewed StructuredChange disappeared")
        print(
            json.dumps(
                {
                    "created": created,
                    "project_id": state.project_id,
                    "change_id": state.change_id,
                    "review_status": state.status.value,
                    "review_event_id": state.review_event_id,
                    "reviewed_by": state.reviewed_by,
                    "reason": state.reason,
                    "reviewed_at": (
                        state.reviewed_at.isoformat() if state.reviewed_at is not None else None
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


if __name__ == "__main__":
    raise SystemExit(main())
