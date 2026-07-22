"""CLI for local VS Code GitHub Copilot quota pause/resume checkpoints."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from operamind.application.copilot_checkpoint import (
    CopilotCheckpointRequest,
    CopilotCheckpointService,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage a local Copilot work checkpoint")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start")
    start.add_argument("--checkpoint-root", type=Path, required=True)
    start.add_argument("--session-id", required=True)
    start.add_argument("--phase", choices=("draft_generation", "code_edit"), required=True)
    start.add_argument("--project-id", required=True)
    start.add_argument("--analysis-case-id", required=True)
    start.add_argument("--registered-repository", type=Path, required=True)
    start.add_argument("--workspace", type=Path, required=True)
    start.add_argument("--base-revision", required=True)
    start.add_argument("--expected-output", action="append", required=True)
    start.add_argument("--approval-grant-id")
    start.add_argument("--edit-packet-id")
    for command in ("status", "resume"):
        child = commands.add_parser(command)
        child.add_argument("--checkpoint-root", type=Path, required=True)
    pause = commands.add_parser("pause")
    pause.add_argument("--checkpoint-root", type=Path, required=True)
    pause.add_argument(
        "--reason",
        choices=("free_quota_exhausted", "model_capacity", "user_requested"),
        required=True,
    )
    rebind = commands.add_parser("rebind-grant")
    rebind.add_argument("--checkpoint-root", type=Path, required=True)
    rebind.add_argument("--expected-previous-grant-id", required=True)
    rebind.add_argument("--approval-grant-id", required=True)
    attach_rehearsal = commands.add_parser(
        "attach-rehearsal",
        help="Attach a non-executable Codex implementation proposal",
    )
    attach_rehearsal.add_argument("--checkpoint-root", type=Path, required=True)
    attach_rehearsal.add_argument("--proposal-file", type=Path, required=True)
    show_rehearsal = commands.add_parser(
        "show-rehearsal",
        help="Revalidate and show the attached Codex implementation proposal",
    )
    show_rehearsal.add_argument("--checkpoint-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = CopilotCheckpointService()
    try:
        if args.command == "start":
            payload = service.initialize(
                checkpoint_root=args.checkpoint_root,
                request=CopilotCheckpointRequest(
                    session_id=args.session_id,
                    phase=args.phase,
                    project_id=args.project_id,
                    analysis_case_id=args.analysis_case_id,
                    registered_repository_root=args.registered_repository,
                    workspace_root=args.workspace,
                    base_revision=args.base_revision,
                    expected_outputs=tuple(args.expected_output),
                    approval_grant_id=args.approval_grant_id,
                    edit_packet_id=args.edit_packet_id,
                ),
            )
        elif args.command == "pause":
            payload = service.pause(
                checkpoint_root=args.checkpoint_root,
                reason=args.reason,
            )
        elif args.command == "resume":
            payload = service.resume(checkpoint_root=args.checkpoint_root)
        elif args.command == "rebind-grant":
            payload = service.rebind_grant(
                checkpoint_root=args.checkpoint_root,
                expected_previous_grant_id=args.expected_previous_grant_id,
                approval_grant_id=args.approval_grant_id,
            )
        elif args.command == "attach-rehearsal":
            payload = service.attach_rehearsal(
                checkpoint_root=args.checkpoint_root,
                proposal_file=args.proposal_file,
            )
        elif args.command == "show-rehearsal":
            payload = service.load_rehearsal(args.checkpoint_root)
        else:
            payload = service.load(args.checkpoint_root)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
