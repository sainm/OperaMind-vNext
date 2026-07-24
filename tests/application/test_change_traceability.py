from __future__ import annotations

from operamind.application.change_traceability import build_change_traceability


def test_traceability_links_all_stages_and_reports_no_gaps() -> None:
    result = build_change_traceability(
        request=_request(),
        document_diff={
            "changes": [
                {
                    "change_id": "change-1",
                    "summary": "Status default changed",
                    "review_status": "accepted",
                }
            ]
        },
        case={
            "impact_report": {
                "impact_report_id": "impact-1",
                "items": [
                    {
                        "impact_item_id": "impact-item-1",
                        "structured_change_refs": ["change-1"],
                        "target_path": "src/ExpenseService.java",
                        "impact_level": "high",
                    }
                ],
            },
            "progress": {"edit_result": {"id": "edit-1", "status": "in_scope"}},
        },
        bundle=_bundle(),
        management={
            "test_data_execution": {
                "result": {"flow_results": [{"flow_id": "flow-1", "status": "passed"}]}
            },
            "change_closure": {
                "closure_result_id": "closure-1",
                "status": "passed",
                "test_results": [{"test_case_id": "case-1", "status": "passed"}],
            },
        },
    )

    assert result["summary"]["gap_count"] == 0
    kinds = {node["kind"] for node in result["nodes"]}
    assert {
        "変更要件",
        "設計変更",
        "影響項目",
        "影響コード",
        "検証基準",
        "Test Case",
        "テストデータ",
        "UI Scenario",
        "UI 検証結果",
        "業務カバレッジ",
        "コード変更結果",
        "Closure Result",
    } <= kinds
    assert result["nodes"][0]["kind"] == "変更要件"
    relations = {(edge["from"], edge["to"], edge["relation"]) for edge in result["edges"]}
    assert ("design:change-1", "impact:impact-item-1", "影響") in relations
    assert ("case:case-1", "data:flow-1", "データ") in relations
    assert ("closure:closure-1", "closure:closure-1", "クローズ根拠") not in relations


def test_traceability_identifies_missing_links_and_results() -> None:
    result = build_change_traceability(
        request=_request(),
        document_diff={"changes": []},
        case={"impact_report": None, "progress": {"edit_result": {"id": None}}},
        bundle=None,
        management={"change_closure": None},
    )

    codes = {gap["code"] for gap in result["gaps"]}
    assert {"design_change", "impact_report", "edit_result", "closure"} <= codes
    assert result["summary"]["critical_gap_count"] >= 3


def test_traceability_reports_dangling_artifact_references() -> None:
    bundle = _bundle()
    bundle["test_plan"]["test_cases"][0]["business_rule_refs"] = ["missing-rule"]
    result = build_change_traceability(
        request=_request(),
        document_diff={"changes": [{"change_id": "change-1"}]},
        case={
            "impact_report": {
                "items": [
                    {
                        "impact_item_id": "impact-item-1",
                        "structured_change_refs": ["missing-change"],
                    }
                ]
            },
            "progress": {"edit_result": {"id": None}},
        },
        bundle=bundle,
        management={"change_closure": None},
    )

    codes = [gap["code"] for gap in result["gaps"]]
    assert "impact_link" in codes
    assert "broken_reference" in codes
    assert any(gap["node_id"] == "rule:missing-rule" for gap in result["gaps"])


def _request() -> dict[str, object]:
    return {
        "change_request_id": "request-1",
        "project_id": "demo",
        "analysis_case_id": "case-analysis-1",
        "document_review": {"status": "confirmed"},
        "artifact": {
            "change_request_id": "request-1",
            "project_id": "demo",
            "business_rules": [{"business_rule_id": "rule-1", "text": "Status is normalized"}],
        },
    }


def _bundle() -> dict[str, object]:
    return {
        "orchestration": {
            "code_scope": [
                {
                    "impact_item_id": "impact-item-1",
                    "target_path": "src/ExpenseService.java",
                    "recommended_action": "modify",
                }
            ],
            "ui_scenarios": [
                {"scenario_id": "case-1", "title": "Expense list", "test_case_refs": ["case-1"]}
            ],
        },
        "acceptance_criteria": {
            "criteria": [
                {"criterion_id": "criterion-1", "subject": "status", "test_case_refs": ["case-1"]}
            ]
        },
        "test_plan": {
            "test_cases": [
                {
                    "test_case_id": "case-1",
                    "title": "Status search",
                    "level": "ui",
                    "business_rule_refs": ["rule-1"],
                    "acceptance_criteria_refs": ["criterion-1"],
                    "test_data_refs": ["data-1"],
                }
            ]
        },
        "test_data_plan": {
            "generation_flows": [
                {"flow_id": "flow-1", "title": "Create data", "test_case_refs": ["case-1"]}
            ]
        },
        "coverage_report": {
            "items": [
                {"business_rule_id": "rule-1", "status": "covered", "test_case_refs": ["case-1"]}
            ]
        },
    }
