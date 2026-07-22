"""CLI for issuing, inspecting, and revoking bounded Approval Grants."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import psycopg

from operamind.application import ApprovalGrantRequest, ApprovalGrantService
from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres import ApprovalGrantRepository
from operamind.profiles import ProfileCatalog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage bounded Approval Grants")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="operation", required=True)

    issue = subparsers.add_parser("issue")
    issue.add_argument("--grant-id", required=True)
    issue.add_argument("--project-id", required=True)
    issue.add_argument("--analysis-case-id", required=True)
    issue.add_argument("--edit-packet-id", required=True)
    issue.add_argument("--approved-by", required=True)
    issue.add_argument("--expires-at", required=True)
    issue.add_argument("--command-profile-binding-key", required=True)
    issue.add_argument("--test-command-ref", action="append", default=[])

    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("--grant-id", required=True)

    revoke = subparsers.add_parser("revoke")
    revoke.add_argument("--event-id", required=True)
    revoke.add_argument("--grant-id", required=True)
    revoke.add_argument("--project-id", required=True)
    revoke.add_argument("--revoked-by", required=True)
    revoke.add_argument("--reason", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        root = args.root.resolve()
        contracts = ContractCatalog.load(root / "contracts")
        profiles = ProfileCatalog.load(root / "profiles")
        with psycopg.connect(database_url) as connection:
            repository = ApprovalGrantRepository(connection, contracts)
            if args.operation == "issue":
                result = ApprovalGrantService(
                    connection=connection,
                    contracts=contracts,
                    profiles=profiles,
                ).issue(
                    ApprovalGrantRequest(
                        grant_id=args.grant_id,
                        project_id=args.project_id,
                        analysis_case_id=args.analysis_case_id,
                        edit_packet_id=args.edit_packet_id,
                        approved_by=args.approved_by,
                        expires_at=_timestamp(args.expires_at),
                        command_profile_binding_key=args.command_profile_binding_key,
                        allowed_test_command_refs=tuple(args.test_command_ref),
                    )
                )
                output: object = {
                    "created": result.record.created,
                    "state": result.record.state,
                    "artifact": result.artifact,
                }
            elif args.operation == "inspect":
                grant = repository.inspect(args.grant_id)
                output = {
                    "grant_id": grant.grant_id,
                    "project_id": grant.project_id,
                    "analysis_case_id": grant.analysis_case_id,
                    "edit_packet_id": grant.edit_packet_id,
                    "base_repository_revision": grant.base_repository_revision,
                    "allowed_actions": list(grant.allowed_actions),
                    "command_profile_version_id": grant.command_profile_version_id,
                    "allowed_test_command_refs": list(grant.allowed_test_command_refs),
                    "allowed_ui_scenarios": list(grant.allowed_ui_scenarios),
                    "expires_at": grant.expires_at.isoformat(),
                    "state": grant.state,
                }
            else:
                created = ApprovalGrantService(
                    connection=connection,
                    contracts=contracts,
                    profiles=profiles,
                ).revoke(
                    event_id=args.event_id,
                    grant_id=args.grant_id,
                    project_id=args.project_id,
                    revoked_by=args.revoked_by,
                    reason=args.reason,
                )
                output = {"event_id": args.event_id, "created": created, "state": "revoked"}
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, psycopg.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("expires-at must include a timezone")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
