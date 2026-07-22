from datetime import UTC, datetime

import pytest

from operamind.commands.readiness import _observation_payload, _pytest_counts


def test_pytest_counts_reads_clean_summary() -> None:
    assert _pytest_counts("================ 312 passed in 8.4s ================") == {
        "passed": 312,
        "failed": 0,
        "skipped": 0,
        "unexpected": 0,
    }


def test_pytest_counts_exposes_non_passing_outcomes() -> None:
    assert _pytest_counts("9 passed, 1 xfailed, 2 deselected in 1s")["unexpected"] == 3


def test_pytest_counts_rejects_output_without_passes() -> None:
    with pytest.raises(ValueError, match="did not report"):
        _pytest_counts("1 failed in 0.1s")


def test_observation_payload_accepts_canonical_external_identity() -> None:
    payload = _observation_payload(
        gate_id="github_copilot_live",
        evidence_type="copilot_session",
        project_id="visiondemo",
        analysis_case_id="case-1",
        observed_at=datetime(2026, 7, 20, tzinfo=UTC),
        review_status="reviewed",
        reviewed_by=("reviewer:developer",),
        subject={"coding_task_id": "coding-task-1"},
        observation_id="copilot-session:receipt-1",
    )

    assert payload["observation_id"] == "copilot-session:receipt-1"
