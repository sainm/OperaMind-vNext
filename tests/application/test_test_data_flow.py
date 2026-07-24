import json
from pathlib import Path
from typing import Any, cast

import pytest

from operamind.application.change_loop_case import ChangeLoopCase
from operamind.application.test_data_flow import (
    build_test_data_plan_flows,
    validate_test_data_plan_artifact,
)

ROOT = Path(__file__).parents[2]
CASE_ROOT = ROOT / "golden-dataset/cases/visiondemo-expense-status-filter-golden"


def test_legacy_data_sets_become_verified_generation_flows() -> None:
    case = ChangeLoopCase.load(CASE_ROOT)

    flows, blocking_reasons = build_test_data_plan_flows(case)

    assert blocking_reasons == []
    assert {flow["test_data_refs"][0] for flow in flows} == {
        "test-data-default-seed",
        "test-data-returned-expense",
    }
    returned = next(flow for flow in flows if flow["flow_id"] == "flow-test-data-returned-expense")
    assert returned["steps"][0]["channel"] == "http"
    assert returned["steps"][0]["inputs"] == {
        "method": "POST",
        "path": "/expense/api/save",
        "json": {
            "expense": {
                "expenseNo": "EXP-OM-RETURNED",
                "employee": {"id": 1},
                "totalAmount": 1000,
                "status": "差戻し",
                "applyDate": "2026-07-17",
                "description": "OperaMind UI verification data",
            },
            "details": [],
        },
    }
    assert returned["steps"][0]["postconditions"][0] == {
        "assertion_id": "returned-expense-1",
        "observe_via": "response",
        "subject": "expenseNo",
        "operator": "equals",
        "expected": "EXP-OM-RETURNED",
    }


def test_reviewed_cross_screen_flow_passes_outputs_to_later_screen() -> None:
    payload = _payload()
    payload["data_generation_flows"] = [_cross_screen_flow()]

    case = ChangeLoopCase.from_payload(root=CASE_ROOT, payload=payload)
    flows, blocking_reasons = build_test_data_plan_flows(case)

    assert blocking_reasons == []
    assert flows[0]["steps"][1]["inputs"] == {"expense_id": "{{expense_id}}"}
    assert flows[0]["steps"][1]["depends_on"] == ["create-expense"]


def test_cross_screen_flow_blocks_variable_used_before_it_is_produced() -> None:
    payload = _payload()
    flow = _cross_screen_flow()
    steps = cast(list[dict[str, Any]], flow["steps"])
    steps[0]["inputs"] = {"employee_id": "{{employee_id}}"}
    payload["data_generation_flows"] = [flow]

    with pytest.raises(ValueError, match="not produced by earlier steps"):
        ChangeLoopCase.from_payload(root=CASE_ROOT, payload=payload)


def test_cross_screen_flow_requires_reviewed_ui_screen_and_action_refs() -> None:
    payload = _payload()
    flow = _cross_screen_flow()
    del cast(list[dict[str, Any]], flow["steps"])[0]["screen_ref"]
    payload["data_generation_flows"] = [flow]

    with pytest.raises(ValueError, match=r"screen_ref.*required"):
        ChangeLoopCase.from_payload(root=CASE_ROOT, payload=payload)


def test_artifact_semantics_require_cleanup_and_resolved_cleanup_variables() -> None:
    plan = {
        "data_sets": [{"test_data_id": "data-1", "test_case_refs": ["case-1"]}],
        "generation_flows": [
            {
                "flow_id": "flow-1",
                "test_data_refs": ["data-1"],
                "test_case_refs": ["case-1"],
                "steps": [
                    {
                        "step_id": "create",
                        "sequence": 1,
                        "channel": "http",
                        "target": "POST /items",
                        "inputs": {},
                        "depends_on": [],
                        "output_bindings": [],
                        "postconditions": [{"assertion_id": "created"}],
                    }
                ],
                "final_assertions": [{"assertion_id": "final"}],
                "cleanup_policy": "delete_after_run",
                "cleanup_steps": [],
            }
        ],
    }

    assert validate_test_data_plan_artifact(plan) == [
        "flow-1: delete_after_run requires cleanup steps"
    ]


def _payload() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((CASE_ROOT / "change-loop-case.json").read_text(encoding="utf-8")),
    )


def _cross_screen_flow() -> dict[str, Any]:
    case = ChangeLoopCase.load(CASE_ROOT)
    return {
        "flow_id": "expense-approval-chain",
        "title": "経費を登録し、別画面で差戻し状態を確認する",
        "test_data_refs": [value["test_data_id"] for value in case.data_sets],
        "test_case_refs": [value["test_case_id"] for value in case.test_cases],
        "steps": [
            {
                "step_id": "create-expense",
                "sequence": 1,
                "channel": "ui",
                "business_action": "経費を登録する",
                "screen_ref": "expense-entry",
                "ui_action_ref": "submit-expense",
                "inputs": {"status": "差戻し"},
                "depends_on": [],
                "output_bindings": [
                    {
                        "variable": "expense_id",
                        "source": "ui",
                        "path": "saved_expense.id",
                        "required": True,
                    }
                ],
                "postconditions": [
                    {
                        "assertion_id": "expense-created",
                        "observe_via": "ui",
                        "subject": "保存結果",
                        "operator": "exists",
                        "expected": True,
                    }
                ],
            },
            {
                "step_id": "find-expense",
                "sequence": 2,
                "channel": "ui",
                "business_action": "登録した経費を検索する",
                "screen_ref": "expense-list",
                "ui_action_ref": "search-expense",
                "inputs": {"expense_id": "{{expense_id}}"},
                "depends_on": ["create-expense"],
                "output_bindings": [],
                "postconditions": [
                    {
                        "assertion_id": "expense-visible",
                        "observe_via": "ui",
                        "subject": "検索結果",
                        "operator": "contains",
                        "expected": "{{expense_id}}",
                    }
                ],
            },
        ],
        "final_assertions": [
            {
                "assertion_id": "returned-status-visible",
                "observe_via": "ui",
                "subject": "経費ステータス",
                "operator": "equals",
                "expected": "差戻し",
            }
        ],
        "cleanup_policy": "isolated_environment",
        "cleanup_steps": [],
    }
