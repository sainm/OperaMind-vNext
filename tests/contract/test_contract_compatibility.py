from __future__ import annotations

import json
from pathlib import Path

from operamind.contracts import project_change_closure_result

ROOT = Path(__file__).parents[2]


def test_legacy_change_closure_is_projected_as_stale_without_mutation() -> None:
    artifact = json.loads(
        (ROOT / "contracts/examples/change-closure-result.v1.example.json").read_text()
    )

    projected = project_change_closure_result(artifact)

    assert artifact["status"] == "passed"
    assert "changed_line_coverage_status" not in artifact
    assert projected["schema_version"] == "v1"
    assert projected["compatibility_status"] == "stale"
    assert projected["status"] == "blocked"
    assert projected["changed_line_coverage_status"] == "missing"
    assert projected["changed_line_coverage_percent"] == 0
    assert projected["unresolved_items"] == [
        "Legacy ChangeClosureResult v1 requires changed-line coverage re-evaluation"
    ]


def test_current_change_closure_projection_preserves_artifact() -> None:
    artifact = json.loads(
        (ROOT / "contracts/examples/change-closure-result.v2.example.json").read_text()
    )

    assert project_change_closure_result(artifact) is artifact
