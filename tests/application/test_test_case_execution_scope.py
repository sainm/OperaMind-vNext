from __future__ import annotations

from copy import deepcopy

from operamind.domain.test_case_execution_scope import (
    compare_test_case_execution_scope,
    compare_test_case_version_results,
)


def test_scope_comparison_reports_data_ui_and_execution_changes() -> None:
    source = _bundle()
    target = deepcopy(source)
    target["test_data_plan"]["data_sets"][0]["value"] = 5
    target["orchestration"]["ui_scenarios"][0]["expected_results"] = ["5 件"]
    target["test_plan"]["test_cases"][0]["expected_results"] = ["5 件"]
    target["test_plan"]["test_cases"][0]["execution_mode"] = "deterministic"

    comparison = compare_test_case_execution_scope(source, target)

    assert comparison.changed
    assert comparison.changed_dimensions == (
        "test_data",
        "ui_scenarios",
        "execution_scope",
    )
    assert comparison.source_scope_digest != comparison.target_scope_digest
    assert comparison.to_dict()["requires_confirmation"] is True


def test_scope_comparison_ignores_regenerated_artifact_ids() -> None:
    source = _bundle()
    target = deepcopy(source)
    target["orchestration"]["orchestration_id"] = "orchestration-v2"
    target["test_plan"]["test_plan_id"] = "test-plan-v2"
    target["test_data_plan"]["test_data_plan_id"] = "test-data-v2"
    target["test_data_plan"]["test_plan_id"] = "test-plan-v2"

    comparison = compare_test_case_execution_scope(source, target)

    assert not comparison.changed
    assert comparison.source_scope_digest == comparison.target_scope_digest


def test_version_result_comparison_exposes_old_and_new_evidence_delta() -> None:
    comparison = compare_test_case_version_results(
        source_orchestration_id="orchestration-v1",
        target_orchestration_id="orchestration-v2",
        source_run=_run("run-v1", "passed", 1),
        target_run=_run("run-v2", "failed", 2),
        source_closure=_closure("closure-v1", "passed", 1),
        target_closure=_closure("closure-v2", "failed", 0),
        source_coverage={"coverage_percent": 100},
        target_coverage={"coverage_percent": 100},
    )

    assert comparison["source"]["evidence_count"] == 1
    assert comparison["target"]["evidence_count"] == 2
    assert {item["field"] for item in comparison["deltas"]} >= {
        "run_status",
        "evidence_count",
        "closure_status",
        "passed_test_count",
    }


def _bundle() -> dict[str, object]:
    return {
        "orchestration": {
            "orchestration_id": "orchestration-v1",
            "repository_revision": "a" * 40,
            "code_scope": [{"target_path": "src/App.java"}],
            "ui_scenarios": [
                {
                    "scenario_id": "case-1",
                    "steps": ["一覧を開く"],
                    "expected_results": ["4 件"],
                }
            ],
        },
        "test_plan": {
            "test_plan_id": "test-plan-v1",
            "test_cases": [
                {
                    "test_case_id": "case-1",
                    "level": "ui",
                    "execution_mode": "browser",
                    "test_data_refs": ["data-1"],
                    "steps": ["一覧を開く"],
                    "expected_results": ["4 件"],
                }
            ],
        },
        "test_data_plan": {
            "test_data_plan_id": "test-data-v1",
            "test_plan_id": "test-plan-v1",
            "status": "ready",
            "data_sets": [{"test_data_id": "data-1", "value": 4}],
            "generation_flows": [{"flow_id": "flow-1"}],
            "blocking_reasons": [],
        },
    }


def _run(run_id: str, status: str, evidence_count: int) -> dict[str, object]:
    return {
        "run_id": run_id,
        "status": status,
        "result": {
            "cleanup_status": "passed",
            "evidence": [{"evidence_id": str(index)} for index in range(evidence_count)],
        },
    }


def _closure(
    closure_id: str, status: str, passed_count: int
) -> dict[str, object]:
    return {
        "closure_result_id": closure_id,
        "status": status,
        "ui_status": status,
        "test_results": [
            {"status": "passed" if index < passed_count else "failed"}
            for index in range(1)
        ],
    }
