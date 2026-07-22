"""CLI for resolving an evidence-bound bounded Code Graph workset."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import psycopg

from operamind.application import (
    CodeScopeBlockedError,
    CodeScopeLimits,
    CodeScopeRequest,
    CodeScopeResolverService,
)
from operamind.contracts import ContractCatalog
from operamind.domain import CodeAnchor, CodeAnchorKind
from operamind.profiles import ProfileCatalog


def build_parser() -> argparse.ArgumentParser:
    """Build a parser that requires typed anchors in a separate JSON input."""

    parser = argparse.ArgumentParser(
        description="Resolve typed anchors through one current Code Graph Snapshot"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="OperaMind root")
    parser.add_argument("--anchors", type=Path, required=True, help="typed anchor JSON")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--analysis-case-id", required=True)
    parser.add_argument("--context-package-id", required=True)
    parser.add_argument("--structured-change-id", required=True)
    parser.add_argument("--code-graph-snapshot-id", required=True)
    parser.add_argument("--repository-revision-id", required=True)
    parser.add_argument("--profile-binding-key", required=True)
    parser.add_argument("--max-matches-per-anchor", type=int, default=100)
    parser.add_argument("--max-edges", type=int, default=100_000)
    parser.add_argument("--max-traversal-states", type=int, default=10_000)
    parser.add_argument("--max-unresolved-edges", type=int, default=100)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Resolve scope using OPERAMIND_DATABASE_URL and print no source content."""

    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        root = args.root.resolve()
        anchors = load_anchors(_resolve(root, args.anchors))
        with psycopg.connect(database_url) as connection:
            result = CodeScopeResolverService(
                connection=connection,
                contracts=ContractCatalog.load(root / "contracts"),
                profiles=ProfileCatalog.load(root / "profiles"),
            ).resolve(
                CodeScopeRequest(
                    project_id=args.project_id,
                    analysis_case_id=args.analysis_case_id,
                    context_package_id=args.context_package_id,
                    structured_change_id=args.structured_change_id,
                    code_graph_snapshot_id=args.code_graph_snapshot_id,
                    repository_revision_id=args.repository_revision_id,
                    profile_binding_key=args.profile_binding_key,
                    anchors=anchors,
                    limits=CodeScopeLimits(
                        max_matches_per_anchor=args.max_matches_per_anchor,
                        max_edges=args.max_edges,
                        max_traversal_states=args.max_traversal_states,
                        max_unresolved_edges=args.max_unresolved_edges,
                    ),
                )
            )
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 1 if result.confirmation_blocked else 0
    except (
        CodeScopeBlockedError,
        OSError,
        ValueError,
        json.JSONDecodeError,
        psycopg.Error,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _resolve(root: Path, path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def load_anchors(path: Path) -> tuple[CodeAnchor, ...]:
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != {"anchors"}:
        raise ValueError("Anchor input must contain only an anchors array")
    values = raw["anchors"]
    if not isinstance(values, list):
        raise ValueError("anchors must be an array")
    anchors: list[CodeAnchor] = []
    for value in values:
        if not isinstance(value, dict) or set(value) != {
            "anchor_id",
            "kind",
            "value",
            "evidence_refs",
        }:
            raise ValueError("Each anchor must contain ID, kind, value, and evidence_refs")
        evidence = value["evidence_refs"]
        if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
            raise ValueError("Anchor evidence_refs must be strings")
        anchors.append(
            CodeAnchor(
                anchor_id=str(value["anchor_id"]),
                kind=CodeAnchorKind(str(value["kind"])),
                value=str(value["value"]),
                evidence_refs=tuple(cast(list[str], evidence)),
            )
        )
    return tuple(anchors)


if __name__ == "__main__":
    raise SystemExit(main())
