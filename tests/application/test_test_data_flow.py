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


def test_cross_screen_flow_blocks_unknown_variable_inside_playwright_action() -> None:
    flow = _cross_screen_flow()
    steps = cast(list[dict[str, Any]], flow["steps"])
    steps[0]["playwright"] = {
        "action": "fill",
        "locator": "#expense-id",
        "value": "{{missing_expense_id}}",
    }

    assert validate_test_data_plan_artifact(_plan(flow)) == [
        "expense-approval-chain/create-expense: input variables are not produced "
        "by earlier steps: ['missing_expense_id']"
    ]


def test_cross_screen_flow_requires_reviewed_ui_screen_and_action_refs() -> None:
    flow = _cross_screen_flow()
    del cast(list[dict[str, Any]], flow["steps"])[0]["screen_ref"]

    assert validate_test_data_plan_artifact(_plan(flow)) == [
        "expense-approval-chain/create-expense: UI generation requires reviewed screen/action refs"
    ]


def test_artifact_semantics_require_cleanup_steps() -> None:
    flow = _cross_screen_flow()
    flow["cleanup_policy"] = "delete_after_run"

    assert validate_test_data_plan_artifact(_plan(flow)) == [
        "expense-approval-chain: delete_after_run requires cleanup steps"
    ]


def test_v2_plan_rejects_unproved_existing_target_system_data() -> None:
    flow = _cross_screen_flow()
    for step in cast(list[dict[str, Any]], flow["steps"]):
        step["test_step_refs"] = [f"test-{step['step_id']}"]
    plan = _plan(flow)
    plan["schema_version"] = "v2"

    assert validate_test_data_plan_artifact(plan) == [
        "expense-approval-chain: v2 UI flow requires an explicit target-system data "
        "generation step; unproved existing data is not executable test data"
    ]


def test_v2_plan_does_not_infer_data_generation_from_action_words() -> None:
    flow = _cross_screen_flow()
    step = cast(list[dict[str, Any]], flow["steps"])[0]
    del step["data_effect"]
    plan = _plan(flow)
    plan["schema_version"] = "v2"

    assert validate_test_data_plan_artifact(plan) == [
        "expense-approval-chain: v2 UI flow requires an explicit target-system data "
        "generation step; unproved existing data is not executable test data"
    ]


def test_v2_plan_rejects_delete_disguised_as_data_generation() -> None:
    flow = _cross_screen_flow()
    step = cast(list[dict[str, Any]], flow["steps"])[0]
    step.update(
        {
            "channel": "http",
            "target": "DELETE /expense/api/1",
            "data_effect": "creates",
            "inputs": {"method": "DELETE", "path": "/expense/api/1"},
            "output_bindings": [
                {
                    "variable": "expense_id",
                    "source": "response",
                    "path": "id",
                    "required": True,
                }
            ],
            "postconditions": [
                {
                    "assertion_id": "expense-deleted",
                    "observe_via": "api",
                    "subject": "status_code",
                    "operator": "equals",
                    "expected": 204,
                }
            ],
        }
    )
    plan = _plan(flow)
    plan["schema_version"] = "v2"

    assert validate_test_data_plan_artifact(plan) == [
        "expense-approval-chain/create-expense: HTTP setup cannot use DELETE as test data "
        "generation",
        "expense-approval-chain: v2 UI flow requires an explicit target-system data "
        "generation step; unproved existing data is not executable test data",
    ]


def test_v2_plan_accepts_typed_ui_data_generation_without_keyword_guessing() -> None:
    flow = _cross_screen_flow()
    step = cast(list[dict[str, Any]], flow["steps"])[0]
    step["business_action"] = "入力内容を確定する"
    step["ui_action_ref"] = "confirm"
    plan = _plan(flow)
    plan["schema_version"] = "v2"

    assert validate_test_data_plan_artifact(plan) == []


def test_v2_http_generation_requires_business_response_checks_and_identity_binding() -> None:
    flow = _cross_screen_flow()
    step = cast(list[dict[str, Any]], flow["steps"])[0]
    step.update(
        {
            "channel": "http",
            "target": "POST /expense/api/save",
            "inputs": {
                "method": "POST",
                "path": "/expense/api/save",
                "json": {"description": "UI 検証用"},
            },
            "output_bindings": [
                {
                    "variable": "expense_id",
                    "source": "response",
                    "path": "id",
                    "required": True,
                }
            ],
            "postconditions": [
                {
                    "assertion_id": "expense-created",
                    "observe_via": "api",
                    "subject": "status_code",
                    "operator": "equals",
                    "expected": 200,
                }
            ],
        }
    )
    plan = _plan(flow)
    plan["schema_version"] = "v2"

    assert validate_test_data_plan_artifact(plan) == [
        "expense-approval-chain/create-expense: mutating HTTP setup must assert "
        "returned business fields"
    ]


