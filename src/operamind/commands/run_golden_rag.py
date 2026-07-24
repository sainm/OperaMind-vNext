"""Run frozen Golden queries against one exact live Search Index scope."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Never, cast

import psycopg

from operamind.application import GoldenRagQualityRequest, GoldenRagQualityService
from operamind.contracts import ContractCatalog
from operamind.golden import GoldenDatasetValidator, plan_golden_queries
from operamind.infrastructure.embeddings import (
    EmbeddingProvider,
    EmbeddingProviderError,
    OpenAICompatibleEmbeddingProvider,
)
from operamind.infrastructure.postgres import (
    ArtifactRepository,
    GoldenSemanticBindingRepository,
    ProfileRepository,
    SearchIndexBuildState,
    SearchIndexRepository,
)
from operamind.profiles import ProfileCatalog


class _ReplayOnlyProvider:
    """Reject Provider access while replaying an immutable report identity."""

    def probe(self) -> Never:
        raise EmbeddingProviderError("Golden report replay unexpectedly probed embeddings")

    def embed(self, texts: tuple[str, ...]) -> Never:
        raise EmbeddingProviderError("Golden report replay unexpectedly embedded queries")


def build_parser() -> argparse.ArgumentParser:
    """Build the live Golden retrieval parser without accepting credentials."""

    parser = argparse.ArgumentParser(
        description=(
            "Execute frozen Golden queries using the current Embedding Profile and "
            "ready/current Search Index"
        )
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("golden-dataset/manifest.golden.json"),
    )
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--report-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument(
        "--document-snapshot-id",
        help="exact Snapshot ID; omit together with Profile/Build to discover by Golden targets",
    )
    parser.add_argument(
        "--embedding-profile-version-id",
        help="exact active Profile version; omit together with Snapshot/Build to discover",
    )
    parser.add_argument(
        "--embedding-profile-binding-key",
        default="embedding:document_search",
    )
    parser.add_argument(
        "--search-index-build-id",
        help="exact ready/current Build; omit together with Snapshot/Profile to discover",
    )
    parser.add_argument("--created-by", required=True)
    parser.add_argument("--vector-top-k", type=int, default=10)
    parser.add_argument("--keyword-top-k", type=int, default=10)
    parser.add_argument("--final-top-k", type=int, default=10)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument(
        "--output",
        type=Path,
        help="write the persisted report envelope instead of stdout",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Persist a passed, failed, or blocked report and return a gate-compatible code."""

    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        root = args.root.resolve()
        dataset_root = root / "golden-dataset"
        manifest_path = _resolve_inside(root, args.manifest)
        validation = GoldenDatasetValidator(dataset_root).validate(
            manifest_path,
            require_ready=True,
        )
        if not validation.is_valid:
            issue = validation.issues[0]
            raise ValueError(f"Golden manifest is not ready at {issue.location}: {issue.message}")
        manifest = _load_object(manifest_path)
        if manifest.get("dataset_stage") != "golden" or manifest.get("status") != "frozen":
            raise ValueError("Golden retrieval requires a frozen Golden manifest")
        case = _select_case(manifest, case_id=args.case_id, project_id=args.project_id)
        expected_path = _resolve_inside(dataset_root, Path(str(case["expected_rag_context"])))
        expected = _load_object(expected_path)
        expected_changes_path = _resolve_inside(
            dataset_root,
            Path(str(case["expected_changes"])),
        )
        query_plan = plan_golden_queries(
            _load_object(expected_changes_path),
            expected,
        )
        contracts = ContractCatalog.load(root / "contracts")
        profiles = ProfileCatalog.load(root / "profiles")
        explicit_scope = (
            args.document_snapshot_id,
            args.embedding_profile_version_id,
            args.search_index_build_id,
        )
        if any(explicit_scope) and not all(explicit_scope):
            raise ValueError(
                "Snapshot, Embedding Profile, and Search Index Build must be supplied together"
            )
        with psycopg.connect(database_url) as connection:
            existing = ArtifactRepository(connection, contracts).get(args.report_id)
            provider: EmbeddingProvider
            if existing is not None:
                scope = (
                    tuple(str(value) for value in explicit_scope)
                    if all(explicit_scope)
                    else _scope_from_existing(existing)
                )
                provider = _ReplayOnlyProvider()
            else:
                active = ProfileRepository(connection, profiles).get_active(
                    project_id=args.project_id,
                    binding_key=args.embedding_profile_binding_key,
                )
                if active is None:
                    raise ValueError("Embedding Profile is not active for the Golden project")
                if all(explicit_scope):
                    scope = tuple(str(value) for value in explicit_scope)
                else:
                    builds = SearchIndexRepository(connection).find_current_builds(
                        project_id=args.project_id,
                        profile_version_id=active.profile_version_id,
                    )
                    resolver = GoldenSemanticBindingRepository(connection)
                    resolved_builds = tuple(
                        build
                        for build in builds
                        if _can_resolve_bindings(
                            resolver,
                            project_id=args.project_id,
                            build=build,
                            expected=expected,
                        )
                    )
                    if len(resolved_builds) != 1:
                        raise ValueError(
                            "Golden semantic binding discovery requires exactly one "
                            f"ready/current Search Index; found {len(resolved_builds)}"
                        )
                    build = resolved_builds[0]
                    scope = (
                        build.spec.snapshot_id,
                        build.spec.profile_version_id,
                        build.spec.build_id,
                    )
                provider = OpenAICompatibleEmbeddingProvider.from_profile(active.profile)
            request = GoldenRagQualityRequest(
                report_id=args.report_id,
                case_id=args.case_id,
                dataset_id=str(manifest["dataset_id"]),
                dataset_version=str(manifest["dataset_version"]),
                project_id=args.project_id,
                document_snapshot_id=scope[0],
                embedding_profile_version_id=scope[1],
                embedding_profile_binding_key=args.embedding_profile_binding_key,
                search_index_build_id=scope[2],
                expected=expected,
                query_plan_version=query_plan.planner_version,
                query_texts=(
                    query_plan.queries[0].text,
                    query_plan.queries[1].text,
                    query_plan.queries[2].text,
                ),
                created_by=args.created_by,
                vector_top_k=args.vector_top_k,
                keyword_top_k=args.keyword_top_k,
                final_top_k=args.final_top_k,
                rrf_k=args.rrf_k,
            )
            result = GoldenRagQualityService(
                connection=connection,
                contracts=contracts,
                profiles=profiles,
            ).run(request, provider=provider)
        rendered = (
            json.dumps(
                {
                    "created": result.created,
                    "status": result.state.status,
                    "report": result.artifact,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        if args.output is None:
            print(rendered, end="")
        else:
            output_path = (
                args.output.resolve()
                if args.output.is_absolute()
                else (root / args.output).resolve()
            )
            if not output_path.parent.is_dir():
                raise ValueError(f"Output parent directory does not exist: {output_path.parent}")
            output_path.write_text(rendered, encoding="utf-8")
        return 0 if result.state.status == "passed" else 1
    except (OSError, ValueError, EmbeddingProviderError, psycopg.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


def _select_case(manifest: dict[str, Any], *, case_id: str, project_id: str) -> dict[str, object]:
    cases = cast(list[dict[str, object]], manifest["cases"])
    matches = [case for case in cases if case.get("case_id") == case_id]
    if len(matches) != 1:
        raise ValueError(f"Golden manifest must contain exactly one case: {case_id}")
    case = matches[0]
    if case.get("project_id") != project_id:
        raise ValueError("Golden case Project differs from requested Project")
    return case


def _can_resolve_bindings(
    resolver: GoldenSemanticBindingRepository,
    *,
    project_id: str,
    build: SearchIndexBuildState,
    expected: dict[str, Any],
) -> bool:
    try:
        resolver.resolve(
            project_id=project_id,
            snapshot_id=str(build.spec.snapshot_id),
            search_index_build_id=str(build.spec.build_id),
            expected=expected,
        )
    except ValueError:
        return False
    return True


def _scope_from_existing(artifact: dict[str, Any]) -> tuple[str, str, str]:
    if artifact.get("artifact_type") != "GoldenRagQualityReport":
        raise ValueError("Golden report ID belongs to a different Artifact type")
    scope = (
        str(artifact.get("document_snapshot_id", "")),
        str(artifact.get("embedding_profile_version_id", "")),
        str(artifact.get("search_index_build_id", "")),
    )
    if any(not value.strip() for value in scope):
        raise ValueError("Persisted Golden report has no complete retrieval scope")
    return scope


def _resolve_inside(root: Path, path: Path) -> Path:
    base = root.resolve()
    resolved = path.resolve() if path.is_absolute() else (base / path).resolve()
    if not resolved.is_relative_to(base):
        raise ValueError(f"Path escapes allowed root: {path}")
    return resolved


def _load_object(path: Path) -> dict[str, Any]:
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return raw


if __name__ == "__main__":
    raise SystemExit(main())
