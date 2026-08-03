"""Parse approved coverage-tool reports into normalized changed-line evidence."""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import cast

from operamind.application.change_coverage import ChangedLineCoverageEvidence


def load_coverage_report(
    *,
    workspace_root: Path,
    report_path: str,
    report_format: str,
    expected_digest: str,
    evidence_ref: str,
    minimum_coverage_percent: float = 80.0,
) -> ChangedLineCoverageEvidence:
    path = _safe_report_path(workspace_root, report_path)
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_digest:
        raise ValueError("Coverage report content changed after the approved command")
    if report_format == "jacoco_xml":
        executable, covered = _jacoco_xml(content, workspace_root)
    elif report_format == "coverage_py_json":
        executable, covered = _coverage_py_json(content, workspace_root)
    elif report_format == "lcov":
        executable, covered = _lcov(content, workspace_root)
    else:
        raise ValueError(f"Unsupported coverage report format: {report_format}")
    return ChangedLineCoverageEvidence(
        evidence_refs=(evidence_ref,),
        executable_lines=_line_tuples(executable),
        covered_lines=_line_tuples(covered),
        minimum_coverage_percent=minimum_coverage_percent,
    )


def coverage_report_digest(workspace_root: Path, report_path: str) -> str:
    return hashlib.sha256(_safe_report_path(workspace_root, report_path).read_bytes()).hexdigest()


def _safe_report_path(workspace_root: Path, report_path: str) -> Path:
    relative = PurePosixPath(report_path)
    if relative.is_absolute() or ".." in relative.parts or "\\" in report_path:
        raise ValueError("Coverage report path must be Workspace-relative")
    root = workspace_root.resolve(strict=True)
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Coverage report path is outside the Workspace or is not a file")
    return path


def _coverage_py_json(
    content: bytes, workspace_root: Path
) -> tuple[dict[str, set[int]], dict[str, set[int]]]:
    value = json.loads(content)
    files = value.get("files") if isinstance(value, dict) else None
    if not isinstance(files, dict):
        raise ValueError("coverage.py JSON report has no files object")
    executable: dict[str, set[int]] = {}
    covered: dict[str, set[int]] = {}
    for raw_path, raw_record in files.items():
        if not isinstance(raw_record, dict):
            continue
        path = _repository_path(workspace_root, str(raw_path))
        executed = {
            int(str(line))
            for line in cast(list[object], raw_record.get("executed_lines", []))
        }
        missing = {
            int(str(line))
            for line in cast(list[object], raw_record.get("missing_lines", []))
        }
        executable[path] = executed | missing
        covered[path] = executed
    return executable, covered


def _jacoco_xml(
    content: bytes, workspace_root: Path
) -> tuple[dict[str, set[int]], dict[str, set[int]]]:
    root = ET.fromstring(content)
    executable: dict[str, set[int]] = {}
    covered: dict[str, set[int]] = {}
    workspace_files = tuple(workspace_root.rglob("*.java"))
    for package in root.findall(".//package"):
        package_name = str(package.get("name") or "").strip("/")
        for source in package.findall("sourcefile"):
            suffix = "/".join(value for value in (package_name, source.get("name")) if value)
            matches = [path for path in workspace_files if path.as_posix().endswith(suffix)]
            if len(matches) != 1:
                raise ValueError(f"JaCoCo source path is not unique in Workspace: {suffix}")
            path = matches[0].relative_to(workspace_root).as_posix()
            executable[path] = set()
            covered[path] = set()
            for line in source.findall("line"):
                number = int(str(line.get("nr")))
                executable[path].add(number)
                if int(str(line.get("ci") or "0")) > 0:
                    covered[path].add(number)
    return executable, covered


def _lcov(
    content: bytes, workspace_root: Path
) -> tuple[dict[str, set[int]], dict[str, set[int]]]:
    executable: dict[str, set[int]] = {}
    covered: dict[str, set[int]] = {}
    current: str | None = None
    for line in content.decode("utf-8", errors="strict").splitlines():
        if line.startswith("SF:"):
            current = _repository_path(workspace_root, line[3:])
            executable.setdefault(current, set())
            covered.setdefault(current, set())
        elif line.startswith("DA:") and current is not None:
            number, count, *_rest = line[3:].split(",")
            executable[current].add(int(number))
            if int(count) > 0:
                covered[current].add(int(number))
    return executable, covered


def _repository_path(workspace_root: Path, value: str) -> str:
    raw = Path(value)
    path = (
        raw.resolve(strict=False)
        if raw.is_absolute()
        else (workspace_root / raw).resolve(strict=False)
    )
    root = workspace_root.resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError(f"Coverage source path is outside the Workspace: {value}")
    return path.relative_to(root).as_posix()


def _line_tuples(values: dict[str, set[int]]) -> tuple[tuple[str, tuple[int, ...]], ...]:
    return tuple((path, tuple(sorted(lines))) for path, lines in sorted(values.items()))
