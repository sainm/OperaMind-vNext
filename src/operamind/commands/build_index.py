"""CLI for building one complete Canonical Document Search Index."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import psycopg

from operamind.application import SearchIndexBuildRequest, SearchIndexBuildService
from operamind.infrastructure.embeddings import (
    EmbeddingProviderError,
    OpenAICompatibleEmbeddingProvider,
)
from operamind.infrastructure.postgres import PersistenceConflictError
from operamind.profiles import ProfileCatalog


def build_parser() -> argparse.ArgumentParser:
    """Build the index command parser without accepting credentials."""

    parser = argparse.ArgumentParser(
        description="Build a complete pgvector index for one committed Document Snapshot"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    parser.add_argument("--profile", type=Path, required=True, help="Embedding Profile JSON")
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--profile-version-id", required=True)
    parser.add_argument("--profile-binding-key", default="embedding:document_search")
    parser.add_argument("--profile-activation-event-id", required=True)
    parser.add_argument("--activated-by", required=True)
    parser.add_argument("--activation-reason", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Build and publish an index using OPERAMIND_DATABASE_URL and Profile env names."""

    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        root = args.root.resolve()
        profile = _load_object(_resolve(root, args.profile))
        profiles = ProfileCatalog.load(root / "profiles")
        profiles.validate_profile(profile)
        provider = OpenAICompatibleEmbeddingProvider.from_profile(profile)
        with psycopg.connect(database_url) as connection:
            result = SearchIndexBuildService(
                connection=connection,
                profiles=profiles,
            ).run(
                SearchIndexBuildRequest(
                    build_id=args.build_id,
                    project_id=args.project_id,
                    snapshot_id=args.snapshot_id,
                    profile_version_id=args.profile_version_id,
                    profile_binding_key=args.profile_binding_key,
                    profile_activation_event_id=args.profile_activation_event_id,
                    activated_by=args.activated_by,
                    activation_reason=args.activation_reason,
                ),
                profile=profile,
                provider=provider,
            )
        print(
            json.dumps(
                {
                    "search_index_build_id": result.state.spec.build_id,
                    "project_id": result.state.spec.project_id,
                    "document_snapshot_id": result.state.spec.snapshot_id,
                    "embedding_profile_version_id": result.state.spec.profile_version_id,
                    "embedding_model": result.state.spec.model,
                    "dimensions": result.state.spec.dimensions,
                    "preprocessing_version": result.state.spec.preprocessing_version,
                    "ranking_policy_version": result.state.spec.ranking_policy_version,
                    "document_relation_build_id": result.state.spec.relation_build_id,
                    "status": result.state.status.value,
                    "eligible_target_count": result.state.eligible_target_count,
                    "indexed_target_count": result.state.indexed_target_count,
                    "reused_vector_count": result.state.reused_vector_count,
                    "generated_vector_count": result.generated_vector_count,
                    "is_current": result.state.is_current,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        EmbeddingProviderError,
        PersistenceConflictError,
        psycopg.Error,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _resolve(root: Path, path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _load_object(path: Path) -> dict[str, Any]:
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return raw


if __name__ == "__main__":
    raise SystemExit(main())
