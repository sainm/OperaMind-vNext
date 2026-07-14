"""Command-line entry point for the P0 baseline checks."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from operamind.contracts import ContractCatalog
from operamind.golden import GoldenDatasetValidator
from operamind.validation import ValidationReport


def build_parser() -> argparse.ArgumentParser:
    """Build the baseline validation command parser."""

    parser = argparse.ArgumentParser(description="Validate the OperaMind vNext baseline")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("golden-dataset/manifest.silver.json"),
        help="Golden Dataset manifest relative to the repository root",
    )
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="also enforce frozen MVP Golden Dataset readiness",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Validate contracts and the selected Golden Dataset manifest."""

    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    contract_catalog = ContractCatalog.load(root / "contracts")
    reports = (
        contract_catalog.validate_catalog(),
        contract_catalog.validate_examples(),
        GoldenDatasetValidator(root / "golden-dataset").validate(
            root / args.manifest, require_ready=args.require_ready
        ),
    )
    issues = tuple(issue for report in reports for issue in report.issues)
    report = ValidationReport(issues)
    for issue in issues:
        print(f"{issue.severity}: {issue.code}: {issue.location}: {issue.message}")
    if report.is_valid:
        print("OperaMind baseline validation passed")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
