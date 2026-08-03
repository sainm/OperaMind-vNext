from __future__ import annotations

import pytest

from operamind.application.change_coverage import (
    ChangedLineCoverageEvidence,
    evaluate_changed_line_coverage,
)


def test_calculates_coverage_only_for_changed_executable_lines() -> None:
    report = evaluate_changed_line_coverage(
        edit_result_id="edit-1",
        project_id="demo",
        base_repository_revision="base",
        result_repository_revision="result",
        changed_lines=(("src/service.py", (10, 11, 12, 13)),),
        changed_paths=("src/service.py", "README.md"),
        evidence=ChangedLineCoverageEvidence(
            evidence_refs=("command-coverage-1",),
            executable_lines=(("src/service.py", (10, 12, 13, 20)),),
            covered_lines=(("src/service.py", (10, 12, 20)),),
            minimum_coverage_percent=80,
        ),
    )

    assert report["changed_line_count"] == 3
    assert report["covered_changed_line_count"] == 2
    assert report["coverage_percent"] == pytest.approx(66.6666666667)
    assert report["status"] == "failed"
    assert report["files"][0]["uncovered_changed_lines"] == [13]
    assert "Uncovered changed line: src/service.py:13" in report["blocking_reasons"]


def test_passes_when_changed_line_coverage_meets_threshold() -> None:
    report = evaluate_changed_line_coverage(
        edit_result_id="edit-1",
        project_id="demo",
        base_repository_revision="base",
        result_repository_revision="result",
        changed_lines=(("src/service.ts", (4, 5, 6, 7, 8)),),
        changed_paths=("src/service.ts",),
        evidence=ChangedLineCoverageEvidence(
            evidence_refs=("command-coverage-1",),
            executable_lines=(("src/service.ts", (4, 5, 6, 7, 8)),),
            covered_lines=(("src/service.ts", (4, 5, 6, 7)),),
            minimum_coverage_percent=80,
        ),
    )

    assert report["coverage_percent"] == 80
    assert report["status"] == "passed"
    assert report["blocking_reasons"] == []


def test_missing_evidence_is_fail_closed_for_source_changes() -> None:
    report = evaluate_changed_line_coverage(
        edit_result_id="edit-1",
        project_id="demo",
        base_repository_revision="base",
        result_repository_revision="result",
        changed_lines=(("src/service.java", (20, 21)),),
        changed_paths=("src/service.java",),
        evidence=None,
    )

    assert report["status"] == "missing"
    assert report["changed_line_count"] == 2
    assert report["files"][0]["uncovered_changed_lines"] == [20, 21]
    assert report["blocking_reasons"] == ["Changed-line coverage evidence is missing"]


def test_document_only_change_does_not_require_line_coverage() -> None:
    report = evaluate_changed_line_coverage(
        edit_result_id="edit-1",
        project_id="demo",
        base_repository_revision="base",
        result_repository_revision="result",
        changed_lines=(("README.md", (1, 2)),),
        changed_paths=("README.md",),
        evidence=None,
    )

    assert report["status"] == "not_required"
    assert report["coverage_percent"] == 100


def test_test_sources_do_not_count_as_production_changed_line_coverage() -> None:
    report = evaluate_changed_line_coverage(
        edit_result_id="edit-1",
        project_id="demo",
        base_repository_revision="base",
        result_repository_revision="result",
        changed_lines=(
            ("src/test/java/com/example/ServiceTest.java", (10, 11)),
            ("src/main/resources/mapper/ServiceMapper.xml", (20,)),
        ),
        changed_paths=(
            "src/test/java/com/example/ServiceTest.java",
            "src/main/resources/mapper/ServiceMapper.xml",
        ),
        evidence=ChangedLineCoverageEvidence(
            evidence_refs=("command-coverage-1",),
            executable_lines=(),
            covered_lines=(),
        ),
    )

    assert report["status"] == "not_required"
    assert report["changed_line_count"] == 0
    assert report["coverage_percent"] == 100


def test_ignores_coverage_from_unchanged_files() -> None:
    report = evaluate_changed_line_coverage(
        edit_result_id="edit-1",
        project_id="demo",
        base_repository_revision="base",
        result_repository_revision="result",
        changed_lines=(("src/service.py", (1,)),),
        changed_paths=("src/service.py",),
        evidence=ChangedLineCoverageEvidence(
            evidence_refs=("command-coverage-1",),
            executable_lines=(("src/service.py", (1,)), ("src/other.py", (1,))),
            covered_lines=(("src/service.py", (1,)), ("src/other.py", (1,))),
        ),
    )

    assert report["status"] == "passed"
    assert [item["path"] for item in report["files"]] == ["src/service.py"]


def test_fails_when_one_changed_source_file_is_absent_from_coverage_evidence() -> None:
    report = evaluate_changed_line_coverage(
        edit_result_id="edit-1",
        project_id="demo",
        base_repository_revision="base",
        result_repository_revision="result",
        changed_lines=(
            ("src/covered.py", (1,)),
            ("src/missing.py", (1,)),
        ),
        changed_paths=("src/covered.py", "src/missing.py"),
        evidence=ChangedLineCoverageEvidence(
            evidence_refs=("command-coverage-1",),
            executable_lines=(("src/covered.py", (1,)),),
            covered_lines=(("src/covered.py", (1,)),),
        ),
    )

    assert report["status"] == "failed"
    assert (
        "Coverage evidence does not include changed source file: src/missing.py"
        in report["blocking_reasons"]
    )
    assert "Uncovered changed line: src/missing.py:1" in report["blocking_reasons"]


def test_rejects_a_caller_supplied_threshold_below_the_system_standard() -> None:
    with pytest.raises(ValueError, match="between 80 and 100"):
        ChangedLineCoverageEvidence(
            evidence_refs=("command-coverage-1",),
            executable_lines=(("src/service.py", (1,)),),
            covered_lines=(("src/service.py", (1,)),),
            minimum_coverage_percent=0,
        )
