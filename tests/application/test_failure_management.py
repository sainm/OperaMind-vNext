from __future__ import annotations

from operamind.application.failure_management import build_failure_management


def test_failure_management_aggregates_all_five_categories() -> None:
    model = build_failure_management(
        test_data_plan={"blocking_reasons": []},
        test_data_execution={
            "run_id": "run-1",
            "result": {
                "status": "failed",
                "failure_reasons": ["flow/create: API returned 500"],
                "cleanup_status": "failed",
                "flow_results": [
                    {
                        "flow_id": "flow",
                        "step_results": [
                            {
                                "step_id": "create",
                                "status": "failed",
                                "failure_reason": "flow/create: API returned 500",
                            }
                        ],
                        "cleanup_results": [
                            {
                                "step_id": "delete",
                                "status": "failed",
                                "failure_reason": "flow/delete cleanup: API returned 503",
                            }
                        ],
                    }
                ],
            },
        },
        ui_result={
            "status": "failed",
            "failure_reasons": [],
            "scenario_results": [
                {
                    "scenario_id": "expense-search",
                    "status": "failed",
                    "failure_category": "business_assertion",
                    "summary": "期待する経費行が表示されない",
                }
            ],
        },
        coverage={
            "status": "failed",
            "coverage_percent": 50,
            "items": [
                {"business_rule_id": "rule-covered", "status": "covered"},
                {"business_rule_id": "rule-missing", "status": "uncovered"},
            ],
        },
        closure={
            "status": "blocked",
            "unresolved_items": ["UI verification is failed"],
        },
        controls={"can_recover": False, "can_rerun": True},
    )

    assert model["status"] == "attention_required"
    assert {value["category"] for value in model["failures"]} == {
        "test_data",
        "ui",
        "cleanup",
        "coverage",
        "closure",
    }
    assert model["actions"] == {
        "can_recover": False,
        "recover_run_id": None,
        "recovery_requires_reason": True,
        "can_rerun": True,
        "rerun_run_id": "run-1",
    }


def test_failure_management_only_exposes_recovery_when_server_allows_it() -> None:
    model = build_failure_management(
        test_data_plan={"blocking_reasons": []},
        test_data_execution={"run_id": "run-stale", "status": "running"},
        ui_result=None,
        coverage=None,
        closure=None,
        controls={"can_recover": True, "can_rerun": False},
    )

    assert model["status"] == "recovery_required"
    assert model["actions"]["recover_run_id"] == "run-stale"
    assert model["actions"]["rerun_run_id"] is None


def test_failure_management_reports_clear_only_when_no_canonical_failure_exists() -> None:
    model = build_failure_management(
        test_data_plan={"blocking_reasons": []},
        test_data_execution={
            "run_id": "run-passed",
            "result": {
                "status": "passed",
                "failure_reasons": [],
                "cleanup_status": "passed",
                "flow_results": [],
            },
        },
        ui_result={"status": "passed", "scenario_results": []},
        coverage={"status": "passed", "coverage_percent": 100, "items": []},
        closure={"status": "passed", "unresolved_items": []},
        controls={"can_recover": False, "can_rerun": True},
    )

    assert model["status"] == "clear"
    assert model["failure_count"] == 0
    assert model["failures"] == []


def test_missing_test_data_plan_is_not_presented_as_clear() -> None:
    model = build_failure_management(
        test_data_plan=None,
        test_data_execution=None,
        ui_result=None,
        coverage=None,
        closure=None,
        controls={
            "can_recover": False,
            "can_rerun": False,
            "blocking_reason": "TestDataPlan is missing",
        },
    )

    assert model["status"] == "attention_required"
    assert model["failure_count"] == 1
    assert model["failures"][0]["category"] == "test_data"
