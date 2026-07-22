"""Command-line entry point for one Contract-validated document Diff."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from operamind.application import DocumentDiffRequest, DocumentDiffService
from operamind.contracts import ContractCatalog
from operamind.domain.document_conventions import DocumentConvention
from operamind.infrastructure.documents import DocumentSignalExtractorRegistry
from operamind.profiles import ProfileCatalog


def build_parser() -> argparse.ArgumentParser:
    """Build the P1 document Diff command parser."""

    parser = argparse.ArgumentParser(
        description="Generate Contract-validated StructuredChange artifacts"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    parser.add_argument("--profile", type=Path, required=True, help="Convention Profile JSON")
    parser.add_argument("--before", type=Path, required=True, help="before Office document")
    parser.add_argument("--after", type=Path, required=True, help="after Office document")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--fact-type", required=True)
    parser.add_argument("--source-snapshot-id", required=True)
    parser.add_argument("--target-snapshot-id", required=True)
    parser.add_argument("--output", type=Path, help="write JSON envelope instead of stdout")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run a strict document Diff and return non-zero when any gate blocks."""

    args = build_parser().parse_args(argv)
    try:
        root = args.root.resolve()
        profile_path = _resolve(root, args.profile)
        profile = _load_object(profile_path)
        ProfileCatalog.load(root / "profiles").validate_profile(profile)
        convention = DocumentConvention.from_validated_profile(profile)
        service = DocumentDiffService(
            extractors=DocumentSignalExtractorRegistry.default(),
            contracts=ContractCatalog.load(root / "contracts"),
        )
        result = service.run(
            DocumentDiffRequest(
                project_id=args.project_id,
                domain=args.domain,
                fact_type=args.fact_type,
                source_snapshot_id=args.source_snapshot_id,
                target_snapshot_id=args.target_snapshot_id,
                before_path=_resolve(root, args.before),
                after_path=_resolve(root, args.after),
            ),
            convention,
        )
        rendered = json.dumps(result.to_payload(), ensure_ascii=False, indent=2) + "\n"
        if args.output is None:
            print(rendered, end="")
        else:
            output_path = _resolve(root, args.output)
            if not output_path.parent.is_dir():
                raise ValueError(f"Output parent directory does not exist: {output_path.parent}")
            output_path.write_text(rendered, encoding="utf-8")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
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
