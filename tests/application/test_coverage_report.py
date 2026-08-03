import hashlib
import json
from pathlib import Path

import pytest

from operamind.application.coverage_report import load_coverage_report


def test_loads_coverage_py_json_and_verifies_report_digest(tmp_path: Path) -> None:
    source = tmp_path / "src" / "service.py"
    source.parent.mkdir()
    source.write_text("value = 1\nvalue += 1\n", encoding="utf-8")
    report = tmp_path / "build" / "coverage.json"
    report.parent.mkdir()
    content = json.dumps(
        {
            "files": {
                str(source): {
                    "executed_lines": [1],
                    "missing_lines": [2],
                }
            }
        }
    ).encode()
    report.write_bytes(content)

    evidence = load_coverage_report(
        workspace_root=tmp_path,
        report_path="build/coverage.json",
        report_format="coverage_py_json",
        expected_digest=hashlib.sha256(content).hexdigest(),
        evidence_ref="coverage-command-1",
    )

    assert evidence.executable_lines == (("src/service.py", (1, 2)),)
    assert evidence.covered_lines == (("src/service.py", (1,)),)

    report.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="changed after the approved command"):
        load_coverage_report(
            workspace_root=tmp_path,
            report_path="build/coverage.json",
            report_format="coverage_py_json",
            expected_digest=hashlib.sha256(content).hexdigest(),
            evidence_ref="coverage-command-1",
        )


def test_loads_lcov_report(tmp_path: Path) -> None:
    source = tmp_path / "src" / "main.js"
    source.parent.mkdir()
    source.write_text("one();\ntwo();\n", encoding="utf-8")
    report = tmp_path / "coverage.lcov"
    content = f"SF:{source}\nDA:1,1\nDA:2,0\nend_of_record\n".encode()
    report.write_bytes(content)

    evidence = load_coverage_report(
        workspace_root=tmp_path,
        report_path="coverage.lcov",
        report_format="lcov",
        expected_digest=hashlib.sha256(content).hexdigest(),
        evidence_ref="coverage-command-1",
    )

    assert evidence.executable_lines == (("src/main.js", (1, 2)),)
    assert evidence.covered_lines == (("src/main.js", (1,)),)


def test_loads_jacoco_xml_by_unique_source_suffix(tmp_path: Path) -> None:
    source = tmp_path / "src" / "main" / "java" / "example" / "Service.java"
    source.parent.mkdir(parents=True)
    source.write_text("package example;\nclass Service {}\n", encoding="utf-8")
    report = tmp_path / "jacoco.xml"
    content = (
        b'<report name="demo"><package name="example">'
        b'<sourcefile name="Service.java">'
        b'<line nr="1" mi="0" ci="1"/><line nr="2" mi="1" ci="0"/>'
        b"</sourcefile></package></report>"
    )
    report.write_bytes(content)

    evidence = load_coverage_report(
        workspace_root=tmp_path,
        report_path="jacoco.xml",
        report_format="jacoco_xml",
        expected_digest=hashlib.sha256(content).hexdigest(),
        evidence_ref="coverage-command-1",
    )

    assert evidence.executable_lines == (("src/main/java/example/Service.java", (1, 2)),)
    assert evidence.covered_lines == (("src/main/java/example/Service.java", (1,)),)
