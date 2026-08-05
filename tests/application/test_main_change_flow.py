from __future__ import annotations

import pytest

from operamind.application.main_change_flow import (
    FLOW_STAGE_IDS,
    _ai_execution_source,
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
            "business_rules": [{"business_rule_id": "rule-001", "text": "差戻しを検索できる"}],
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
            "code_graph_artifact": {
                "files": [
                    {
                        "file_id": "file-service",
                        "path": "src/ExpenseService.java",
                        "language": "java",
                        "role": "production",
                        "symbols": [{"symbol_id": "symbol-search", "name": "search"}],
                    },
                    {
                        "file_id": "file-repository",
                        "path": "src/ExpenseRepository.java",
                        "language": "java",
                        "role": "production",
                        "symbols": [{"symbol_id": "symbol-find", "name": "findByStatus"}],
                    },
                    {
                        "file_id": "file-test",
                        "path": "test/ExpenseServiceTest.java",
                        "language": "java",
                        "role": "test",
                        "symbols": [{"symbol_id": "symbol-test", "name": "searchReturned"}],
                    },
                ],
                "edges": [
                    {
                        "edge_type": "calls",
                        "from_ref": "symbol-search",
                        "to_ref": "symbol-find",
                        "resolution_status": "resolved",
                        "source_location": {
                            "path": "src/ExpenseService.java",
                            "start_line": 20,
                        },
                    },
                    {
                        "edge_type": "calls",
                        "from_ref": "symbol-search",
                        "to_ref": "symbol-find",
                        "resolution_status": "resolved",
                        "source_location": {
                            "path": "src/ExpenseService.java",
                            "start_line": 24,
                        },
                    },
                    {
                        "edge_type": "tests",
                        "from_ref": "symbol-test",
                        "to_ref": "symbol-search",
                        "resolution_status": "resolved",
                        "source_location": {
                            "path": "test/ExpenseServiceTest.java",
                            "start_line": 15,
                        },
                    },
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
            "commands": [{"command_ref": "targeted-unit", "status": "passed", "exit_code": 0}],
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
                    "data_bindings": [
                        {
                            "test_data_id": "expense-returned-data",
                            "binding_mode": "generated",
                            "identity_provider_type": "database",
                            "identity_provider_ref": "database.v1",
                            "primary_key": {"name": "id", "value": 42},
                            "business_unique_keys": [
                                {"name": "expense_number", "value": "EXP-042"}
                            ],
                            "screen_key": {
                                "name": "expense_number",
                                "value": "EXP-042",
                            },
                            "screen_identity_values": [
                                {"name": "expense_number", "value": "EXP-042"}
                            ],
                            "screen_locator": {
                                "by": "css",
                                "value": "[data-expense-number='EXP-042']",
                                "exact": True,
                            },
                            "record_scope_locator": {
                                "by": "css",
                                "value": "[data-expense-number='EXP-042']",
                                "exact": True,
                            },
                            "identity_observations": {
                                "business_unique_keys": [
                                    {
                                        "name": "expense_number",
                                        "kind": "attribute",
                                        "attribute_name": "data-observed-expense-number",
                                    }
                                ],
                                "screen_key": {
                                    "name": "expense_number",
                                    "kind": "attribute",
                                    "attribute_name": "data-observed-expense-number",
                                },
                            },
                            "identity_digest": "c" * 64,
                            "match_count": 1,
                            "frozen_at": "2026-08-04T00:00:00Z",
                            "content_digest": "a" * 64,
                            "evidence_ref": "artifact://result/data-binding/expense-returned-data",
                        }
                    ],
                    "data_coverage": {
                        "status": "passed",
                        "coverage_percent": 100,
                        "proofs": [
                            {
                                "condition_id": "expense-returned-status",
                                "criterion_ref": "criterion-returned",
                                "test_case_ref": "expense-returned-ui",
                                "test_data_id": "expense-returned-data",
                                "condition_kind": "status",
                                "path": "rows[0].status",
                                "operator": "equals",
                                "expected": "RETURNED",
                                "actual": "RETURNED",
                                "status": "passed",
                                "content_digest": "b" * 64,
                                "evidence_ref": "artifact://result/data-coverage/status",
                            }
                        ],
                    },
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
    assert result["stages"][2]["details"]["items"][0]["target_path"] == ("src/ExpenseService.java")
    impact_graph = result["stages"][2]["details"]["impact_graph"]
    assert [node["path"] for node in impact_graph["nodes"]] == [
        "src/ExpenseService.java",
        "test/ExpenseServiceTest.java",
        "src/ExpenseRepository.java",
    ]
    assert {edge["relation"] for edge in impact_graph["edges"]} == {
        "calls",
        "tests",
    }
    assert impact_graph["relation_count"] == 2
    assert impact_graph["nodes"][0]["rationale"] == "状態条件を追加するため"
    assert impact_graph["nodes"][0]["related_tests"] == ["test/ExpenseServiceTest.java"]
    assert result["stages"][3]["details"]["commands"][0] == {
        "command_ref": "targeted-unit",
        "status": "passed",
        "exit_code": 0,
    }
    assert result["stages"][4]["details"]["ui_test_cases"][0]["title"] == ("差戻し状態で検索する")
    generation_flow = result["stages"][4]["details"]["generation_flows"][0]
    assert generation_flow["steps"][0]["output_variables"] == ["expense_id"]
    assert generation_flow["steps"][0]["assertions"][0]["expected"] == "RETURNED"
    assert generation_flow["cleanup_steps"][0]["status"] == "passed"
    assert result["stages"][4]["details"]["data_bindings"] == [
        {
            "test_data_id": "expense-returned-data",
            "binding_mode": "generated",
            "identity_provider_type": "database",
            "business_unique_keys": [
                {"name": "expense_number", "value": "EXP-042"}
            ],
            "screen_identity_values": [
                {"name": "expense_number", "value": "EXP-042"}
            ],
            "match_count": 1,
            "frozen_at": "2026-08-04T00:00:00Z",
        }
    ]
    assert result["stages"][4]["details"]["data_coverage_status"] == "passed"
    assert result["stages"][4]["details"]["data_coverage_percent"] == 100
    assert result["stages"][4]["details"]["data_coverage_proofs"][0] == {
        "condition_id": "expense-returned-status",
        "criterion_ref": "criterion-returned",
        "test_case_ref": "expense-returned-ui",
        "test_data_id": "expense-returned-data",
        "condition_kind": "status",
        "path": "rows[0].status",
        "operator": "equals",
        "expected": "RETURNED",
        "actual": "RETURNED",
        "status": "passed",
        "failure_reason": None,
        "content_digest": "b" * 64,
        "evidence_ref": "artifact://result/data-coverage/status",
    }
    assert result["stages"][5]["details"]["test_results"] == [
        {
            "title": "差戻し状態で検索する",
            "status": "passed",
            "summary": "期待した差戻し申請を確認",
        }
    ]


def test_ui_confirmation_exposes_business_coverage_with_rule_text() -> None:
    result = build_main_change_flow(
        request=_request(),
        document_diff={"total": 1, "changes": [{"change_id": "doc-1"}]},
        workspace={
            "impact_report": {"id": "impact-001", "status": "confirmed"},
            "confirmation": {"id": "confirmation-001"},
            "edit_result": {
                "id": "edit-001",
                "status": "in_scope",
                "validation_mode": "committed",
                "tests_passed": True,
                "command_evidence_status": "verified",
                "changed_line_coverage_status": "passed",
            },
        },
        automation={"current_stage": "test_plan_confirmation", "status": "waiting"},
        copilot_task={"state": "completed", "commands": []},
        execution={
            "test_plan": _test_plan(),
            "test_data_plan": _test_data_plan(),
            "business_coverage": {
                "status": "failed",
                "coverage_percent": 0,
                "items": [
                    {
                        "business_rule_id": "rule-001",
                        "test_case_refs": [],
                        "criterion_refs": [],
                        "status": "uncovered",
                    }
                ],
            },
        },
    )

    details = result["stages"][4]["details"]
    assert result["status"] == "blocked"
    assert result["current_stage"] == "ui_validation"
    assert result["stages"][4]["status"] == "blocked"
    assert result["stages"][4]["blocking_reasons"] == [
        "業務要件カバレッジが 100% ではないため、TestPlan を Copilot に返却します。"
    ]
    assert details["confirmation"] is None
    assert details["business_coverage_status"] == "failed"
    assert details["business_coverage_percent"] == 0
    assert details["business_coverage_items"] == [
        {
            "text": "差戻しを検索できる",
            "status": "uncovered",
            "test_case_count": 0,
            "criterion_count": 0,
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
                    "before": {"values": {"label": "承認済", "unchanged": "same"}},
                    "after": {"values": {"label": "差戻し", "unchanged": "same"}},
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
    assert change["field_deltas"] == [{"field": "label", "before": "承認済", "after": "差戻し"}]
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


def test_projection_labels_codex_fallback_without_forging_copilot_evidence() -> None:
    result = build_main_change_flow(
        request=_request(),
        document_diff={"total": 1, "changes": [{"change_id": "doc-1"}]},
        workspace={
            "impact_report": {"id": "impact-001", "status": "confirmed"},
            "confirmation": {"id": "confirmation-001"},
            "edit_packet": {"editable_files": []},
            "edit_result": {
                "id": "edit-001",
                "status": "no_changes",
                "validation_mode": "committed",
                "tests_passed": True,
                "command_evidence_status": "verified",
            },
        },
        automation={"current_stage": "test_plan_confirmation", "status": "waiting"},
        copilot_task={
            "state": "completed",
            "claimed_by": "codex-fallback-r4",
            "accepted_by": "codex:fallback",
            "commands": [],
            "events": [{"actor": "codex:fallback"}],
        },
        execution={"test_plan": _test_plan(), "test_data_plan": _test_data_plan()},
    )

    document = result["stages"][1]
    compile_test = result["stages"][3]
    assert document["executor"] == "codex_fallback"
    assert document["details"]["ai_source"] == "Codex fallback"
    assert "Codex fallback" in document["summary"]
    assert "GitHub Copilot" not in document["summary"]
    assert compile_test["executor"] == "codex_fallback"
    assert compile_test["details"]["ai_source"] == "Codex fallback"


def test_ai_source_uses_accepted_executor_instead_of_later_audit_actor() -> None:
    assert _ai_execution_source(
        {
            "accepted_by": "vscode:github-copilot",
            "events": [
                {"event_type": "accepted", "actor": "vscode:github-copilot"},
                {"event_type": "context_read", "actor": "codex:auditor"},
            ],
        }
    ) == ("vscode_github_copilot", "VS Code GitHub Copilot")


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
            "next_action": "resolve_blocker",
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


def test_persisted_state_machine_prevents_web_from_displaying_a_future_stage() -> None:
    result = build_main_change_flow(
        request=_request(),
        document_diff={"total": 1, "changes": [{"change_id": "doc-1"}]},
        workspace={
            "impact_report": {"id": "impact-001", "status": "confirmed"},
            "confirmation": {"id": "confirmation-001"},
            "edit_result": {
                "id": "edit-committed",
                "status": "in_scope",
                "validation_mode": "committed",
                "tests_passed": True,
                "command_evidence_status": "verified",
            },
        },
        automation={
            "current_stage": "code_change",
            "status": "waiting",
            "next_action": "apply_code_change_with_copilot",
        },
        copilot_task={"state": "completed", "commands": []},
        execution={"test_plan": _test_plan(), "test_data_plan": _test_data_plan()},
    )

    assert result["current_stage"] == "compile_test"
    assert result["stages"][3]["status"] == "waiting"
    assert result["stages"][4]["status"] == "waiting"


def test_state_machine_blocker_is_projected_even_without_stage_specific_evidence() -> None:
    result = build_main_change_flow(
        request=_request(),
        document_diff={"total": 1, "changes": [{"change_id": "doc-1"}]},
        workspace={
            "impact_report": {"id": "impact-001", "status": "confirmed"},
            "confirmation": {"id": "confirmation-001"},
            "edit_result": {"id": None, "status": None},
        },
        automation={
            "current_stage": "execution_approval",
            "status": "blocked",
            "next_action": "resolve_blocker",
            "blocking_reason": "実行範囲を作成できません。",
        },
        copilot_task={"state": "pending", "commands": []},
        execution=None,
    )

    assert result["status"] == "blocked"
    assert result["current_stage"] == "compile_test"
    assert result["stages"][3]["blocking_reasons"] == ["実行範囲を作成できません。"]


def test_verification_only_no_change_result_completes_compile_test() -> None:
    result = build_main_change_flow(
        request=_request(),
        document_diff={"total": 1, "changes": [{"change_id": "doc-1"}]},
        workspace={
            "impact_report": {"id": "impact-001", "status": "confirmed"},
            "confirmation": {"id": "confirmation-001"},
            "edit_packet": {
                "id": "packet-001",
                "status": "superseded",
                "editable_files": [],
            },
            "edit_result": {
                "id": "edit-verified",
                "status": "no_changes",
                "validation_mode": "committed",
                "tests_passed": True,
                "command_evidence_status": "verified",
            },
        },
        automation={"current_stage": "test_plan_confirmation", "status": "running"},
        copilot_task={"state": "completed", "commands": []},
        execution={"test_plan": _test_plan(), "test_data_plan": _test_data_plan()},
    )

    assert result["stages"][3]["status"] == "completed"


def test_future_stale_copilot_failure_does_not_override_current_confirmation() -> None:
    result = build_main_change_flow(
        request=_request(),
        document_diff={"total": 0, "changes": []},
        workspace=None,
        automation={
            "current_stage": "requirement_confirmation",
            "status": "waiting",
            "next_action": "confirm_requirement",
            "pending_confirmation": {
                "checkpoint": "requirement",
                "subject_digest": "a" * 64,
                "stage_label": "変更要件の確認",
                "message": "変更要件を確認してください。",
            },
        },
        copilot_task={"state": "cancelled", "commands": []},
        execution=None,
    )

    assert result["status"] == "in_progress"
    assert result["current_stage"] == "requirement"
    assert result["stages"][3]["status"] == "waiting"
    assert result["stages"][3]["blocking_reasons"] == []


@pytest.mark.parametrize(
    ("automation_stage", "expected_stage", "stage_index"),
    [
        ("test_plan_confirmation", "ui_validation", 4),
        ("ui_test_confirmation", "ui_validation", 4),
        ("final_report_confirmation", "final_report", 5),
    ],
)
def test_rejected_confirmation_blocks_the_visible_product_stage(
    automation_stage: str, expected_stage: str, stage_index: int
) -> None:
    result = build_main_change_flow(
        request=_request(),
        document_diff={"total": 1, "changes": [{"change_id": "doc-1"}]},
        workspace={
            "impact_report": {"id": "impact-001", "status": "confirmed"},
            "confirmation": {"id": "confirmation-001"},
            "edit_result": {
                "id": "edit-001",
                "status": "in_scope",
                "validation_mode": "committed",
                "tests_passed": True,
                "command_evidence_status": "verified",
            },
        },
        automation={
            "current_stage": automation_stage,
            "status": "blocked",
            "next_action": "resolve_blocker",
            "blocking_reason": "ユーザーにより差し戻されました。",
        },
        copilot_task={"state": "completed", "commands": []},
        execution={
            "test_plan": _test_plan(),
            "test_data_plan": _test_data_plan(),
            "test_data_execution": {
                "status": "passed",
                "result": {
                    "data_coverage": {
                        "status": "passed",
                        "coverage_percent": 100,
                        "proofs": [],
                    }
                },
            },
            "business_coverage": {"coverage_percent": 100},
            "changed_line_coverage": {"coverage_percent": 90},
            "change_closure": {
                "status": "passed",
                "ui_status": "passed",
                "blocking_reasons": [],
                "test_results": [],
            },
        },
    )

    assert result["status"] == "blocked"
    assert result["current_stage"] == expected_stage
    assert result["stages"][stage_index]["status"] == "blocked"
    assert result["stages"][stage_index]["blocking_reasons"] == ["ユーザーにより差し戻されました。"]
