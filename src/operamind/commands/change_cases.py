"""CLI for initializing, validating and batch-running change-loop cases."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from operamind.application.change_loop import ChangeInputMode
from operamind.application.change_loop_batch import (
    ChangeLoopBatchRequest,
    ChangeLoopBatchRunner,
)
from operamind.application.change_loop_catalog import (
    ChangeLoopCaseCatalog,
    initialize_case,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Initialize, validate, plan or run configured change-loop cases"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Clone an existing complete case as a draft")
    init.add_argument("--from-case", type=Path, required=True)
    init.add_argument("--case-root", type=Path, required=True)
    init.add_argument("--case-id", required=True)
    init.add_argument("--project-id")

    validate = subparsers.add_parser("validate", help="Validate all case references")
    _add_catalog_arguments(validate, require_after_root=True)

    plan = subparsers.add_parser("plan", help="Generate plans in isolated worktrees")
    _add_catalog_arguments(plan, require_after_root=False)
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--entry", choices=("documents", "requirement"), required=True)
    plan.add_argument("--keep-workspaces", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = args.root.resolve(strict=True)
        if args.command == "init":
            case = initialize_case(
                source_case_root=_resolve(root, args.from_case),
                target_case_root=_resolve(root, args.case_root),
                case_id=args.case_id,
                project_id=args.project_id,
            )
            print(
                json.dumps(
                    {
                        "status": "draft_created",
                        "case_id": case.case_id,
                        "case_root": str(case.root),
                        "next": (
                            "Customize the cloned files, validate references, then approve "
                            "review_status"
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        cases_root = _resolve(root, args.cases_root)
        target_repository = _resolve(root, args.target_repository)
        before_roots = tuple(_resolve(root, value) for value in args.before_root)
        after_roots = tuple(_resolve(root, value) for value in args.after_root)
        selected = frozenset(args.case_id)
        require_after = args.command == "validate" or args.entry == "documents"
        if require_after and not after_roots:
            raise ValueError("documents entry and validate require at least one --after-root")
        catalog = ChangeLoopCaseCatalog(repository_root=root, cases_root=cases_root)
        cases = catalog.discover(
            before_roots=before_roots,
            after_roots=after_roots,
            target_repository=target_repository,
            case_ids=selected,
            require_after=require_after,
        )
        if args.command == "validate":
            payload = {
                "status": "ready" if cases and all(case.ready for case in cases) else "invalid",
                "case_count": len(cases),
                "ready_count": sum(case.ready for case in cases),
                "cases": [case.to_dict() for case in cases],
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0 if payload["status"] == "ready" else 1

        mode = (
            ChangeInputMode.DOCUMENTS
            if args.entry == "documents"
            else ChangeInputMode.NATURAL_LANGUAGE
        )
        result = ChangeLoopBatchRunner(repository_root=root).run(
            cases,
            ChangeLoopBatchRequest(
                target_repository=target_repository,
                output_root=_resolve(root, args.output),
                input_mode=mode,
                keep_workspaces=bool(args.keep_workspaces),
            ),
        )
        print(
            json.dumps(
                {
                    "status": result.report["status"],
                    "case_count": result.report["case_count"],
                    "summary": result.report["summary"],
                    "report": str(result.report_path),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if result.successful else 1
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _add_catalog_arguments(parser: argparse.ArgumentParser, *, require_after_root: bool) -> None:
    parser.add_argument("--cases-root", type=Path, default=Path("golden-dataset/cases"))
    parser.add_argument("--target-repository", type=Path, required=True)
    parser.add_argument("--before-root", type=Path, action="append", required=True)
    parser.add_argument(
        "--after-root", type=Path, action="append", required=require_after_root, default=[]
    )
    parser.add_argument("--case-id", action="append", default=[])


def _resolve(root: Path, value: Path) -> Path:
    return value.expanduser().resolve() if value.is_absolute() else (root / value).resolve()


if __name__ == "__main__":
    raise SystemExit(main())
