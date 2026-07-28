from __future__ import annotations

from operamind.application.main_change_flow import (
    FLOW_STAGE_IDS,
    build_main_change_flow,
)


def _request() -> dict[str, object]:
    return {
        "change_request_id": "change-001",
        "project_id": "visiondemo",
        "analysis_case_id": "case-001",
        "artifact": {
            "input_mode": "natural_language",
            "requirement_text": "経費一覧に差戻し状態を追加する",
            "ambiguity_status": "clear",
            "ambiguities": [],
            "business_rules": [
                {"business_rule_id": "rule-001", "text": "差戻しを検索できる"}
            ],
        },
    }


def _test_plan() -> dict[str, object]:
    return {
        "test_plan_id": "test-plan-internal-001",
        "status": "ready",
        "test_cases": [
            {
                "test_case_id": "test-case-internal-001",
                "title": "差戻し状態で検索する",
                "level": "ui",
                "execution_mode": "browser",
                "preconditions": ["経費申請が差戻し状態で存在する"],
                "steps": ["経費一覧を開く", "差戻しを選択して検索する"],
                "expected_results": ["差戻し状態の申請だけが表示される"],
            }
        ],
    }


def _test_data_plan() -> dict[str, object]:
    return {
        "test_data_plan_id": "test-data-plan-internal-001",
        "status": "ready",
        "blocking_reasons": [],
        "generation_flows": [
            {
                "flow_id": "flow-internal-001",
                "title": "差戻し経費申請を作成して検索する",
                "steps": [
                    {
                        "step_id": "step-internal-001",
                        "sequence": 1,
                        "channel": "http",
                        "business_action": "差戻し状態の経費申請を作成する",
                        "inputs": {"employee": "employee-1"},
                        "output_bindings": [{"variable": "expense_id"}],
                        "postconditions": [
                            {
                                "assertion_id": "assertion-internal-001",
                                "observe_via": "response",
                                "subject": "status",
                                "operator": "equals",
                                "expected": "RETURNED",
                            }
                        ],
                    }
                ],
                "final_assertions": [
                    {
                        "assertion_id": "assertion-internal-002",
                        "observe_via": "ui",
                        "subject": "検索結果",
                        "operator": "count_equals",
                        "expected": 1,
                    }
                ],
                "cleanup_policy": "delete_after_run",
                "cleanup_steps": [
                    {
                        "step_id": "cleanup-internal-001",
                        "sequence": 1,
                        "channel": "http",
                        "business_action": "作成した経費申請を削除する",
                        "inputs": {"expense_id": "${expense_id}"},
                        "output_bindings": [],
                        "postconditions": [],
                    }
                ],
            }
        ],
    }


