from copy import deepcopy
from typing import Any, cast

from operamind.application.test_data_flow import (
    _runtime_variable_uses,
    _validate_v3_run_context_contract,
    validate_test_data_plan_artifact,
)


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


def test_plan_rejects_secret_shaped_fields_before_execution() -> None:
    flow = _cross_screen_flow()
    steps = cast(list[dict[str, Any]], flow["steps"])
    steps[0]["inputs"] = {"password": "must-never-enter-the-plan"}

    reasons = validate_test_data_plan_artifact(_plan(flow))

    assert any("Secret-like field" in reason and "password" in reason for reason in reasons)


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
    plan = _v2_plan(flow)

    assert validate_test_data_plan_artifact(plan) == [
        "All real Provider data coverage conditions must execute before the first TestPlan UI step",
        "expense-draft: generated identity requires an earlier explicit data generation step",
    ]


def test_v2_identity_requires_configured_provider_and_rejects_camel_case_secret() -> None:
    plan = _v2_plan(_cross_screen_flow())
    identity = cast(
        dict[str, Any],
        cast(list[dict[str, Any]], plan["data_sets"])[0]["identity_binding"],
    )
    del identity["provider"]
    cast(dict[str, Any], identity["primary_key"])["name"] = "accessToken"

    reasons = validate_test_data_plan_artifact(plan)

    assert "expense-draft: DataIdentityProvider is not configured" in reasons
    assert "expense-draft: Secret-like fields cannot be identity values" in reasons

    identity["provider"] = {"type": "database", "provider_ref": "unknown.v1"}
    reasons = validate_test_data_plan_artifact(plan)
    assert (
        "expense-draft: DataIdentityProvider is not configured for database:unknown.v1"
        in reasons
    )


def test_v2_hybrid_identity_requires_an_executable_step_for_each_real_source() -> None:
    plan = _v2_plan(_cross_screen_flow())
    identity = cast(
        dict[str, Any],
        cast(list[dict[str, Any]], plan["data_sets"])[0]["identity_binding"],
    )
    identity["provider"] = {"type": "hybrid", "provider_ref": "hybrid.v1"}
    cast(list[dict[str, Any]], identity["business_unique_keys"])[0].update(
        {"source": "api", "path": "body.record.expense_number"}
    )

    reasons = validate_test_data_plan_artifact(plan)

    assert (
        "expense-draft: hybrid identity has no executable source steps for channels ['http']"
        in reasons
    )


def test_v2_plan_does_not_infer_data_generation_from_action_words() -> None:
    flow = _cross_screen_flow()
    step = cast(list[dict[str, Any]], flow["steps"])[0]
    del step["data_effect"]
    plan = _v2_plan(flow)

    assert validate_test_data_plan_artifact(plan) == [
        "expense-draft: generated identity requires an earlier explicit data generation step"
    ]


