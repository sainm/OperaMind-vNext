"""CLI for the configuration-driven dual-entry change loop."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from operamind.application import (
    ChangeInputMode,
    ChangeLoopPlanner,
    ChangeLoopPlanRequest,
)
from operamind.application.change_loop_case import ChangeLoopCase


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan a configured dual-entry change candidate")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="entry", required=True)
    for entry in ("documents", "requirement", "hybrid"):
        command = subparsers.add_parser(entry)
        _add_common_arguments(command)
        if entry in {"documents", "hybrid"}:
            command.add_argument("--after-document", type=Path, required=True)
        if entry in {"requirement", "hybrid"}:
            command.add_argument("--requirement", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = args.root.resolve(strict=True)
        output = _resolve(root, args.output)
        mode = ChangeInputMode(args.entry if args.entry != "requirement" else "natural_language")
        before_document = _resolve(root, args.before_document)
        proposal = (
            output / "document-proposal" / before_document.name
            if mode is ChangeInputMode.NATURAL_LANGUAGE
            else None
        )
        case_root = _resolve(root, args.case_root)
        configured_case = ChangeLoopCase.load(case_root)
        plan_request = ChangeLoopPlanRequest(
            change_request_id=args.change_request_id,
            project_id=args.project_id or configured_case.project_id,
            case_root=case_root,
            workspace_root=_resolve(root, args.workspace),
            before_document=before_document,
            input_mode=mode,
            after_document=(
                _resolve(root, args.after_document) if hasattr(args, "after_document") else None
            ),
            requirement_text=args.requirement if hasattr(args, "requirement") else None,
            proposal_document=proposal,
        )
        plan = ChangeLoopPlanner(repository_root=root).plan(plan_request)
        paths = plan.write_artifacts(output / "artifacts")
        print(
            json.dumps(
                {
                    "status": "planned",
                    "input_mode": mode.value,
                    "artifact_count": len(paths),
                    "output": str(output),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--case-root", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--before-document", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--change-request-id", default="configured-change-loop")
    parser.add_argument("--project-id")


def _resolve(root: Path, value: Path) -> Path:
    return value.resolve() if value.is_absolute() else (root / value).resolve()


if __name__ == "__main__":
    raise SystemExit(main())