def test_v2_http_generation_accepts_verified_business_response_and_id() -> None:
    flow = _cross_screen_flow()
    step = cast(list[dict[str, Any]], flow["steps"])[0]
    step.update(
        {
            "channel": "http",
            "target": "POST /expense/api/save",
            "inputs": {
                "method": "POST",
                "path": "/expense/api/save",
                "json": {"description": "UI 検証用"},
            },
            "output_bindings": [
                {
                    "variable": "expense_id",
                    "source": "response",
                    "path": "id",
                    "required": True,
                }
            ],
            "postconditions": [
                {
                    "assertion_id": "expense-created",
                    "observe_via": "api",
                    "subject": "status_code",
                    "operator": "equals",
                    "expected": 200,
                },
                {
                    "assertion_id": "expense-description-returned",
                    "observe_via": "response",
                    "subject": "description",
                    "operator": "equals",
                    "expected": "UI 検証用",
                },
            ],
        }
    )
    plan = _plan(flow)
    plan["schema_version"] = "v2"

    assert validate_test_data_plan_artifact(plan) == []


def test_v2_http_generation_rejects_hard_coded_nested_master_identity() -> None:
    flow = _cross_screen_flow()
    step = cast(list[dict[str, Any]], flow["steps"])[0]
    step.update(
        {
            "channel": "http",
            "target": "POST /expense/api/save",
            "inputs": {
                "method": "POST",
                "path": "/expense/api/save",
                "json": {
                    "expense": {
                        "employee": {"id": 1},
                        "description": "UI 検証用",
                    }
                },
            },
            "output_bindings": [
                {
                    "variable": "expense_id",
                    "source": "response",
                    "path": "id",
                    "required": True,
                }
            ],
            "postconditions": [
                {
                    "assertion_id": "expense-created",
                    "observe_via": "api",
                    "subject": "status_code",
                    "operator": "equals",
                    "expected": 200,
                },
                {
                    "assertion_id": "expense-description",
                    "observe_via": "response",
                    "subject": "description",
                    "operator": "equals",
                    "expected": "UI 検証用",
                },
            ],
        }
    )
    plan = _plan(flow)
    plan["schema_version"] = "v2"

    assert validate_test_data_plan_artifact(plan) == [
        "expense-approval-chain/create-expense: mutating HTTP setup must resolve nested "
        "identity fields from earlier verified output bindings: ['expense.employee.id']"
    ]


def test_v2_http_generation_rejects_hard_coded_identity_nested_in_array() -> None:
    flow = _cross_screen_flow()
    step = cast(list[dict[str, Any]], flow["steps"])[0]
    step.update(
        {
            "channel": "http",
            "target": "POST /expense/api/save",
            "inputs": {
                "method": "POST",
                "path": "/expense/api/save",
                "json": {"details": [{"employee": {"id": 1}}]},
            },
            "output_bindings": [
                {
                    "variable": "expense_id",
                    "source": "response",
                    "path": "id",
                    "required": True,
                }
            ],
            "postconditions": [
                {
                    "assertion_id": "expense-created",
                    "observe_via": "api",
                    "subject": "status_code",
                    "operator": "equals",
                    "expected": 201,
                },
                {
                    "assertion_id": "expense-returned",
                    "observe_via": "response",
                    "subject": "id",
                    "operator": "exists",
                    "expected": True,
                },
            ],
        }
    )
    plan = _plan(flow)
    plan["schema_version"] = "v2"

    assert validate_test_data_plan_artifact(plan) == [
        "expense-approval-chain/create-expense: mutating HTTP setup must resolve nested "
        "identity fields from earlier verified output bindings: "
        "['details[0].employee.id']"
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
                "data_effect": "creates",
                "screen_ref": "expense-entry",
                "ui_action_ref": "submit-expense",
                "playwright": {
                    "action": "click",
                    "mask_locators": [],
                    "observations": [{"key": "saved_expense.id", "kind": "attribute"}],
                },
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