def test_projection_exposes_exactly_six_product_stages_and_no_internal_approval() -> None:
    result = build_main_change_flow(
        request=_request(),
        document_diff={
            "total": 1,
            "changes": [
                {
                    "change_id": "change-doc-1",
                    "change_type": "modified",
                    "summary": "状態説明を更新",
                }
            ],
        },
        workspace={
            "analysis_case_id": "case-001",
            "base_revision": "abc123",
            "impact_report": {"id": "impact-001", "status": "confirmed"},
            "confirmation": {"id": "confirmation-001"},
            "impact_artifact": {
                "ui_impact_status": "impacted",
                "items": [
                    {
                        "target_path": "src/ExpenseService.java",
                        "target_symbols": ["search"],
                        "recommended_action": "modify",
                        "test_file_refs": ["test/ExpenseServiceTest.java"],
                        "rationale": "状態条件を追加するため",
                    }
                ],
            },
            "edit_result": {
                "id": "edit-001",
                "status": "in_scope",
                "validation_mode": "committed",
                "tests_passed": True,
                "result_revision": "def456",
                "command_evidence_status": "verified",
            },
        },
        automation={"current_stage": "completed", "status": "completed"},
        copilot_task={
            "coding_task_id": "copilot-001",
            "state": "completed",
            "commands": [
                {"command_ref": "targeted-unit", "status": "passed", "exit_code": 0}
            ],
        },
        execution={
            "test_plan": _test_plan(),
            "test_data_plan": _test_data_plan(),
            "test_data_execution": {
                "status": "passed",
                "result": {
                    "cleanup_status": "passed",
                    "flow_results": [
                        {
                            "flow_id": "flow-internal-001",
                            "status": "passed",
                            "step_results": [
                                {
                                    "step_id": "step-internal-001",
                                    "status": "passed",
                                }
                            ],
                            "cleanup_results": [
                                {
                                    "step_id": "cleanup-internal-001",
                                    "status": "passed",
                                }
                            ],
                        }
                    ],
                },
            },
            "business_coverage": {"coverage_percent": 100},
            "changed_line_coverage": {"coverage_percent": 91.5},
            "change_closure": {
                "change_closure_result_id": "closure-001",
                "status": "passed",
                "ui_status": "passed",
                "blocking_reasons": [],
                "artifact_refs": ["edit-001", "ui-001"],
                "modified_paths": ["src/ExpenseService.java"],
                "test_results": [
                    {
                        "test_case_id": "test-case-internal-001",
                        "status": "passed",
                        "summary": "期待した差戻し申請を確認",
                    }
                ],
                "unresolved_items": [],
            },
            "screenshots": [
                {
                    "evidence_id": "screen-001",
                    "available": True,
                    "content_url": "/screen-001",
                }
            ],
        },
    )

    assert tuple(stage["stage_id"] for stage in result["stages"]) == FLOW_STAGE_IDS
    assert result["status"] == "completed"
    assert result["progress_percent"] == 100
    assert set(result) == {
        "change_request_id",
        "project_id",
        "status",
        "current_stage",
        "progress_percent",
        "stages",
        "blocking_reasons",
    }
    serialized = repr(result).lower()
    assert "approval_grant" not in serialized
    assert "orchestration_task" not in serialized
    assert "lease_token" not in serialized
    assert "analysis_case_id" not in serialized
    assert "impact_report_id" not in serialized
    assert "copilot_task_id" not in serialized
    assert "edit_result_id" not in serialized
    assert "closure_result_id" not in serialized
    assert "test-plan-internal-001" not in serialized
    assert "test-case-internal-001" not in serialized
    assert "flow-internal-001" not in serialized
    assert "step-internal-001" not in serialized
    assert result["stages"][2]["details"]["items"][0]["target_path"] == (
        "src/ExpenseService.java"
    )
    assert result["stages"][3]["details"]["commands"][0] == {
        "command_ref": "targeted-unit",
        "status": "passed",
        "exit_code": 0,
    }
    assert result["stages"][3]["details"]["test_cases"][0]["title"] == (
        "差戻し状態で検索する"
    )
    generation_flow = result["stages"][4]["details"]["generation_flows"][0]
    assert generation_flow["steps"][0]["output_variables"] == ["expense_id"]
    assert generation_flow["steps"][0]["assertions"][0]["expected"] == "RETURNED"
    assert generation_flow["cleanup_steps"][0]["status"] == "passed"
    assert result["stages"][5]["details"]["test_results"] == [
        {
            "title": "差戻し状態で検索する",
            "status": "passed",
            "summary": "期待した差戻し申請を確認",
        }
    ]


def test_document_stage_projects_field_level_before_and_after_values() -> None:
    result = build_main_change_flow(
        request=_request(),
        document_diff={
            "changes": [
                {
                    "change_id": "internal-change-id",
                    "stable_key": "internal-stable-key",
                    "domain": "expense",
                    "fact_type": "screen_field",
                    "change_type": "modified",
                    "summary": "状態表示を更新",
                    "before": {
                        "values": {"label": "承認済", "unchanged": "same"}
                    },
                    "after": {
                        "values": {"label": "差戻し", "unchanged": "same"}
                    },
                    "source_refs": ["設計書.xlsx#検索条件"],
                }
            ]
        },
        workspace=None,
        automation=None,
        copilot_task=None,
        execution=None,
    )

    change = result["stages"][1]["details"]["changes"][0]
    assert change["field_deltas"] == [
        {"field": "label", "before": "承認済", "after": "差戻し"}
    ]
    assert "change_id" not in change
    assert "stable_key" not in change


