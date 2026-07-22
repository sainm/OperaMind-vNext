"""CLI for building a Workspace-bound Copilot Edit Packet."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import psycopg

from operamind.application import EditPacketRequest, EditPacketService
from operamind.contracts import ContractCatalog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a confirmed Workspace-bound Edit Packet")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--edit-packet-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--analysis-case-id", required=True)
    parser.add_argument("--impact-report-id", required=True)
    parser.add_argument("--confirmation-id", required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--forbidden-glob", action="append", required=True)
    parser.add_argument("--implementation-constraints", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        root = args.root.resolve()
        constraints = _load_constraints(root, args.implementation_constraints)
        with psycopg.connect(database_url) as connection:
            result = EditPacketService(
                connection=connection,
                contracts=ContractCatalog.load(root / "contracts"),
            ).run(
                EditPacketRequest(
                    edit_packet_id=args.edit_packet_id,
                    project_id=args.project_id,
                    analysis_case_id=args.analysis_case_id,
                    impact_report_id=args.impact_report_id,
                    confirmation_id=args.confirmation_id,
                    workspace_root=args.workspace_root,
                    forbidden_globs=tuple(args.forbidden_glob),
                    implementation_constraints=constraints,
                )
            )
        print(json.dumps(result.artifact, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, psycopg.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _load_constraints(root: Path, path: Path | None) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if path is None:
        return ()
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    raw: object = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not all(
        isinstance(key, str)
        and isinstance(value, list)
        and all(isinstance(item, str) for item in value)
        for key, value in raw.items()
    ):
        raise ValueError("Implementation constraints must map Item IDs to string arrays")
    values = cast(dict[str, list[str]], raw)
    return tuple((item_id, tuple(constraints)) for item_id, constraints in sorted(values.items()))


if __name__ == "__main__":
    raise SystemExit(main())
