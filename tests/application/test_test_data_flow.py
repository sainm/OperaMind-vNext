from copy import deepcopy
from typing import Any, cast

from operamind.application.test_data_flow import validate_test_data_plan_artifact


def test_cross_screen_flow_passes_outputs_to_later_screen() -> None:
    plan = _plan(_cross_screen_flow())

    assert validate_test_data_plan_artifact(plan) == []


def test_cross_screen_flow_blocks_variable_used_before_it_is_produced() -> None:
    flow = _cross_screen_flow()
    steps = cast(list[dict[str, Any]], flow["steps"])
    steps[0]["inputs"] = {"employee_id": "{{employee_id}}"}

    assert validate_test_data_plan_artifact(_plan(flow)) == [
        "expense-approval-chain/create-expense: input variables are not produced "
        "by earlier steps: ['employee_id']"
    ]


def test_cross_screen_flow_requires_reviewed_ui_screen_and_action_refs() -> None:
    flow = _cross_screen_flow()
    del cast(list[dict[str, Any]], flow["steps"])[0]["screen_ref"]

    assert validate_test_data_plan_artifact(_plan(flow)) == [
        "expense-approval-chain/create-expense: UI generation requires reviewed "
        "screen/action refs"
    ]


def test_artifact_semantics_require_cleanup_steps() -> None:
    flow = _cross_screen_flow()
    flow["cleanup_policy"] = "delete_after_run"

    assert validate_test_data_plan_artifact(_plan(flow)) == [
        "expense-approval-chain: delete_after_run requires cleanup steps"
    ]


def _plan(flow: dict[str, Any]) -> dict[str, Any]:
    return {
        "data_sets": [
            {
                "test_data_id": "expense-draft",
                "test_case_refs": ["expense-entry", "expense-search"],
            }
        ],
        "generation_flows": [deepcopy(flow)],
    }


def _cross_screen_flow() -> dict[str, Any]:
    return {
        "flow_id": "expense-approval-chain",
        "title": "経費を登録し、別画面で差戻し状態を確認する",
        "test_data_refs": ["expense-draft"],
        "test_case_refs": ["expense-entry", "expense-search"],
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