def test_projection_stops_at_document_generation_without_exposing_scheduler_state() -> None:
    result = build_main_change_flow(
        request=_request(),
        document_diff={"total": 0, "changes": []},
        workspace=None,
        automation={
            "current_stage": "document_generation",
            "status": "waiting",
            "next_action": "prepare_document_with_copilot",
        },
        copilot_task=None,
        execution=None,
    )

    assert result["status"] == "in_progress"
    assert result["current_stage"] == "document_change"
    assert result["progress_percent"] == 17
    document = result["stages"][1]
    assert document["status"] == "waiting"
    assert document["executor"] == "vscode_github_copilot"
    assert "RAG" in document["summary"]


def test_projection_surfaces_business_blockers_on_the_relevant_product_stage() -> None:
    result = build_main_change_flow(
        request=_request(),
        document_diff={"total": 1, "changes": [{"change_id": "doc-1"}]},
        workspace={
            "analysis_case_id": "case-001",
            "impact_report": {"id": "impact-001", "status": "blocked"},
            "edit_result": {"id": None, "status": None},
        },
        automation={
            "current_stage": "impact_analysis",
            "status": "blocked",
            "blocking_reason": "RAG index is not ready",
        },
        copilot_task=None,
        execution=None,
    )

    assert result["status"] == "blocked"
    assert result["current_stage"] == "code_scope"
    assert result["blocking_reasons"] == ["RAG index is not ready"]
    assert result["stages"][2]["blocking_reasons"] == ["RAG index is not ready"]


def test_document_diff_waits_for_internal_confirmation_before_code_scope() -> None:
    result = build_main_change_flow(
        request=_request(),
        document_diff={"total": 1, "changes": [{"change_id": "doc-1"}]},
        workspace=None,
        automation={
            "current_stage": "document_confirmation",
            "status": "running",
        },
        copilot_task=None,
        execution=None,
    )

    assert result["current_stage"] == "document_change"
    assert result["stages"][1]["status"] == "running"
    assert result["stages"][2]["status"] == "waiting"


def test_unconfirmed_impact_scope_is_not_projected_as_completed() -> None:
    result = build_main_change_flow(
        request=_request(),
        document_diff={"total": 1, "changes": [{"change_id": "doc-1"}]},
        workspace={
            "impact_report": {"id": "impact-001", "status": "awaiting_confirmation"},
            "confirmation": {"id": None},
            "edit_result": {"id": None, "status": None},
        },
        automation={
            "current_stage": "impact_confirmation",
            "status": "running",
        },
        copilot_task=None,
        execution=None,
    )

    assert result["current_stage"] == "code_scope"
    assert result["stages"][2]["status"] == "running"


def test_working_diff_keeps_compile_test_running_until_committed_evidence() -> None:
    result = build_main_change_flow(
        request=_request(),
        document_diff={"total": 1, "changes": [{"change_id": "doc-1"}]},
        workspace={
            "impact_report": {"id": "impact-001", "status": "confirmed"},
            "confirmation": {"id": "confirmation-001"},
            "edit_result": {
                "id": "edit-working",
                "status": "in_scope",
                "validation_mode": "working",
                "tests_passed": None,
                "command_evidence_status": "not_applicable",
            },
        },
        automation={"current_stage": "code_change", "status": "waiting"},
        copilot_task={"state": "in_progress", "commands": []},
        execution={"test_plan": _test_plan()},
    )

    assert result["current_stage"] == "compile_test"
    assert result["stages"][3]["status"] == "running"
