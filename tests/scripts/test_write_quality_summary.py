from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/write_quality_summary.py"


def test_quality_summary_reads_pytest_node_and_coverage_reports(tmp_path: Path) -> None:
    pytest_report = tmp_path / "pytest.xml"
    pytest_report.write_text(
        """<?xml version="1.0"?>
<testsuites><testsuite>
  <testcase name="passed" />
  <testcase name="failed"><failure /></testcase>
  <testcase name="error"><error /></testcase>
  <testcase name="skipped"><skipped /></testcase>
</testsuite></testsuites>
""",
        encoding="utf-8",
    )
    node_report = tmp_path / "node.xml"
    node_report.write_text(
        """<?xml version="1.0"?>
<testsuites><testcase name="one" /><testcase name="two" /></testsuites>
""",
        encoding="utf-8",
    )
    coverage = tmp_path / "coverage.json"
    coverage.write_text(
        json.dumps({"totals": {"percent_covered": 81.234}}),
        encoding="utf-8",
    )
    output = tmp_path / "summary.md"

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--junit",
            f"Python={pytest_report}",
            "--junit",
            f"VS Code Extension={node_report}",
            "--coverage-json",
            str(coverage),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    summary = output.read_text(encoding="utf-8")
    assert "| Python | 1 | 1 | 1 | 1 | 4 |" in summary
    assert "| VS Code Extension | 2 | 0 | 0 | 0 | 2 |" in summary
    assert "Statement coverage: **81.23%**" in summary


def test_quality_summary_can_report_missing_artifacts_without_masking_job_failure(
    tmp_path: Path,
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--junit",
            f"Python={tmp_path / 'missing.xml'}",
            "--allow-missing",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "未生成の Report" in completed.stdout
    assert "Python:" in completed.stdout


def test_quality_summary_rejects_an_invalid_junit_argument() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--junit", "missing-separator"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "LABEL=PATH" in completed.stderr