def test_v3_run_context_rejects_dependency_variable_and_cleanup_scope_violations() -> None:
    data_sets = [
        {
            "test_data_id": "adopted-expense",
            "runtime_variable_writes": [
                {"variable": "operamind_run_id", "target_field": "status"}
            ],
                "identity_binding": {
                    "binding_mode": "adopted",
                    "provider": {"type": "database"},
                    "primary_key": {"source": "database"},
                    "business_unique_keys": [{"source": "database"}],
                    "screen_key": {"source": "database"},
                    "match_count": {"source": "database"},
                },
        }
    ]
    flows = [
        {
            "flow_id": "flow-a",
            "depends_on_flows": ["flow-a", "flow-b", "missing-flow"],
            "test_data_refs": ["adopted-expense"],
            "steps": [
                {
                    "step_id": "unsafe-run-variable",
                    "channel": "ui",
                    "target": "{{test_data_token}}",
                    "playwright": {},
                    "inputs": {"status": "{{test_data_token}}"},
                }
            ],
            "cleanup_steps": [
                {
                    "step_id": "cleanup-bound-record",
                    "channel": "ui",
                    "data_binding_ref": "adopted-expense",
                    "postconditions": [],
                }
            ],
        },
        {
            "flow_id": "flow-b",
            "depends_on_flows": ["flow-a"],
            "test_data_refs": [],
            "steps": [],
            "cleanup_steps": [],
        },
    ]

    reasons = _validate_v3_run_context_contract(data_sets, flows)

    assert any("do not exist" in reason for reason in reasons)
    assert any("cannot depend on itself" in reason for reason in reasons)
    assert any("dependency cycle" in reason for reason in reasons)
    assert any("adopted data cannot overwrite" in reason for reason in reasons)
    assert any("system Run variables" in reason for reason in reasons)
    assert any("not explicitly allowed" in reason for reason in reasons)
    assert any("must verify match count 0" in reason for reason in reasons)
    assert any("absence verification is missing" in reason for reason in reasons)
    assert _runtime_variable_uses(
        {"payload": [{"token": "{{test_data_token}}"}, 41]}
    ) == {("test_data_token", "payload[0].token")}


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
    plan = _v2_plan(flow)

    assert validate_test_data_plan_artifact(plan) == [
        "expense-approval-chain/create-expense: HTTP setup cannot use DELETE as test data "
        "generation",
        "expense-draft: generated identity requires an earlier explicit data generation step",
    ]


def test_v2_plan_accepts_typed_ui_data_generation_without_keyword_guessing() -> None:
    flow = _cross_screen_flow()
    step = cast(list[dict[str, Any]], flow["steps"])[0]
    step["business_action"] = "入力内容を確定する"
    step["ui_action_ref"] = "confirm"
    plan = _v2_plan(flow)

    assert validate_test_data_plan_artifact(plan) == []


def test_v2_plan_requires_reviewed_pre_action_state_for_mutating_ui_action() -> None:
    plan = _v2_plan(_cross_screen_flow())
    action = cast(
        dict[str, Any],
        cast(list[dict[str, Any]], plan["generation_flows"])[0]["steps"][0][
            "playwright"
        ],
    )
    del action["pre_action_observations"]

    assert (
        "expense-approval-chain/create-expense: state-changing Playwright action requires "
        "reviewed pre_action_observations" in validate_test_data_plan_artifact(plan)
    )


def test_v2_plan_rejects_secret_pre_action_observation() -> None:
    plan = _v2_plan(_cross_screen_flow())
    action = cast(
        dict[str, Any],
        cast(list[dict[str, Any]], plan["generation_flows"])[0]["steps"][0][
            "playwright"
        ],
    )
    cast(list[dict[str, Any]], action["pre_action_observations"])[0]["key"] = (
        "accessToken"
    )

    assert (
        "expense-approval-chain/create-expense: pre-action observation cannot expose a "
        "Secret field" in validate_test_data_plan_artifact(plan)
    )


def test_v2_plan_requires_explicit_record_scope_and_consistent_binding() -> None:
    plan = _v2_plan(_cross_screen_flow())
    flow = cast(list[dict[str, Any]], plan["generation_flows"])[0]
    record_step = cast(list[dict[str, Any]], flow["steps"])[2]

    del record_step["operation_scope"]
    assert (
        "expense-approval-chain/find-expense: v2 UI operation requires an explicit screen "
        "or bound_record operation_scope" in validate_test_data_plan_artifact(plan)
    )

    record_step["operation_scope"] = "screen"
    assert (
        "expense-approval-chain/find-expense: screen operation cannot carry data_binding_ref"
        in validate_test_data_plan_artifact(plan)
    )

    del record_step["data_binding_ref"]
    record_step["operation_scope"] = "bound_record"
    assert (
        "expense-approval-chain/find-expense: bound_record operation requires data_binding_ref"
        in validate_test_data_plan_artifact(plan)
    )


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
    plan = _v2_plan(flow)

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
    plan = _v2_plan(flow)

    assert validate_test_data_plan_artifact(plan) == []


