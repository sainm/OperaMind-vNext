from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from operamind.application.change_orchestration import (
    ChangeOrchestrationBlockedError,
    ChangeOrchestrationInput,
    ChangeOrchestrationPlanner,
)

ROOT = Path(__file__).parents[2]


def test_canonical_orchestration_binds_copilot_plans_to_scope_and_coverage() -> None:
    result = ChangeOrchestrationPlanner(repository_root=ROOT).plan(_input())

    assert result.orchestration["status"] == "ready"
    assert result.orchestration["reviewed_case_id"] == "copilot-task-001"
    assert result.orchestration["code_scope"] == [
        {
            "impact_item_id": "impact-expense-service",
            "target_path": "VisionDemo/src/main/java/com/visiondemo/service/ExpenseService.java",
            "target_symbols": ["ExpenseService#search"],
            "recommended_action": "modify",
            "test_file_refs": [],
        }
    ]
    assert result.coverage_report["status"] == "passed"
    assert result.coverage_report["coverage_percent"] == 100
    assert {
        tuple(criterion["business_rule_refs"])
        for criterion in result.acceptance_criteria["criteria"]
    } == {("rule-expense-status-change",)}
    assert result.test_data_plan["status"] == "ready"
    assert len(result.orchestration["ui_scenarios"]) == 1


def test_orchestration_rejects_unreviewed_structured_change() -> None:
    value = _input()
    changes = list(value.structured_changes)
    changes[0] = deepcopy(changes[0])
    changes[0]["review_status"] = "needs_review"

    with pytest.raises(ChangeOrchestrationBlockedError, match="must be accepted"):
        ChangeOrchestrationPlanner(repository_root=ROOT).plan(
            replace(
                value,
                structured_changes=tuple(changes),
                accepted_structured_change_refs=frozenset(),
            )
        )


@pytest.mark.parametrize(
    "missing_field",
    ["copilot_coding_task_id", "generated_test_plan", "generated_test_data_plan"],
)
def test_orchestration_requires_complete_copilot_planning_outputs(
    missing_field: str,
) -> None:
    value = replace(_input(), **{missing_field: None})

    with pytest.raises(ChangeOrchestrationBlockedError, match="outputs are incomplete"):
        ChangeOrchestrationPlanner(repository_root=ROOT).plan(value)


def test_orchestration_blocks_incomplete_business_rule_coverage_before_review() -> None:
    value = _input()
    request = deepcopy(value.change_request)
    request["business_rules"].append(
        {
            "business_rule_id": "rule-status-options-remain",
            "text": "ステータス選択肢を維持する",
            "source_refs": [],
        }
    )
    impact = deepcopy(value.impact_report)
    impact["required_ui_scenario_refs"].append("expense-filter-status-options")

    with pytest.raises(
        ChangeOrchestrationBlockedError,
        match=r"Business coverage must be 100.*rule-status-options-remain",
    ):
        ChangeOrchestrationPlanner(repository_root=ROOT).plan(
            replace(value, change_request=request, impact_report=impact)
        )


def test_orchestration_rejects_unknown_business_rule_reference() -> None:
    value = _input()
    test_plan = deepcopy(value.generated_test_plan)
    assert test_plan is not None
    test_plan["test_cases"][0]["business_rule_refs"].append("unknown-rule")

    with pytest.raises(ChangeOrchestrationBlockedError, match=r"unknown.*unknown-rule"):
        ChangeOrchestrationPlanner(repository_root=ROOT).plan(
            replace(value, generated_test_plan=test_plan)
        )


