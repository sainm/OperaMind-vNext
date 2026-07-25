"""Command-line entry point for the P0 baseline checks."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from operamind.contracts import ContractCatalog
from operamind.golden import GOLDEN_DATASET_DIGEST_ALGORITHM, GoldenDatasetValidator
from operamind.profiles import ProfileCatalog
from operamind.readiness import MvpReadinessValidator
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
    parser.add_argument(
        "--readiness-manifest",
        type=Path,
        default=Path("readiness/mvp-readiness.silver.json"),
        help="repository-wide MVP readiness manifest relative to the repository root",
    )
    parser.add_argument(
        "--require-mvp-ready",
        action="store_true",
        help="require frozen Golden data and finalized evidence for every MVP gate",
    )
    parser.add_argument(
        "--print-source-tree-digest",
        action="store_true",
        help="print the deterministic source tree digest used by full-regression evidence",
    )
    parser.add_argument(
        "--print-golden-dataset-digest",
        action="store_true",
        help="print the deterministic digest of the selected Golden Dataset and references",
    )
    parser.add_argument(
        "--print-readiness-status",
        action="store_true",
        help="print a compact MVP readiness stage summary for the selected manifest",
    )
    parser.add_argument(
        "--print-readiness-json",
        action="store_true",
        help="print a machine-readable MVP readiness stage summary for the selected manifest",
    )
    parser.add_argument(
        "--require-readiness-stage",
        choices=("dev_silver", "golden_ready_partial", "partial_ready", "mvp_ready"),
        help="fail unless the selected readiness manifest resolves to this stage",
    )
    parser.add_argument(
        "--print-evidence-digest",
        action="append",
        default=[],
        metavar="PATH",
        help="print the SHA-256 digest for a repository-relative readiness evidence file",
    )
    parser.add_argument(
        "--validate-evidence-candidate",
        action="append",
        default=[],
        type=Path,
        metavar="PATH",
        help="validate a captured pending-review evidence candidate",
    )
    parser.add_argument(
        "--validate-reviewed-evidence",
        action="append",
        default=[],
        type=Path,
        metavar="PATH",
        help="preflight reviewed or deterministically verified final evidence",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Validate contracts and the selected Golden Dataset manifest."""

    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    contract_catalog = ContractCatalog.load(root / "contracts")
    profile_catalog = ProfileCatalog.load(root / "profiles")
    readiness_validator = MvpReadinessValidator(root)
    candidate_reports = tuple(
        (
            candidate_path,
            readiness_validator.validate_candidate(root / candidate_path),
        )
        for candidate_path in args.validate_evidence_candidate
    )
    reviewed_evidence_reports = tuple(
        (
            evidence_path,
            readiness_validator.validate_reviewed_evidence(
                root / evidence_path,
                golden_manifest_path=root / args.manifest,
            ),
        )
        for evidence_path in args.validate_reviewed_evidence
    )
    reports = (
        contract_catalog.validate_catalog(),
        contract_catalog.validate_examples(),
        profile_catalog.validate_catalog(),
        profile_catalog.validate_examples(),
        GoldenDatasetValidator(root / "golden-dataset").validate(
            root / args.manifest,
            require_ready=args.require_ready or args.require_mvp_ready,
        ),
        readiness_validator.validate(
            root / args.readiness_manifest,
            require_ready=args.require_mvp_ready,
            require_golden_ready=args.require_ready or args.require_mvp_ready,
            golden_manifest_path=root / args.manifest,
        ),
        *(candidate_report for _, candidate_report in candidate_reports),
        *(evidence_report for _, evidence_report in reviewed_evidence_reports),
    )
    issues = tuple(issue for report in reports for issue in report.issues)
    report = ValidationReport(issues)
    for issue in issues:
        print(f"{issue.severity}: {issue.code}: {issue.location}: {issue.message}")
    for candidate_path, candidate_report in candidate_reports:
        if candidate_report.is_valid:
            print(f"Readiness evidence candidate valid: {candidate_path.as_posix()}")
    for evidence_path, evidence_report in reviewed_evidence_reports:
        if evidence_report.is_valid:
            print(f"Reviewed readiness evidence valid: {evidence_path.as_posix()}")
    summary = None
    summary_failed = False
    if (
        args.print_readiness_status
        or args.print_readiness_json
        or args.require_readiness_stage is not None
    ):
        try:
            summary = readiness_validator.summarize(root / args.readiness_manifest)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            print(f"error: readiness.summary_unavailable: {error}")
            summary_failed = True
    if args.print_readiness_status and summary is not None:
        print(f"Readiness stage: {summary.readiness_stage}")
        print(f"Manifest status: {summary.manifest_status}")
        print(f"Passed gates: {', '.join(summary.passed_gates) or '(none)'}")
        print(f"Pending gates: {', '.join(summary.pending_gates) or '(none)'}")
        print(f"Validation issues: {', '.join(summary.validation_issues) or '(none)'}")
    if args.print_readiness_json and summary is not None:
        print(
            json.dumps(
                {
                    "readiness_stage": summary.readiness_stage,
                    "manifest_status": summary.manifest_status,
                    "passed_gates": list(summary.passed_gates),
                    "pending_gates": list(summary.pending_gates),
                    "validation_issues": list(summary.validation_issues),
                    "gates": [
                        {
                            "gate_id": gate.gate_id,
                            "status": gate.status,
                            "expected_evidence_type": gate.expected_evidence_type,
                            "evidence_template": gate.evidence_template,
                            "evidence_count": gate.evidence_count,
                            "blocking_reason": gate.blocking_reason,
                            "validation_issues": list(gate.validation_issues),
                        }
                        for gate in summary.gates
                    ],
                },
                sort_keys=True,
            )
        )
    stage_failed = False
    if args.require_readiness_stage is not None:
        if summary is None:
            stage_failed = True
        elif summary.readiness_stage != args.require_readiness_stage:
            print(
                "error: readiness.stage_mismatch: "
                f"expected={args.require_readiness_stage} actual={summary.readiness_stage}"
            )
            stage_failed = True
    digest_failed = False
    for raw_evidence_path in args.print_evidence_digest:
        pure = PurePosixPath(raw_evidence_path)
        if "\\" in raw_evidence_path or pure.is_absolute() or ".." in pure.parts:
            print(f"error: readiness.evidence_digest_path_invalid: {raw_evidence_path}")
            digest_failed = True
            continue
        evidence_path = (root / raw_evidence_path).resolve()
        if not evidence_path.is_relative_to(root) or not evidence_path.is_file():
            print(f"error: readiness.evidence_digest_path_invalid: {raw_evidence_path}")
            digest_failed = True
            continue
        print(
            "Readiness evidence digest "
            f"{pure.as_posix()} "
            f"{MvpReadinessValidator._file_digest(evidence_path)}"
        )
    if summary_failed or stage_failed or digest_failed:
        return 1
    if report.is_valid:
        if args.print_source_tree_digest:
            print(f"OperaMind source tree digest {MvpReadinessValidator.source_tree_digest(root)}")
        if args.print_golden_dataset_digest:
            try:
                dataset_digest = GoldenDatasetValidator(root / "golden-dataset").dataset_digest(
                    root / args.manifest
                )
            except (OSError, ValueError) as error:
                print(f"error: golden.dataset_digest_unavailable: {error}")
                return 1
            print(
                "OperaMind Golden Dataset digest "
                f"{GOLDEN_DATASET_DIGEST_ALGORITHM} {dataset_digest}"
            )
        print("OperaMind baseline validation passed")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