def test_v2_sql_generation_is_executable_after_project_binding_validation() -> None:
    flow = _cross_screen_flow()
    step = cast(list[dict[str, Any]], flow["steps"])[0]
    step.update(
        {
            "channel": "sql",
            "target": "upsert_expense",
            "inputs": {"expense_id": "EXP-001", "status": "SUBMITTED"},
            "output_bindings": [
                {
                    "variable": "expense_id",
                    "source": "database",
                    "path": "rows[0].expense_id",
                    "required": True,
                }
            ],
            "postconditions": [
                {
                    "assertion_id": "expense-created",
                    "observe_via": "database",
                    "subject": "read_after_write",
                    "operator": "equals",
                    "expected": "passed",
                }
            ],
        }
    )
    plan = _v2_plan(flow)

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
    plan = _v2_plan(flow)

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
    plan = _v2_plan(flow)

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


def _v2_plan(flow: dict[str, Any]) -> dict[str, Any]:
    value = deepcopy(flow)
    steps = cast(list[dict[str, Any]], value["steps"])
    steps.insert(
        1,
        {
            "step_id": "read-expense-identity",
            "sequence": 2,
            "channel": "sql",
            "business_action": "登録した経費を DB から一意に読み戻す",
            "data_effect": "none",
            "test_step_refs": [],
            "target": "read_expense_identity",
            "inputs": {"expense_id": "{{expense_id}}"},
            "depends_on": [str(steps[0]["step_id"])],
            "output_bindings": [],
            "postconditions": [
                {
                    "assertion_id": "expense-identity-unique",
                    "observe_via": "database",
                    "subject": "row_count",
                    "operator": "equals",
                    "expected": 1,
                }
            ],
        },
    )
    for index, step in enumerate(steps, start=1):
        step["sequence"] = index
        if step.get("channel") == "ui":
            step["operation_scope"] = "screen"
    record_step = steps[2]
    record_step["depends_on"] = ["read-expense-identity"]
    record_step["data_binding_ref"] = "expense-draft"
    record_step["operation_scope"] = "bound_record"
    record_step["playwright"] = {
        "action": "wait_for",
        "locator": {"by": "css", "value": ":scope", "exact": True},
        "state": "visible",
        "mask_locators": [],
        "observations": [],
    }
    plan = _plan(value)
    plan["schema_version"] = "v2"
    cast(list[dict[str, Any]], plan["data_sets"])[0]["identity_binding"] = {
        "provider": {"type": "database", "provider_ref": "database.v1"},
        "binding_mode": "generated",
        "source_flow_id": "expense-approval-chain",
        "source_step_id": "read-expense-identity",
        "primary_key": {"name": "id", "source": "database", "path": "rows[0].id"},
        "business_unique_keys": [
            {
                "name": "expense_number",
                "source": "database",
                "path": "rows[0].expense_number",
                "dom_observation": {
                    "kind": "attribute",
                    "attribute_name": "data-observed-expense-number",
                },
            }
        ],
        "screen_key": {
            "name": "expense_number",
            "source": "database",
            "path": "rows[0].expense_number",
            "dom_observation": {
                "kind": "attribute",
                "attribute_name": "data-observed-expense-number",
            },
            "locator_template": {
                "by": "css",
                "value": "[data-expense-number='{{value}}']",
                "exact": True,
            },
        },
        "match_count": {"source": "database", "path": "row_count"},
    }
    cast(list[dict[str, Any]], plan["data_sets"])[0]["coverage_conditions"] = [
        {
            "condition_id": "expense-returned-condition",
            "criterion_ref": "criterion-returned",
            "test_case_ref": "expense-search",
            "test_data_id": "expense-draft",
            "condition_kind": "status",
            "source_flow_id": "expense-approval-chain",
            "source_step_id": "read-expense-identity",
            "path": "rows[0].status",
            "operator": "equals",
            "expected": "RETURNED",
        }
    ]
    return plan


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
                    "pre_action_observations": [
                        {
                            "key": "expense_entry_title",
                            "kind": "title",
                            "expected": "経費登録",
                        }
                    ],
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
