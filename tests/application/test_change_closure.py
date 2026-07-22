from __future__ import annotations

import copy
from pathlib import Path

from operamind.application.change_closure import ChangeClosureEvaluator, ChangeClosureInput
from operamind.contracts import ContractCatalog

ROOT = Path(__file__).parents[2]


def test_passes_only_when_code_tests_data_coverage_and_ui_pass() -> None:
    result = _evaluator().evaluate(_input())

    assert result["status"] == "passed"
    assert result["ui_status"] == "passed"
    assert result["business_coverage_percent"] == 100
    assert result["unresolved_items"] == []
    assert {item["status"] for item in result["test_results"]} == {"passed"}
    assert "edit-result-001" in result["artifact_refs"]
    assert "test-data-result-001" in result["artifact_refs"]
    assert "ui-result-001" in result["artifact_refs"]


def test_blocks_when_committed_edit_result_is_missing() -> None:
    value = _input()
    result = _evaluator().evaluate(
        ChangeClosureInput(
            change_request=value.change_request,
            orchestration=value.orchestration,
            test_plan=value.test_plan,
            test_data_plan=value.test_data_plan,
            coverage_report=value.coverage_report,
            edit_result=None,
            test_data_result=value.test_data_result,
            ui_result=value.ui_result,
        )
    )

    assert result["status"] == "blocked"
    assert "Committed Edit Result is missing" in result["unresolved_items"]


def test_requires_reanalysis_for_out_of_scope_code() -> None:
    value = _input()
    edit = copy.deepcopy(value.edit_result)
    assert edit is not None
    edit["status"] = "out_of_scope"
    edit["out_of_scope_files"] = ["VisionDemo/src/main/java/Unexpected.java"]

    result = _evaluator().evaluate(_replace(value, edit_result=edit))

    assert result["status"] == "reanalysis_required"
    assert any("Unexpected.java" in item for item in result["unresolved_items"])


def test_fails_when_test_data_or_cleanup_fails() -> None:
    value = _input()
    data_result = copy.deepcopy(value.test_data_result)
    assert data_result is not None
    data_result["status"] = "failed"
    data_result["cleanup_status"] = "failed"
    data_result["flow_results"][0]["status"] = "failed"

    result = _evaluator().evaluate(_replace(value, test_data_result=data_result))

    assert result["status"] == "failed"
    assert "Test data cleanup failed" in result["unresolved_items"]


def _evaluator() -> ChangeClosureEvaluator:
    return ChangeClosureEvaluator(ContractCatalog.load(ROOT / "contracts"))


def _replace(value: ChangeClosureInput, **changes: object) -> ChangeClosureInput:
    fields: dict[str, object] = {
        "change_request": value.change_request,
        "orchestration": value.orchestration,
        "test_plan": value.test_plan,
        "test_data_plan": value.test_data_plan,
        "coverage_report": value.coverage_report,
        "edit_result": value.edit_result,
        "test_data_result": value.test_data_result,
        "ui_result": value.ui_result,
        "ui_test_case_refs": value.ui_test_case_refs,
    }
    fields.update(changes)
    return ChangeClosureInput(**fields)  # type: ignore[arg-type]


def _input() -> ChangeClosureInput:
    return ChangeClosureInput(
        change_request={
            "artifact_type": "ChangeRequest",
            "schema_version": "v1",
            "change_request_id": "change-001",
            "project_id": "visiondemo",
            "input_mode": "natural_language",
        },
        orchestration={
            "artifact_type": "ChangeOrchestrationPlan",
            "schema_version": "v1",
            "orchestration_id": "orchestration-001",
            "change_request_id": "change-001",
            "project_id": "visiondemo",
            "analysis_case_id": "case-001",
            "status": "ready",
            "structured_change_refs": ["change-fact-001"],
            "artifact_refs": {
                "acceptance_criteria_id": "acceptance-001",
                "test_plan_id": "test-plan-001",
                "test_data_plan_id": "test-data-plan-001",
                "coverage_report_id": "coverage-001",
            },
            "blocking_reasons": [],
        },
        test_plan={
            "artifact_type": "TestPlan",
            "schema_version": "v1",
            "test_plan_id": "test-plan-001",
            "change_request_id": "change-001",
            "project_id": "visiondemo",
            "status": "ready",
            "test_cases": [
                {
                    "test_case_id": "source-test",
                    "level": "source",
                },
                {
                    "test_case_id": "ui-test",
                    "level": "ui",
                },
            ],
        },
        test_data_plan={
            "artifact_type": "TestDataPlan",
            "schema_version": "v1",
            "test_data_plan_id": "test-data-plan-001",
            "test_plan_id": "test-plan-001",
            "project_id": "visiondemo",
            "status": "ready",
            "generation_flows": [
                {
                    "flow_id": "flow-001",
                    "test_case_refs": ["ui-test"],
                }
            ],
        },
        coverage_report={
            "artifact_type": "BusinessCoverageReport",
            "schema_version": "v1",
            "coverage_report_id": "coverage-001",
            "change_request_id": "change-001",
            "test_plan_id": "test-plan-001",
            "acceptance_criteria_id": "acceptance-001",
            "project_id": "visiondemo",
            "coverage_percent": 100,
            "status": "passed",
            "items": [
                {"business_rule_id": "rule-001", "status": "covered"}
            ],
        },
        edit_result={
            "edit_result_id": "edit-result-001",
            "project_id": "visiondemo",
            "analysis_case_id": "case-001",
            "validation_mode": "committed",
            "status": "in_scope",
            "changed_paths": ["VisionDemo/src/main/java/ExpenseService.java"],
            "out_of_scope_files": [],
            "test_result_refs": ["command-test-001"],
            "tests_passed": True,
            "command_evidence_status": "verified",
        },
        test_data_result={
            "artifact_type": "TestDataExecutionResult",
            "schema_version": "v1",
            "execution_result_id": "test-data-result-001",
            "test_data_plan_id": "test-data-plan-001",
            "project_id": "visiondemo",
            "status": "passed",
            "cleanup_status": "passed",
            "flow_results": [
                {
                    "flow_id": "flow-001",
                    "status": "passed",
                    "step_results": [
                        {"evidence_refs": ["data-step-evidence-001"]}
                    ],
                    "cleanup_results": [
                        {"evidence_refs": ["cleanup-evidence-001"]}
                    ],
                }
            ],
        },
        ui_result={
            "artifact_type": "UiVerificationResult",
            "schema_version": "v1",
            "verification_result_id": "ui-result-001",
            "analysis_case_id": "case-001",
            "status": "passed",
            "scenario_results": [
                {
                    "scenario_id": "ui-test",
                    "status": "passed",
                    "evidence_refs": ["ui-evidence-001"],
                    "summary": "Cross-screen row matched.",
                }
            ],
            "unresolved_impact_item_ids": [],
            "out_of_scope_files": [],
            "failure_reasons": [],
        },
        ui_test_case_refs=(),
    )


def test_maps_approved_ui_scenario_to_test_plan_case() -> None:
    value = _input()
    assert value.ui_result is not None
    value.ui_result["scenario_results"][0]["scenario_id"] = "approved-scenario"
    result = _evaluator().evaluate(
        _replace(
            value,
            ui_test_case_refs=(("approved-scenario", ("ui-test",)),),
        )
    )

    assert result["status"] == "passed"
