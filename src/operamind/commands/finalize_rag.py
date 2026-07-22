"""CLI for appending effective RAG readiness evidence."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import psycopg

from operamind.application import RagReadinessRequest, RagReadinessService
from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres import PersistenceConflictError
from operamind.profiles import ProfileCatalog


def build_parser() -> argparse.ArgumentParser:
    """Build the finalization parser without accepting database credentials."""

    parser = argparse.ArgumentParser(
        description="Append RAG readiness evidence and advance the Analysis Case"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--ingestion-batch-id", required=True)
    parser.add_argument("--analysis-case-id", required=True)
    parser.add_argument("--expected-previous-event-id", required=True)
    parser.add_argument("--search-index-build-id", required=True)
    parser.add_argument(
        "--embedding-profile-binding-key",
        default="embedding:document_search",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Finalize one ingestion batch using OPERAMIND_DATABASE_URL."""

    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
        return 2

    try:
        root = args.root.resolve()
        with psycopg.connect(database_url) as connection:
            result = RagReadinessService(
                connection=connection,
                contracts=ContractCatalog.load(root / "contracts"),
                profiles=ProfileCatalog.load(root / "profiles"),
            ).run(
                RagReadinessRequest(
                    event_id=args.event_id,
                    project_id=args.project_id,
                    ingestion_batch_id=args.ingestion_batch_id,
                    analysis_case_id=args.analysis_case_id,
                    expected_previous_event_id=args.expected_previous_event_id,
                    search_index_build_id=args.search_index_build_id,
                    embedding_profile_binding_key=args.embedding_profile_binding_key,
                )
            )
        print(
            json.dumps(
                {
                    "created": result.created,
                    "ingestion_result_event_id": result.event.event_id,
                    "previous_event_id": result.event.previous_event_id,
                    "artifact_id": result.event.artifact_id,
                    "search_index_build_id": result.event.search_index_build_id,
                    "ingestion_status": result.event.status.value,
                    "analysis_case_status": result.analysis_case_status,
                    "created_at": result.event.created_at.isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (OSError, ValueError, PersistenceConflictError, psycopg.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
