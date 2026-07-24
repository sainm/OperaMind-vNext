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
            "message": (
                "Authorization: Bearer abc.secret token=top-secret "
                "access_token=access-secret clientSecret=client-secret "
                "api-key=api-secret"
            ),
            "token": "json-secret",
            "access_token": "json-access-secret",
            "clientSecret": "json-client-secret",
            "api-key": "json-api-secret",
            "profile": {"refreshToken": "json-refresh-secret"},
        },
    )
    second = store.store_json(
        project_id="project-001",
        run_id="run-001",
        evidence_id="evidence-001",
        scenario_id="scenario-001",
        evidence_type="step_log",
        payload={
            "message": (
                "Authorization: Bearer abc.secret token=top-secret "
                "access_token=access-secret clientSecret=client-secret "
                "api-key=api-secret"
            ),
            "token": "json-secret",
            "access_token": "json-access-secret",
            "clientSecret": "json-client-secret",
            "api-key": "json-api-secret",
            "profile": {"refreshToken": "json-refresh-secret"},
        },
    )

    assert first == second
    stored = json.loads((tmp_path / "project-001/run-001/evidence-001.json").read_text())
    assert "abc.secret" not in stored["message"]
    assert "top-secret" not in stored["message"]
    assert "access-secret" not in stored["message"]
    assert "client-secret" not in stored["message"]
    assert "api-secret" not in stored["message"]
    assert stored["message"].count("[REDACTED]") >= 5
    assert stored["token"] == "[REDACTED]"
    assert stored["access_token"] == "[REDACTED]"
    assert stored["clientSecret"] == "[REDACTED]"
    assert stored["api-key"] == "[REDACTED]"
    assert stored["profile"]["refreshToken"] == "[REDACTED]"


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
