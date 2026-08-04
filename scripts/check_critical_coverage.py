#!/usr/bin/env python3
"""Fail when total or capability-critical file coverage is below policy."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "operamind-critical-coverage-v1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-json", type=Path, default=Path("coverage.json"))
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("quality/critical-coverage.json"),
    )
    args = parser.parse_args(argv)

    try:
        policy = _load_object(args.config, "coverage policy")
        report = _load_object(args.coverage_json, "coverage report")
        overall_minimum, file_minimum, file_overrides, capabilities = _parse_policy(
            policy
        )
        files = _coverage_files(report)
        total = _percent(_required_object(report, "totals"), "percent_covered")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"coverage gate configuration error: {error}", file=sys.stderr)
        return 2

    failures: list[str] = []
    if total < overall_minimum:
        failures.append(
            f"TOTAL {total:.2f}% is below required {overall_minimum:.2f}%"
        )
    print(f"{'PASS' if total >= overall_minimum else 'FAIL'} total {total:.2f}%")

    for capability, paths in capabilities.items():
        for path in paths:
            summary = files.get(path)
            if summary is None:
                failures.append(f"{capability}: coverage report is missing {path}")
                print(f"FAIL {capability} {path} missing")
                continue
            percent = _percent(summary, "percent_covered")
            required = file_overrides.get(path, file_minimum)
            passed = percent >= required
            print(f"{'PASS' if passed else 'FAIL'} {capability} {path} {percent:.2f}%")
            if not passed:
                failures.append(
                    f"{capability}: {path} {percent:.2f}% is below "
                    f"required {required:.2f}%"
                )

    if failures:
        print("\nCoverage gate failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    return 0


def _load_object(path: Path, label: str) -> dict[str, Any]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _parse_policy(
    policy: dict[str, Any],
) -> tuple[float, float, dict[str, float], dict[str, tuple[str, ...]]]:
    if policy.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"coverage policy schema_version must be {SCHEMA_VERSION}")
    overall = _bounded_percent(policy.get("overall_minimum_percent"), "overall minimum")
    file_minimum = _bounded_percent(policy.get("file_minimum_percent"), "file minimum")
    raw_capabilities = policy.get("capabilities")
    if not isinstance(raw_capabilities, dict) or not raw_capabilities:
        raise ValueError("coverage policy capabilities must be a non-empty object")

    capabilities: dict[str, tuple[str, ...]] = {}
    seen: set[str] = set()
    for raw_name, raw_paths in raw_capabilities.items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ValueError("coverage capability names must be non-blank strings")
        if not isinstance(raw_paths, list) or not raw_paths:
            raise ValueError(f"coverage capability {raw_name} must list files")
        paths: list[str] = []
        for raw_path in raw_paths:
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise ValueError(f"coverage capability {raw_name} has an invalid file")
            path = raw_path.replace("\\", "/")
            if path in seen:
                raise ValueError(f"coverage policy lists {path} more than once")
            seen.add(path)
            paths.append(path)
        capabilities[raw_name] = tuple(paths)

    raw_overrides = policy.get("file_minimum_percent_overrides", {})
    if not isinstance(raw_overrides, dict):
        raise ValueError("coverage policy file minimum overrides must be an object")
    overrides: dict[str, float] = {}
    for raw_path, raw_percent in raw_overrides.items():
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("coverage policy file minimum override has an invalid file")
        path = raw_path.replace("\\", "/")
        if path not in seen:
            raise ValueError(
                f"coverage policy file minimum override is not a capability file: {path}"
            )
        overrides[path] = _bounded_percent(
            raw_percent,
            f"file minimum override for {path}",
        )
    return overall, file_minimum, overrides, capabilities


def _coverage_files(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_files = report.get("files")
    if not isinstance(raw_files, dict):
        raise ValueError("coverage report files must be an object")
    files: dict[str, dict[str, Any]] = {}
    for raw_path, raw_value in raw_files.items():
        if not isinstance(raw_path, str) or not isinstance(raw_value, dict):
            raise ValueError("coverage report has an invalid file entry")
        summary = _required_object(raw_value, "summary")
        files[raw_path.replace("\\", "/")] = summary
    return files


def _required_object(value: dict[str, Any], key: str) -> dict[str, Any]:
    child = value.get(key)
    if not isinstance(child, dict):
        raise ValueError(f"coverage report {key} must be an object")
    return child


def _percent(value: dict[str, Any], key: str) -> float:
    return _bounded_percent(value.get(key), f"coverage report {key}")


def _bounded_percent(value: object, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{label} must be a number")
    percent = float(value)
    if not 0 <= percent <= 100:
        raise ValueError(f"{label} must be between 0 and 100")
    return percent


if __name__ == "__main__":
    raise SystemExit(main())
