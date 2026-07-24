from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from operamind.application.change_loop_case import ChangeLoopCase
from operamind.application.change_orchestration import (
    ChangeOrchestrationBlockedError,
    ChangeOrchestrationInput,
    ChangeOrchestrationPlanner,
)

ROOT = Path(__file__).parents[2]
CASE_ROOT = ROOT / "golden-dataset/cases/visiondemo-expense-status-filter-golden"


def test_canonical_orchestration_generates_scope_tests_data_coverage_and_ui() -> None:
    result = ChangeOrchestrationPlanner(repository_root=ROOT).plan(_input())

    assert result.orchestration["status"] == "ready"
    assert result.orchestration["reviewed_case_id"] == ("visiondemo-expense-status-filter-golden")
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
    assert result.test_data_plan["generation_flows"]
    assert len(result.orchestration["ui_scenarios"]) == 3


def test_orchestration_rejects_unreviewed_structured_change() -> None:
    value = _input()
    changes = list(value.structured_changes)
    changes[0] = deepcopy(changes[0])
    changes[0]["review_status"] = "needs_review"

    with pytest.raises(ChangeOrchestrationBlockedError, match="must be accepted"):
        ChangeOrchestrationPlanner(repository_root=ROOT).plan(
            ChangeOrchestrationInput(
                change_request=value.change_request,
                analysis_case_id=value.analysis_case_id,
                structured_changes=tuple(changes),
                accepted_structured_change_refs=frozenset(),
                impact_report=value.impact_report,
                impact_report_state=value.impact_report_state,
                impact_confirmation=value.impact_confirmation,
                reviewed_case=value.reviewed_case,
            )
        )


def test_orchestration_rejects_stale_reviewed_case_revision() -> None:
    value = _input()
    report = deepcopy(value.impact_report)
    report["repository_revision"] = "b" * 40

    with pytest.raises(ChangeOrchestrationBlockedError, match="revision differs"):
        ChangeOrchestrationPlanner(repository_root=ROOT).plan(
            ChangeOrchestrationInput(
                change_request=value.change_request,
                analysis_case_id=value.analysis_case_id,
                structured_changes=value.structured_changes,
                accepted_structured_change_refs=value.accepted_structured_change_refs,
                impact_report=report,
                impact_report_state=value.impact_report_state,
                impact_confirmation=value.impact_confirmation,
                reviewed_case=value.reviewed_case,
            )
        )


def _input() -> ChangeOrchestrationInput:
    request: dict[str, Any] = {
        "artifact_type": "ChangeRequest",
        "schema_version": "v1",
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
    return ChangeOrchestrationInput(
        change_request=request,
        analysis_case_id="case-expense-status",
        structured_changes=(change,),
        accepted_structured_change_refs=frozenset({"change-expense-status-filter"}),
        impact_report=report,
        impact_report_state="confirmed",
        impact_confirmation=confirmation,
        reviewed_case=ChangeLoopCase.load(CASE_ROOT),
    )
