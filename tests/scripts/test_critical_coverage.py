from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/check_critical_coverage.py"


def test_critical_coverage_gate_accepts_threshold(tmp_path: Path) -> None:
    policy, report = _write_inputs(tmp_path, total=80, file_percent=80)

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--config",
            str(policy),
            "--coverage-json",
            str(report),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "PASS total 80.00%" in completed.stdout
    assert "PASS approval src/approval.py 80.00%" in completed.stdout


def test_critical_coverage_gate_reports_total_file_and_missing_failures(
    tmp_path: Path,
) -> None:
    policy, report = _write_inputs(tmp_path, total=79.9, file_percent=79.9)
    payload = json.loads(policy.read_text(encoding="utf-8"))
    payload["capabilities"]["approval"].append("src/missing.py")
    policy.write_text(json.dumps(payload), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--config",
            str(policy),
            "--coverage-json",
            str(report),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "FAIL total 79.90%" in completed.stdout
    assert "src/missing.py missing" in completed.stdout
    assert "Coverage gate failed" in completed.stderr


def test_critical_coverage_gate_rejects_invalid_policy(tmp_path: Path) -> None:
    policy, report = _write_inputs(tmp_path, total=80, file_percent=80)
    policy.write_text("{}", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--config",
            str(policy),
            "--coverage-json",
            str(report),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "configuration error" in completed.stderr


def _write_inputs(
    root: Path, *, total: float, file_percent: float
) -> tuple[Path, Path]:
    policy = root / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "schema_version": "operamind-critical-coverage-v1",
                "overall_minimum_percent": 80,
                "file_minimum_percent": 80,
                "capabilities": {"approval": ["src/approval.py"]},
            }
        ),
        encoding="utf-8",
    )
    report = root / "coverage.json"
    report.write_text(
        json.dumps(
            {
                "totals": {"percent_covered": total},
                "files": {
                    "src/approval.py": {
                        "summary": {"percent_covered": file_percent}
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return policy, report
