"""CLI for publishing one Scope-bound Impact Report."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import psycopg

from operamind.application import (
    CodeScopeBlockedError,
    CodeScopeLimits,
    CodeScopeRequest,
    ImpactReportRequest,
    ImpactReportService,
    UiImpactStatus,
)
from operamind.commands.resolve_code_scope import load_anchors
from operamind.contracts import ContractCatalog
from operamind.profiles import ProfileCatalog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and publish one bounded Impact Report")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--anchors", type=Path, required=True)
    parser.add_argument("--impact-report-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--analysis-case-id", required=True)
    parser.add_argument("--context-package-id", required=True)
    parser.add_argument("--structured-change-id", required=True)
    parser.add_argument("--code-graph-snapshot-id", required=True)
    parser.add_argument("--repository-revision-id", required=True)
    parser.add_argument("--profile-binding-key", required=True)
    parser.add_argument(
        "--ui-impact-status",
        required=True,
        choices=[value.value for value in UiImpactStatus],
    )
    parser.add_argument("--ui-scenario-ref", action="append", default=[])
    parser.add_argument(
        "--planned-test-file",
        action="append",
        default=[],
        help="confirmed new test path derived from the Draft verification plan",
    )
    parser.add_argument("--analysis-policy-version", default="scope-impact-v1")
    parser.add_argument("--max-matches-per-anchor", type=int, default=100)
    parser.add_argument("--max-edges", type=int, default=100_000)
    parser.add_argument("--max-traversal-states", type=int, default=10_000)
    parser.add_argument("--max-unresolved-edges", type=int, default=100)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        root = args.root.resolve()
        anchors_path = args.anchors.resolve() if args.anchors.is_absolute() else root / args.anchors
        with psycopg.connect(database_url) as connection:
            result = ImpactReportService(
                connection=connection,
                contracts=ContractCatalog.load(root / "contracts"),
                profiles=ProfileCatalog.load(root / "profiles"),
            ).run(
                ImpactReportRequest(
                    impact_report_id=args.impact_report_id,
                    scope=CodeScopeRequest(
                        project_id=args.project_id,
                        analysis_case_id=args.analysis_case_id,
                        context_package_id=args.context_package_id,
                        structured_change_id=args.structured_change_id,
                        code_graph_snapshot_id=args.code_graph_snapshot_id,
                        repository_revision_id=args.repository_revision_id,
                        profile_binding_key=args.profile_binding_key,
                        anchors=load_anchors(anchors_path.resolve()),
                        limits=CodeScopeLimits(
                            max_matches_per_anchor=args.max_matches_per_anchor,
                            max_edges=args.max_edges,
                            max_traversal_states=args.max_traversal_states,
                            max_unresolved_edges=args.max_unresolved_edges,
                        ),
                    ),
                    ui_impact_status=UiImpactStatus(args.ui_impact_status),
                    required_ui_scenario_refs=tuple(args.ui_scenario_ref),
                    planned_test_files=tuple(args.planned_test_file),
                    analysis_policy_version=args.analysis_policy_version,
                )
            )
        print(json.dumps(result.artifact, ensure_ascii=False, indent=2))
        return 1 if result.artifact["status"] == "blocked" else 0
    except (
        CodeScopeBlockedError,
        OSError,
        ValueError,
        json.JSONDecodeError,
        psycopg.Error,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
