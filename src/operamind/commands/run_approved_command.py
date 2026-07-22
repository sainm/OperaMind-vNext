"""CLI for one Approval Grant-bound, Profile-defined local command."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import psycopg

from operamind.application import ApprovedCommandRequest, ApprovedCommandService
from operamind.contracts import ContractCatalog
from operamind.profiles import ProfileCatalog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one approved command without a shell")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--command-execution-id", required=True)
    parser.add_argument("--approval-grant-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--analysis-case-id", required=True)
    parser.add_argument("--edit-packet-id", required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--command-ref", required=True)
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
            result = ApprovedCommandService(
                connection=connection,
                contracts=ContractCatalog.load(root / "contracts"),
                profiles=ProfileCatalog.load(root / "profiles"),
            ).run(
                ApprovedCommandRequest(
                    command_execution_id=args.command_execution_id,
                    approval_grant_id=args.approval_grant_id,
                    project_id=args.project_id,
                    analysis_case_id=args.analysis_case_id,
                    edit_packet_id=args.edit_packet_id,
                    workspace_root=args.workspace_root,
                    command_ref=args.command_ref,
                )
            )
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0 if result.record.status == "passed" else 1
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError, psycopg.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
