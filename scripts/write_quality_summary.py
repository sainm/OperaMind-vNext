#!/usr/bin/env python3
"""Create a commit-bound GitHub Job Summary from machine-readable test reports."""

from __future__ import annotations

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TestSummary:
    label: str
    passed: int
    failures: int
    errors: int
    skipped: int
    total: int


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--junit",
        action="append",
        default=[],
        metavar="LABEL=PATH",
        help="JUnit XML report with its display label; may be repeated",
    )
    parser.add_argument("--coverage-json", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args(argv)

    if not args.junit and args.coverage_json is None:
        parser.error("at least one --junit or --coverage-json input is required")

    summaries: list[TestSummary] = []
    missing: list[str] = []
    try:
        for raw_report in args.junit:
            label, path = _labeled_path(raw_report)
            if not path.is_file():
                missing.append(f"{label}: {path}")
                continue
            summaries.append(_read_junit(label, path))
        coverage = None
        if args.coverage_json is not None:
            if args.coverage_json.is_file():
                coverage = _read_coverage(args.coverage_json)
            else:
                missing.append(f"Coverage: {args.coverage_json}")
    except (ET.ParseError, json.JSONDecodeError, OSError, ValueError) as error:
        print(f"quality summary error: {error}", file=sys.stderr)
        return 2

    if missing and not args.allow_missing:
        print(f"quality summary error: missing reports: {', '.join(missing)}", file=sys.stderr)
        return 2

    markdown = _render_summary(
        summaries=summaries,
        coverage=coverage,
        missing=missing,
        revision=os.getenv("GITHUB_SHA"),
    )
    if args.output is None:
        print(markdown, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("a", encoding="utf-8", newline="\n") as output:
            output.write(markdown)
    return 0


def _labeled_path(value: str) -> tuple[str, Path]:
    label, separator, raw_path = value.partition("=")
    if not separator or not label.strip() or not raw_path.strip():
        raise ValueError("--junit must use LABEL=PATH with non-blank values")
    return label.strip(), Path(raw_path.strip())


def _read_junit(label: str, path: Path) -> TestSummary:
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    failures = sum(case.find("failure") is not None for case in cases)
    errors = sum(case.find("error") is not None for case in cases)
    skipped = sum(case.find("skipped") is not None for case in cases)
    total = len(cases)
    passed = total - failures - errors - skipped
    if passed < 0:
        raise ValueError(f"JUnit report has inconsistent outcomes: {path}")
    return TestSummary(label, passed, failures, errors, skipped, total)


def _read_coverage(path: Path) -> float:
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("coverage report must be a JSON object")
    totals = payload.get("totals")
    if not isinstance(totals, dict):
        raise ValueError("coverage report totals must be an object")
    percent = totals.get("percent_covered")
    if not isinstance(percent, (int, float)) or isinstance(percent, bool):
        raise ValueError("coverage percent_covered must be a number")
    value = float(percent)
    if not 0 <= value <= 100:
        raise ValueError("coverage percent_covered must be between 0 and 100")
    return value


def _render_summary(
    *,
    summaries: list[TestSummary],
    coverage: float | None,
    missing: list[str],
    revision: str | None,
) -> str:
    lines = ["## 自動品質ベースライン", ""]
    if revision:
        lines.extend((f"Revision: `{revision}`", ""))
    if summaries:
        lines.extend(
            (
                "| Test suite | Passed | Failed | Errors | Skipped | Total |",
                "|---|---:|---:|---:|---:|---:|",
            )
        )
        lines.extend(
            f"| {_markdown(summary.label)} | {summary.passed} | {summary.failures} | "
            f"{summary.errors} | {summary.skipped} | {summary.total} |"
            for summary in summaries
        )
        lines.append("")
    if coverage is not None:
        lines.extend((f"Statement coverage: **{coverage:.2f}%**", ""))
    if missing:
        lines.extend(("未生成の Report:", ""))
        lines.extend(f"- `{_markdown(value)}`" for value in missing)
        lines.append("")
    lines.append("数値はこの Workflow Run の機械可読 Artifact から生成されています。")
    return "\n".join(lines) + "\n"


def _markdown(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


if __name__ == "__main__":
    raise SystemExit(main())
