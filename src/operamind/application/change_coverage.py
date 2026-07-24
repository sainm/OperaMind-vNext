"""Calculate fail-closed coverage for executable lines changed by one Edit Result."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

MINIMUM_CHANGED_LINE_COVERAGE_PERCENT = 80.0


@dataclass(frozen=True, slots=True)
class ChangedLineCoverageEvidence:
    """Normalized output from a coverage tool run by an approved command."""

    evidence_refs: tuple[str, ...]
    executable_lines: tuple[tuple[str, tuple[int, ...]], ...]
    covered_lines: tuple[tuple[str, tuple[int, ...]], ...]
    minimum_coverage_percent: float = MINIMUM_CHANGED_LINE_COVERAGE_PERCENT

    def __post_init__(self) -> None:
        if not MINIMUM_CHANGED_LINE_COVERAGE_PERCENT <= self.minimum_coverage_percent <= 100:
            raise ValueError("Changed-line coverage threshold must be between 80 and 100")
        if not self.evidence_refs or len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("Changed-line coverage evidence refs must be non-empty and unique")
        if any(not value.strip() for value in self.evidence_refs):
            raise ValueError("Changed-line coverage evidence refs must not be blank")
        executable = _line_map(self.executable_lines, "executable")
        covered = _line_map(self.covered_lines, "covered")
        if set(covered) - set(executable):
            raise ValueError("Covered-line paths must exist in executable-line evidence")
        for path, lines in covered.items():
            if lines - executable[path]:
                raise ValueError(f"Covered lines are not executable: {path}")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ChangedLineCoverageEvidence:
        return cls(
            evidence_refs=tuple(str(item) for item in value.get("evidence_refs", [])),
            executable_lines=_mapping_lines(value.get("executable_lines", {})),
            covered_lines=_mapping_lines(value.get("covered_lines", {})),
            minimum_coverage_percent=float(
                value.get(
                    "minimum_coverage_percent",
                    MINIMUM_CHANGED_LINE_COVERAGE_PERCENT,
                )
            ),
        )


def evaluate_changed_line_coverage(
    *,
    edit_result_id: str,
    project_id: str,
    base_repository_revision: str,
    result_repository_revision: str,
    changed_lines: tuple[tuple[str, tuple[int, ...]], ...],
    changed_paths: tuple[str, ...],
    evidence: ChangedLineCoverageEvidence | None,
) -> dict[str, Any]:
    """Intersect Git added/modified lines with tool-reported executable and covered lines."""

    git_lines = _line_map(changed_lines, "changed")
    source_paths = sorted(path for path in changed_paths if _is_source_path(path))
    source_with_lines = sorted(path for path in source_paths if git_lines.get(path))
    if not source_with_lines:
        return _report(
            edit_result_id=edit_result_id,
            project_id=project_id,
            base_repository_revision=base_repository_revision,
            result_repository_revision=result_repository_revision,
            threshold=(
                evidence.minimum_coverage_percent
                if evidence
                else MINIMUM_CHANGED_LINE_COVERAGE_PERCENT
            ),
            files=[],
            evidence_refs=list(evidence.evidence_refs) if evidence else [],
            status="not_required",
            blockers=[],
        )
    if evidence is None:
        files = [
            _file_result(path, git_lines[path], git_lines[path], set())
            for path in source_with_lines
        ]
        return _report(
            edit_result_id=edit_result_id,
            project_id=project_id,
            base_repository_revision=base_repository_revision,
            result_repository_revision=result_repository_revision,
            threshold=MINIMUM_CHANGED_LINE_COVERAGE_PERCENT,
            files=files,
            evidence_refs=[],
            status="missing",
            blockers=["Changed-line coverage evidence is missing"],
        )

    executable = _line_map(evidence.executable_lines, "executable")
    covered = _line_map(evidence.covered_lines, "covered")
    unexpected = sorted((set(executable) | set(covered)) - set(changed_paths))
    if unexpected:
        raise ValueError(f"Coverage evidence contains paths outside the Edit Result: {unexpected}")
    missing_paths = sorted(set(source_with_lines) - set(executable))
    files = [
        _file_result(
            path,
            git_lines[path],
            executable.get(path, git_lines[path] if path in missing_paths else set()),
            covered.get(path, set()),
        )
        for path in source_with_lines
    ]
    changed_count = sum(int(item["changed_line_count"]) for item in files)
    covered_count = sum(int(item["covered_changed_line_count"]) for item in files)
    percent = 100.0 if changed_count == 0 else covered_count * 100.0 / changed_count
    blockers = [
        f"Coverage evidence does not include changed source file: {path}" for path in missing_paths
    ]
    below_threshold = percent < evidence.minimum_coverage_percent
    if below_threshold:
        blockers.append(
            "Changed-line coverage: "
            f"{_number(percent)}% < {_number(evidence.minimum_coverage_percent)}%"
        )
        for item in files:
            blockers.extend(
                f"Uncovered changed line: {item['path']}:{line}"
                for line in item["uncovered_changed_lines"]
            )
    status = "passed" if not blockers else "failed"
    return _report(
        edit_result_id=edit_result_id,
        project_id=project_id,
        base_repository_revision=base_repository_revision,
        result_repository_revision=result_repository_revision,
        threshold=evidence.minimum_coverage_percent,
        files=files,
        evidence_refs=list(evidence.evidence_refs),
        status=status,
        blockers=blockers,
    )


def _file_result(
    path: str, changed: set[int], executable: set[int], covered: set[int]
) -> dict[str, Any]:
    changed_executable = changed & executable
    covered_changed = changed_executable & covered
    uncovered = changed_executable - covered
    return {
        "path": path,
        "changed_line_count": len(changed_executable),
        "covered_changed_line_count": len(covered_changed),
        "changed_lines": sorted(changed_executable),
        "covered_changed_lines": sorted(covered_changed),
        "uncovered_changed_lines": sorted(uncovered),
    }


def _report(
    *,
    edit_result_id: str,
    project_id: str,
    base_repository_revision: str,
    result_repository_revision: str,
    threshold: float,
    files: list[dict[str, Any]],
    evidence_refs: list[str],
    status: str,
    blockers: list[str],
) -> dict[str, Any]:
    changed_count = sum(int(item["changed_line_count"]) for item in files)
    covered_count = sum(int(item["covered_changed_line_count"]) for item in files)
    percent = 100.0 if changed_count == 0 else covered_count * 100.0 / changed_count
    material = json.dumps(
        {
            "edit_result_id": edit_result_id,
            "base": base_repository_revision,
            "result": result_repository_revision,
            "threshold": threshold,
            "files": files,
            "evidence_refs": evidence_refs,
            "status": status,
            "blocking_reasons": blockers,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return {
        "artifact_type": "ChangedLineCoverageReport",
        "schema_version": "v1",
        "changed_line_coverage_report_id": (
            f"changed-line-coverage-{hashlib.sha256(material).hexdigest()[:24]}"
        ),
        "edit_result_id": edit_result_id,
        "project_id": project_id,
        "base_repository_revision": base_repository_revision,
        "result_repository_revision": result_repository_revision,
        "minimum_coverage_percent": threshold,
        "changed_line_count": changed_count,
        "covered_changed_line_count": covered_count,
        "coverage_percent": percent,
        "files": files,
        "evidence_refs": sorted(evidence_refs),
        "status": status,
        "blocking_reasons": sorted(blockers),
    }


def _mapping_lines(value: object) -> tuple[tuple[str, tuple[int, ...]], ...]:
    if not isinstance(value, dict):
        raise ValueError("Coverage line maps must be objects")
    normalized: list[tuple[str, tuple[int, ...]]] = []
    for path, lines in sorted(value.items()):
        if not isinstance(lines, list):
            raise ValueError(f"Coverage lines for {path} must be an array")
        normalized.append((str(path), tuple(int(line) for line in lines)))
    return tuple(normalized)


def _line_map(values: tuple[tuple[str, tuple[int, ...]], ...], label: str) -> dict[str, set[int]]:
    result: dict[str, set[int]] = {}
    for raw_path, raw_lines in values:
        path = _safe_path(raw_path)
        if path in result:
            raise ValueError(f"Duplicate {label}-line path: {path}")
        lines = set(raw_lines)
        if len(lines) != len(raw_lines) or any(line < 1 for line in lines):
            raise ValueError(f"{label.title()} lines must be unique positive integers: {path}")
        result[path] = lines
    return result


def _safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError(f"Coverage path must be a safe repository-relative path: {value}")
    return path.as_posix()


def _is_source_path(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in {
        ".c",
        ".cc",
        ".cpp",
        ".cs",
        ".go",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".kts",
        ".php",
        ".py",
        ".rb",
        ".rs",
        ".scala",
        ".ts",
        ".tsx",
    }


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}".rstrip("0")
