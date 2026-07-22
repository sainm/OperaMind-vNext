"""CLI for registering the P0 identity chain from clean Git evidence."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import psycopg

from operamind.application import AnalysisStartRequest, AnalysisStartService
from operamind.infrastructure.postgres import AnalysisRepository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start one Canonical Analysis Case from a clean repository"
    )
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--repository-id", required=True)
    parser.add_argument("--repository-revision-id", required=True)
    parser.add_argument("--analysis-case-id", required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--base-revision", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(database_url) as connection:
            result = AnalysisStartService(
                repository=AnalysisRepository(connection),
            ).run(
                AnalysisStartRequest(
                    project_id=args.project_id,
                    project_name=args.project_name,
                    repository_id=args.repository_id,
                    repository_revision_id=args.repository_revision_id,
                    analysis_case_id=args.analysis_case_id,
                    workspace_root=args.workspace_root,
                    expected_base_revision=args.base_revision,
                )
            )
        print(
            json.dumps(
                {
                    "project_id": result.project_id,
                    "repository_id": result.repository_id,
                    "repository_revision_id": result.repository_revision_id,
                    "analysis_case_id": result.analysis_case_id,
                    "status": result.status,
                    "created": result.created,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (OSError, RuntimeError, ValueError, psycopg.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
