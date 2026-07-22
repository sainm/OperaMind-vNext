"""CLI for formal ContextPackage planning, retrieval, and persistence."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Never

import psycopg

from operamind.application import ContextPackageRequest, ContextPackageService
from operamind.contracts import ContractCatalog
from operamind.infrastructure.embeddings import (
    EmbeddingProvider,
    EmbeddingProviderError,
    OpenAICompatibleEmbeddingProvider,
)
from operamind.infrastructure.postgres import ArtifactRepository, ProfileRepository
from operamind.profiles import ProfileCatalog


class _ReplayOnlyProvider:
    """Prove that immutable ContextPackage replay performs no Provider call."""

    def probe(self) -> Never:
        raise EmbeddingProviderError("Replay unexpectedly requested an embedding probe")

    def embed(self, texts: tuple[str, ...]) -> Never:
        raise EmbeddingProviderError("Replay unexpectedly requested query embeddings")


def build_parser() -> argparse.ArgumentParser:
    """Build the Context Package parser without accepting credentials."""

    parser = argparse.ArgumentParser(
        description="Build and persist one formal ContextPackage for an accepted change"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    parser.add_argument("--context-package-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--analysis-case-id", required=True)
    parser.add_argument("--ingestion-batch-id", required=True)
    parser.add_argument("--ingestion-result-event-id", required=True)
    parser.add_argument("--target-snapshot-id", required=True)
    parser.add_argument("--change-id", required=True)
    parser.add_argument("--embedding-profile-version-id", required=True)
    parser.add_argument(
        "--embedding-profile-binding-key",
        default="embedding:document_search",
    )
    parser.add_argument("--token-budget", type=int, required=True)
    parser.add_argument("--vector-top-k", type=int, default=10)
    parser.add_argument("--keyword-top-k", type=int, default=10)
    parser.add_argument("--final-top-k", type=int, default=10)
    parser.add_argument("--adjacent-distance", type=int, default=1)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Build a ContextPackage using DB-bound Profile configuration and env secrets."""

    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
        return 2

    try:
        root = args.root.resolve()
        contracts = ContractCatalog.load(root / "contracts")
        profiles = ProfileCatalog.load(root / "profiles")
        request = ContextPackageRequest(
            context_package_id=args.context_package_id,
            project_id=args.project_id,
            analysis_case_id=args.analysis_case_id,
            ingestion_batch_id=args.ingestion_batch_id,
            ingestion_result_event_id=args.ingestion_result_event_id,
            target_snapshot_id=args.target_snapshot_id,
            change_id=args.change_id,
            embedding_profile_version_id=args.embedding_profile_version_id,
            embedding_profile_binding_key=args.embedding_profile_binding_key,
            token_budget=args.token_budget,
            vector_top_k=args.vector_top_k,
            keyword_top_k=args.keyword_top_k,
            final_top_k=args.final_top_k,
            adjacent_distance=args.adjacent_distance,
        )
        with psycopg.connect(database_url) as connection:
            existing = ArtifactRepository(connection, contracts).get(args.context_package_id)
            provider: EmbeddingProvider
            if existing is None:
                active = ProfileRepository(connection, profiles).get_active(
                    project_id=args.project_id,
                    binding_key=args.embedding_profile_binding_key,
                )
                if active is None:
                    raise ValueError("Embedding Profile is not active for the project")
                provider = OpenAICompatibleEmbeddingProvider.from_profile(active.profile)
            else:
                provider = _ReplayOnlyProvider()
            result = ContextPackageService(
                connection=connection,
                contracts=contracts,
                profiles=profiles,
            ).run(request, provider=provider)
        print(
            json.dumps(
                {
                    "created": result.created,
                    "query_plan_version": result.query_plan.planner_version,
                    "context_package": result.artifact,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (OSError, ValueError, EmbeddingProviderError, psycopg.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
