from __future__ import annotations

import copy
from pathlib import Path

import pytest

from operamind.application.change_closure import ChangeClosureEvaluator, ChangeClosureInput
from operamind.contracts import ContractCatalog

ROOT = Path(__file__).parents[2]


def test_passes_only_when_code_tests_data_coverage_and_ui_pass() -> None:
    result = _evaluator().evaluate(_input())

    assert result["status"] == "passed"
    assert result["schema_version"] == "v2"
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
        "changed_line_coverage": value.changed_line_coverage,
        "ui_test_case_refs": value.ui_test_case_refs,
        "verification_only": value.verification_only,
        "workspace_evidence_current": value.workspace_evidence_current,
        "workspace_evidence_reason": value.workspace_evidence_reason,
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
            "business_rule_count": 1,
            "covered_rule_count": 1,
            "coverage_percent": 100,
            "status": "passed",
            "items": [
                {
                    "business_rule_id": "rule-001",
                    "test_case_refs": ["source-test"],
                    "criterion_refs": ["criterion-001"],
                    "status": "covered",
                }
            ],
        },
        edit_result={
            "edit_result_id": "edit-result-001",
            "project_id": "visiondemo",
            "analysis_case_id": "case-001",
            "validation_mode": "committed",
            "status": "in_scope",
            "base_repository_revision": "base-sha",
            "result_repository_revision": "result-sha",
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
                    "step_results": [{"evidence_refs": ["data-step-evidence-001"]}],
                    "cleanup_results": [{"evidence_refs": ["cleanup-evidence-001"]}],
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
        changed_line_coverage={
            "artifact_type": "ChangedLineCoverageReport",
            "schema_version": "v1",
            "changed_line_coverage_report_id": "changed-line-coverage-001",
            "edit_result_id": "edit-result-001",
            "project_id": "visiondemo",
            "base_repository_revision": "base-sha",
            "result_repository_revision": "result-sha",
            "minimum_coverage_percent": 80,
            "changed_line_count": 4,
            "covered_changed_line_count": 4,
            "coverage_percent": 100,
            "files": [],
            "evidence_refs": ["command-test-001"],
            "status": "passed",
            "blocking_reasons": [],
        },
        ui_test_case_refs=(),
    )


def test_blocks_when_changed_line_coverage_is_below_threshold() -> None:
    value = _input()
    report = copy.deepcopy(value.changed_line_coverage)
    assert report is not None
    report.update(
        {
            "covered_changed_line_count": 3,
            "coverage_percent": 75,
            "status": "failed",
            "blocking_reasons": ["Changed-line coverage: 75% < 80%"],
        }
    )

    result = _evaluator().evaluate(_replace(value, changed_line_coverage=report))

    assert result["status"] == "blocked"
    assert result["changed_line_coverage_percent"] == 75
    assert "Changed-line coverage: 75% < 80%" in result["unresolved_items"]


def test_verification_only_no_changes_can_pass() -> None:
    value = _input()
    edit = copy.deepcopy(value.edit_result)
    report = copy.deepcopy(value.changed_line_coverage)
    assert edit is not None and report is not None
    edit.update(
        {
            "status": "no_changes",
            "result_repository_revision": "base-sha",
            "changed_paths": [],
        }
    )
    report.update(
        {
            "result_repository_revision": "base-sha",
            "changed_line_count": 0,
            "covered_changed_line_count": 0,
            "coverage_percent": 100,
            "status": "not_required",
            "blocking_reasons": [],
        }
    )

    result = _evaluator().evaluate(
        _replace(
            value,
            edit_result=edit,
            changed_line_coverage=report,
            verification_only=True,
        )
    )

    assert result["status"] == "passed"
    assert result["modified_paths"] == []
    assert "Edit Result is not in scope" not in result["unresolved_items"]


def test_blocks_when_workspace_no_longer_matches_committed_evidence() -> None:
    result = _evaluator().evaluate(
        _replace(
            _input(),
            workspace_evidence_current=False,
            workspace_evidence_reason="Code workspace no longer matches committed Edit Result",
        )
    )

    assert result["status"] == "blocked"
    assert (
        "Code workspace no longer matches committed Edit Result"
        in result["unresolved_items"]
    )


def test_blocks_when_business_coverage_is_incomplete() -> None:
    value = _input()
    coverage = copy.deepcopy(value.coverage_report)
    coverage["coverage_percent"] = 0
    coverage["covered_rule_count"] = 0
    coverage["status"] = "failed"
    coverage["items"][0]["status"] = "uncovered"

    result = _evaluator().evaluate(_replace(value, coverage_report=coverage))

    assert result["status"] == "blocked"
    assert "Uncovered business rule: rule-001" in result["unresolved_items"]


def test_recalculates_business_coverage_and_blocks_inconsistent_summary() -> None:
    value = _input()
    coverage = copy.deepcopy(value.coverage_report)
    coverage["items"][0]["status"] = "uncovered"

    result = _evaluator().evaluate(_replace(value, coverage_report=coverage))

    assert result["status"] == "blocked"
    assert result["business_coverage_percent"] == 0
    assert "Uncovered business rule: rule-001" in result["unresolved_items"]
    assert "Business Coverage Report summary is inconsistent" in result["unresolved_items"]


def test_rejects_changed_line_coverage_from_another_commit() -> None:
    value = _input()
    report = copy.deepcopy(value.changed_line_coverage)
    assert report is not None
    report["result_repository_revision"] = "other-result-sha"

    with pytest.raises(ValueError, match="outside Closure scope"):
        _evaluator().evaluate(_replace(value, changed_line_coverage=report))


def test_working_edit_coverage_is_current_but_cannot_close() -> None:
    value = _input()
    edit = copy.deepcopy(value.edit_result)
    report = copy.deepcopy(value.changed_line_coverage)
    assert edit is not None and report is not None
    edit.update(
        {
            "validation_mode": "working",
            "result_repository_revision": None,
            "command_evidence_status": "not_applicable",
        }
    )
    report.update(
        {
            "result_repository_revision": "base-sha",
            "coverage_percent": 0,
            "covered_changed_line_count": 0,
            "status": "missing",
            "blocking_reasons": ["Changed-line coverage evidence is missing"],
        }
    )

    result = _evaluator().evaluate(_replace(value, edit_result=edit, changed_line_coverage=report))

    assert result["status"] == "blocked"
    assert "Edit Result is not committed" in result["unresolved_items"]
    assert "Changed-line coverage evidence is missing" in result["unresolved_items"]


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
