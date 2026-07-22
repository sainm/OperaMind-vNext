"""CLI for validating a worktree or recording a committed Edit Result."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import psycopg

from operamind.application import (
    EditResultRequest,
    EditResultService,
    EditValidationMode,
)
from operamind.contracts import ContractCatalog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate path-only Git changes against a Packet")
    parser.add_argument("--edit-result-id", required=True)
    parser.add_argument("--edit-packet-id", required=True)
    parser.add_argument("--approval-grant-id", required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--analysis-case-id", required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument(
        "--mode",
        required=True,
        choices=[value.value for value in EditValidationMode],
    )
    parser.add_argument("--test-result-ref", action="append", default=[])
    outcome = parser.add_mutually_exclusive_group()
    outcome.add_argument("--tests-passed", action="store_true")
    outcome.add_argument("--tests-failed", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        tests_passed = True if args.tests_passed else False if args.tests_failed else None
        with psycopg.connect(database_url) as connection:
            result = EditResultService(
                connection=connection,
                contracts=ContractCatalog.load(args.root.resolve() / "contracts"),
            ).run(
                EditResultRequest(
                    edit_result_id=args.edit_result_id,
                    edit_packet_id=args.edit_packet_id,
                    approval_grant_id=args.approval_grant_id,
                    project_id=args.project_id,
                    analysis_case_id=args.analysis_case_id,
                    workspace_root=args.workspace_root,
                    mode=EditValidationMode(args.mode),
                    test_result_refs=tuple(args.test_result_ref),
                    tests_passed=tests_passed,
                )
            )
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 1 if result.record.status == "out_of_scope" else 0
    except (OSError, ValueError, json.JSONDecodeError, psycopg.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
