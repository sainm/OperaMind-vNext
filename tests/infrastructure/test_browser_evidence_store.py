import json

import pytest

from operamind.infrastructure.browser import LocalEvidenceStore


def test_local_evidence_store_sanitizes_json_and_replays_identical_content(tmp_path) -> None:
    store = LocalEvidenceStore(tmp_path)

    first = store.store_json(
        project_id="project-001",
        run_id="run-001",
        evidence_id="evidence-001",
        scenario_id="scenario-001",
        evidence_type="step_log",
        payload={
            "message": "Authorization: Bearer abc.secret token=top-secret",
            "token": "json-secret",
        },
    )
    second = store.store_json(
        project_id="project-001",
        run_id="run-001",
        evidence_id="evidence-001",
        scenario_id="scenario-001",
        evidence_type="step_log",
        payload={
            "message": "Authorization: Bearer abc.secret token=top-secret",
            "token": "json-secret",
        },
    )

    assert first == second
    stored = json.loads((tmp_path / "project-001/run-001/evidence-001.json").read_text())
    assert "abc.secret" not in stored["message"]
    assert "top-secret" not in stored["message"]
    assert stored["message"].count("[REDACTED]") >= 2
    assert stored["token"] == "[REDACTED]"


def test_local_evidence_store_rejects_path_escape_and_content_conflict(tmp_path) -> None:
    store = LocalEvidenceStore(tmp_path)

    with pytest.raises(ValueError, match="Unsafe"):
        store.store_json(
            project_id="../outside",
            run_id="run-001",
            evidence_id="evidence-001",
            scenario_id="scenario-001",
            evidence_type="step_log",
            payload={},
        )

    store.store_json(
        project_id="project-001",
        run_id="run-001",
        evidence_id="evidence-001",
        scenario_id="scenario-001",
        evidence_type="step_log",
        payload={"status": "first"},
    )
    with pytest.raises(ValueError, match="different content"):
        store.store_json(
            project_id="project-001",
            run_id="run-001",
            evidence_id="evidence-001",
            scenario_id="scenario-001",
            evidence_type="step_log",
            payload={"status": "changed"},
        )
