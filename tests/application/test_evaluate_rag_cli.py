import json
from pathlib import Path

import pytest

from operamind.commands.evaluate_rag import main

PURPOSES = ("business_behavior", "precise_anchor", "acceptance_criteria")


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def expected_payload() -> dict[str, object]:
    return {
        "case_id": "case-1",
        "project_id": "project-1",
        "dataset_stage": "golden",
        "required_contexts": [
            {
                "document": "screen.xlsx",
                "locations": ["items!A2"],
                "reason": "Required behavior",
                "canonical_node_ids": ["node-required"],
            }
        ],
        "expected_query_concepts": ["required behavior"],
        "must_exclude_as_primary_context": ["unrelated behavior"],
        "canonical_id_status": "frozen",
        "review_status": "approved",
        "query_expectations": [
            {
                "query_purpose": purpose,
                "required_candidate_refs": ["node-required"],
                "irrelevant_candidate_refs": ["node-irrelevant"],
            }
            for purpose in PURPOSES
        ],
        "quality_thresholds": {
            "min_recall_at_5": 1.0,
            "min_recall_at_10": 1.0,
            "min_mrr": 1.0,
            "max_irrelevant_rate": 0.0,
            "max_cross_project_leaks": 0,
        },
    }


def observed_payload(*, project_id: str = "project-1") -> dict[str, object]:
    return {
        "case_id": "case-1",
        "project_id": "project-1",
        "query_results": [
            {
                "query_purpose": purpose,
                "candidates": [{"target_id": "node-required", "project_id": project_id}],
            }
            for purpose in PURPOSES
        ],
    }


def test_evaluate_rag_cli_passes_and_fails_frozen_gates(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    expected_path = tmp_path / "expected.json"
    observed_path = tmp_path / "observed.json"
    write_json(expected_path, expected_payload())
    write_json(observed_path, observed_payload())

    passed = main(
        [
            "--root",
            str(Path(__file__).parents[2]),
            "--expected",
            str(expected_path),
            "--observed",
            str(observed_path),
        ]
    )

    assert passed == 0
    report = json.loads(capsys.readouterr().out)
    assert report["passed"]
    assert report["metrics"]["recall_at_5"] == 1.0

    write_json(observed_path, observed_payload(project_id="other-project"))
    failed = main(
        [
            "--root",
            str(Path(__file__).parents[2]),
            "--expected",
            str(expected_path),
            "--observed",
            str(observed_path),
        ]
    )

    assert failed == 1
    report = json.loads(capsys.readouterr().out)
    assert not report["passed"]
    assert report["failures"] == ["cross_project_leaks"]
