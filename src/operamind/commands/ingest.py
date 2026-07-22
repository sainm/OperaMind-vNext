"""CLI for transactionally ingesting one before/after document pair."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import psycopg

from operamind.application import (
    DocumentDiffRequest,
    DocumentDiffService,
    PersistedDocumentDiffRequest,
    PersistedDocumentDiffService,
)
from operamind.contracts import ContractCatalog
from operamind.infrastructure.documents import DocumentSignalExtractorRegistry
from operamind.profiles import ProfileCatalog


def build_parser() -> argparse.ArgumentParser:
    """Build the P1 persisted-ingestion parser without accepting DB credentials."""

    parser = argparse.ArgumentParser(
        description="Persist one Contract-validated before/after document Diff"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    parser.add_argument("--profile", type=Path, required=True, help="Convention Profile JSON")
    parser.add_argument("--before", type=Path, required=True, help="before Office document")
    parser.add_argument("--after", type=Path, required=True, help="after Office document")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--analysis-case-id", required=True)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--fact-type", required=True)
    parser.add_argument("--source-snapshot-id", required=True)
    parser.add_argument("--target-snapshot-id", required=True)
    parser.add_argument("--ingestion-batch-id", required=True)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--logical-name", required=True)
    parser.add_argument("--source-document-version-id", required=True)
    parser.add_argument("--target-document-version-id", required=True)
    parser.add_argument("--source-ref", required=True, help="immutable source document reference")
    parser.add_argument("--target-ref", required=True, help="immutable target document reference")
    parser.add_argument("--profile-version-id", required=True)
    parser.add_argument("--profile-binding-key", required=True)
    parser.add_argument("--profile-activation-event-id", required=True)
    parser.add_argument("--activated-by", required=True)
    parser.add_argument("--activation-reason", required=True)
    parser.add_argument("--embedding-profile-ref")
    parser.add_argument("--output", type=Path, help="write JSON result instead of stdout")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Persist the full P1 ingestion transaction using OPERAMIND_DATABASE_URL."""

    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
        return 2

    try:
        root = args.root.resolve()
        output_path = _resolve(root, args.output) if args.output is not None else None
        if output_path is not None and not output_path.parent.is_dir():
            raise ValueError(f"Output parent directory does not exist: {output_path.parent}")
        profile = _load_object(_resolve(root, args.profile))
        contracts = ContractCatalog.load(root / "contracts")
        profiles = ProfileCatalog.load(root / "profiles")
        with psycopg.connect(database_url) as connection:
            service = PersistedDocumentDiffService(
                connection=connection,
                document_diff=DocumentDiffService(
                    extractors=DocumentSignalExtractorRegistry.default(),
                    contracts=contracts,
                ),
                contracts=contracts,
                profiles=profiles,
            )
            result = service.run(
                PersistedDocumentDiffRequest(
                    diff=DocumentDiffRequest(
                        project_id=args.project_id,
                        domain=args.domain,
                        fact_type=args.fact_type,
                        source_snapshot_id=args.source_snapshot_id,
                        target_snapshot_id=args.target_snapshot_id,
                        before_path=_resolve(root, args.before),
                        after_path=_resolve(root, args.after),
                    ),
                    ingestion_batch_id=args.ingestion_batch_id,
                    analysis_case_id=args.analysis_case_id,
                    document_id=args.document_id,
                    logical_name=args.logical_name,
                    source_document_version_id=args.source_document_version_id,
                    target_document_version_id=args.target_document_version_id,
                    source_ref=args.source_ref,
                    target_ref=args.target_ref,
                    profile_version_id=args.profile_version_id,
                    profile_binding_key=args.profile_binding_key,
                    profile_activation_event_id=args.profile_activation_event_id,
                    activated_by=args.activated_by,
                    activation_reason=args.activation_reason,
                    embedding_profile_ref=args.embedding_profile_ref,
                ),
                profile,
            )
        payload = {
            "document_ingestion_result": result.ingestion_artifact,
            "ingestion_result_event_id": result.initial_ingestion_event_id,
            **result.diff.to_payload(),
            "artifact_digests": dict(result.artifact_digests),
        }
        rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if output_path is None:
            print(rendered, end="")
        else:
            output_path.write_text(rendered, encoding="utf-8")
        return 0
    except (OSError, ValueError, json.JSONDecodeError, psycopg.Error) as error:
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