def _input() -> ChangeOrchestrationInput:
    request: dict[str, Any] = {
        "artifact_type": "ChangeRequest",
        "schema_version": "v2",
        "plan_kind": "ui",
        "change_request_id": "visiondemo-web-control-plane-e2e",
        "project_id": "visiondemo",
        "input_mode": "documents",
        "source_document_ref": "file:///before.xlsx",
        "target_document_ref": "file:///after.xlsx",
        "business_rules": [
            {
                "business_rule_id": "rule-expense-status-change",
                "text": "初期値をすべてにし、差戻しを選択可能にする",
                "source_refs": [
                    "02_画面設計書_経費精算申請一覧.xlsx#画面項目一覧!G5",
                    "02_画面設計書_経費精算申請一覧.xlsx#画面項目一覧!I5",
                ],
            }
        ],
        "ambiguity_status": "clear",
        "confirmation_required": False,
        "ambiguities": [],
    }
    change = {
        "artifact_type": "StructuredChange",
        "schema_version": "v1",
        "change_id": "change-expense-status-filter",
        "project_id": "visiondemo",
        "source_snapshot_id": "before",
        "target_snapshot_id": "after",
        "stable_key": "screen_element:screen_expense_list/expense-search-status",
        "fact_type": "screen_element",
        "domain": "ui",
        "change_type": "modified",
        "before": {"fact_ref": "before", "values": {}, "source_refs": ["before"]},
        "after": {"fact_ref": "after", "values": {}, "source_refs": ["after"]},
        "summary": "Expense status filter changed",
        "source_refs": ["before", "after"],
        "confidence": "high",
        "review_status": "accepted",
    }
    report = {
        "artifact_type": "ImpactReport",
        "schema_version": "v1",
        "impact_report_id": "impact-expense-status",
        "analysis_case_id": "case-expense-status",
        "project_id": "visiondemo",
        "document_snapshot_id": "after",
        "context_package_id": "context-expense-status",
        "code_graph_snapshot_id": "graph-expense-status",
        "repository_revision": "ad23d0a7a54ce196c0ea6c41445e5f5492ae1ea6",
        "status": "awaiting_confirmation",
        "summary": "Expense search service requires normalization",
        "items": [
            {
                "impact_item_id": "impact-expense-service",
                "structured_change_refs": ["change-expense-status-filter"],
                "target_path": (
                    "VisionDemo/src/main/java/com/visiondemo/service/ExpenseService.java"
                ),
                "target_symbols": ["ExpenseService#search"],
                "impact_level": "high",
                "recommended_action": "modify",
                "evidence_refs": ["context-expense-status"],
                "graph_path_refs": [],
                "test_file_refs": [],
                "requires_confirmation": True,
            }
        ],
        "ui_impact_status": "impacted",
        "required_ui_scenario_refs": ["expense-filter-default-all"],
        "blocking_unknowns": [],
    }
    confirmation = {
        "artifact_type": "ImpactConfirmation",
        "schema_version": "v1",
        "confirmation_id": "confirmation-expense-status",
        "impact_report_id": "impact-expense-status",
        "confirmed_by": "conversation:user",
        "approved_item_ids": ["impact-expense-service"],
        "rejected_item_ids": [],
        "confirmed_at": "2026-07-18T00:00:00Z",
    }
    test_plan = {
        "artifact_type": "TestPlan",
        "schema_version": "v1",
        "test_plan_id": "test-plan-expense-status",
        "change_request_id": request["change_request_id"],
        "project_id": request["project_id"],
        "status": "ready",
        "test_cases": [
            {
                "test_case_id": "expense-filter-default-all",
                "title": "既定値ですべての経費を表示する",
                "level": "ui",
                "execution_mode": "browser",
                "business_rule_refs": ["rule-expense-status-change"],
                "acceptance_criteria_refs": ["criterion-expense-status"],
                "preconditions": ["既定データが存在する"],
                "steps": ["経費一覧を開く"],
                "step_ids": ["open-expense-list"],
                "expected_results": ["すべての経費が表示される"],
                "test_data_refs": ["expense-default-seed"],
            }
        ],
    }
    test_data_plan = {
        "artifact_type": "TestDataPlan",
        "schema_version": "v2",
        "test_data_plan_id": "test-data-expense-status",
        "test_plan_id": test_plan["test_plan_id"],
        "project_id": request["project_id"],
        "status": "ready",
        "data_sets": [
            {
                "test_data_id": "expense-default-seed",
                "test_case_refs": ["expense-filter-default-all"],
                "setup_actions": [
                    {
                        "action_id": "load-default-seed",
                        "action_type": "fixture",
                        "target": "classpath:data.sql",
                        "payload": {"expected_expense_count": 4},
                    }
                ],
                "cleanup_policy": "isolated_environment",
            }
        ],
        "generation_flows": [
            {
                "flow_id": "flow-expense-default-seed",
                "title": "既定の経費データを生成する",
                "test_data_refs": ["expense-default-seed"],
                "test_case_refs": ["expense-filter-default-all"],
                "steps": [
                    {
                        "step_id": "load-default-seed",
                        "sequence": 1,
                        "channel": "fixture",
                        "business_action": "既定データをロードする",
                        "target": "classpath:data.sql",
                        "inputs": {"expected_expense_count": 4},
                        "depends_on": [],
                        "output_bindings": [],
                        "postconditions": [
                            {
                                "assertion_id": "default-expense-count",
                                "observe_via": "fixture",
                                "subject": "expected_expense_count",
                                "operator": "equals",
                                "expected": 4,
                            }
                        ],
                    },
                    {
                        "step_id": "open-expense-list-action",
                        "sequence": 2,
                        "channel": "ui",
                        "business_action": "経費一覧を開く",
                        "test_step_refs": ["open-expense-list"],
                        "screen_ref": "expense-list",
                        "ui_action_ref": "open",
                        "playwright": {
                            "action": "goto",
                            "path": "/expense",
                            "mask_locators": [],
                            "observations": [
                                {
                                    "key": "expense_rows",
                                    "kind": "count",
                                    "locator": {"by": "css", "value": "tbody tr"},
                                }
                            ],
                        },
                        "inputs": {},
                        "depends_on": ["load-default-seed"],
                        "output_bindings": [],
                        "postconditions": [
                            {
                                "assertion_id": "expense-rows-visible",
                                "observe_via": "ui",
                                "subject": "expense_rows",
                                "operator": "count_equals",
                                "expected": 4,
                            }
                        ],
                    },
                ],
                "final_assertions": [
                    {
                        "assertion_id": "all-expenses-visible",
                        "observe_via": "ui",
                        "subject": "経費一覧",
                        "operator": "count_equals",
                        "expected": 4,
                    }
                ],
                "cleanup_policy": "isolated_environment",
                "cleanup_steps": [],
            }
        ],
        "blocking_reasons": [],
    }
    return ChangeOrchestrationInput(
        change_request=request,
        analysis_case_id="case-expense-status",
        structured_changes=(change,),
        accepted_structured_change_refs=frozenset({"change-expense-status-filter"}),
        impact_report=report,
        impact_report_state="confirmed",
        impact_confirmation=confirmation,
        copilot_coding_task_id="copilot-task-001",
        generated_test_plan=test_plan,
        generated_test_data_plan=test_data_plan,
    )
