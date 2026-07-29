"""VisionDemo-only cross-screen TestDataPlan fixture."""

from __future__ import annotations

import re
from typing import Any

_PARAMETER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def build_visiondemo_cross_screen_plan(*, force_business_failure: bool = False) -> dict[str, Any]:
    """Return one direct TestDataPlan with cross-screen variables and reverse cleanup."""

    suffix = "failure" if force_business_failure else "passed"
    expected_employee = "意図的失敗" if force_business_failure else "{{employee_name}}"
    return _substitute(
        _cross_screen_plan_source(suffix),
        {
            "department_id": 7,
            "business_date": "2026-07-19",
            "expense_status": "差戻し",
            "expense_amount": 4321,
            "expected_employee_name": expected_employee,
        },
    )


def _cross_screen_plan_source(suffix: str) -> dict[str, Any]:
    return {
        "artifact_type": "TestDataPlan",
        "schema_version": "v1",
        "test_data_plan_id": f"test-data-plan-visiondemo-target-{suffix}",
        "test_plan_id": "test-plan-visiondemo-target-e2e",
        "project_id": "visiondemo",
        "status": "ready",
        "data_sets": [
            {
                "test_data_id": "visiondemo-cross-screen-data",
                "test_case_refs": ["visiondemo-cross-screen-target-e2e"],
                "setup_actions": [
                    {
                        "action_id": "allocate-runtime-identities",
                        "action_type": "fixture",
                        "target": "visiondemo.runtime-identities",
                        "payload": {},
                    }
                ],
                "cleanup_policy": "delete_after_run",
            }
        ],
        "generation_flows": [{
            "flow_id": "visiondemo-cross-screen-flow",
            "title": "社員画面と経費画面を同一データ系列で検証する",
            "test_case_refs": ["visiondemo-cross-screen-target-e2e"],
            "test_data_refs": ["visiondemo-cross-screen-data"],
            "steps": [
                _step(
                    "allocate-runtime-identities",
                    1,
                    "fixture",
                    "実行ごとの識別子を割り当てる",
                    target="visiondemo.runtime-identities",
                    outputs=[
                        _output(name, "fixture", name)
                        for name in (
                            "employee_no",
                            "employee_name",
                            "employee_email",
                            "expense_no",
                        )
                    ],
                    postconditions=[
                        {
                            "assertion_id": "runtime-identities-allocated",
                            "observe_via": "fixture",
                            "subject": "employee_no",
                            "operator": "exists",
                            "expected": True,
                        }
                    ],
                ),
                _step(
                    "create-linked-employee",
                    2,
                    "http",
                    "関連社員を作成する",
                    target="POST /employee/api/save",
                    inputs={
                        "method": "POST",
                        "path": "/employee/api/save",
                        "json": {
                            "employeeNo": "{{employee_no}}",
                            "name": "{{employee_name}}",
                            "nameKana": "レンケイシケン",
                            "department": {"id": "${department_id}"},
                            "position": "テスト担当",
                            "hireDate": "${business_date}",
                            "email": "{{employee_email}}",
                            "phone": "03-9999-0001",
                        },
                    },
                    depends_on=["allocate-runtime-identities"],
                    outputs=[_output("employee_id", "response", "id")],
                    entity_ref="employee",
                    postconditions=[
                        _assertion(
                            "employee-created",
                            "response",
                            "employeeNo",
                            "{{employee_no}}",
                        )
                    ],
                ),
                _step(
                    "create-linked-expense",
                    3,
                    "http",
                    "社員に関連する差戻し経費を作成する",
                    target="POST /expense/api/save",
                    inputs={
                        "method": "POST",
                        "path": "/expense/api/save",
                        "json": {
                            "expense": {
                                "expenseNo": "{{expense_no}}",
                                "employee": {"id": "{{employee_id}}"},
                                "totalAmount": "${expense_amount}",
                                "status": "${expense_status}",
                                "applyDate": "${business_date}",
                                "description": "OperaMind 跨画面 E2E",
                            },
                            "details": [
                                {
                                    "lineNo": 1,
                                    "accountItem": "交通費",
                                    "amount": "${expense_amount}",
                                    "expenseDate": "${business_date}",
                                    "description": "関連データ",
                                }
                            ],
                        },
                    },
                    depends_on=["create-linked-employee"],
                    outputs=[_output("expense_id", "response", "id")],
                    entity_ref="expense",
                    postconditions=[
                        _assertion(
                            "expense-created",
                            "response",
                            "expenseNo",
                            "{{expense_no}}",
                        )
                    ],
                ),
                _step(
                    "verify-employee-screen",
                    4,
                    "ui",
                    "社員一覧で関連社員を確認する",
                    screen_ref="employee-list",
                    ui_action_ref="search-created-employee",
                    inputs={
                        "employee_no": "{{employee_no}}",
                        "employee_name": "{{employee_name}}",
                    },
                    depends_on=["create-linked-expense"],
                    postconditions=[
                        _assertion(
                            "employee-visible",
                            "ui",
                            "employee.employeeNo",
                            "{{employee_no}}",
                        )
                    ],
                ),
                _step(
                    "verify-expense-screen",
                    5,
                    "ui",
                    "経費一覧で同じ社員の経費を確認する",
                    screen_ref="expense-list",
                    ui_action_ref="search-created-expense",
                    inputs={
                        "expense_no": "{{expense_no}}",
                        "employee_name": "{{employee_name}}",
                        "status": "${expense_status}",
                    },
                    depends_on=["verify-employee-screen"],
                    postconditions=[
                        _assertion(
                            "linked-expense-visible",
                            "ui",
                            "matching_row.expenseNo",
                            "{{expense_no}}",
                        )
                    ],
                ),
                _step(
                    "verify-database-link",
                    6,
                    "sql",
                    "DB 上の社員と経費の関連を確認する",
                    target="visiondemo.expense-by-id",
                    inputs={
                        "expense_id": "{{expense_id}}",
                        "employee_id": "{{employee_id}}",
                    },
                    depends_on=["verify-expense-screen"],
                    postconditions=[
                        _assertion("database-link-exists", "database", "expense_count", 1)
                    ],
                ),
            ],
            "final_assertions": [
                _assertion(
                    "cross-screen-employee-name",
                    "ui",
                    "matching_row.employee_name",
                    "${expected_employee_name}",
                ),
                _assertion(
                    "cross-screen-expense-number",
                    "ui",
                    "matching_row.expenseNo",
                    "{{expense_no}}",
                ),
            ],
            "cleanup_policy": "delete_after_run",
            "cleanup_steps": [
                _step(
                    "delete-linked-expense",
                    1,
                    "http",
                    "関連経費を削除する",
                    target="DELETE /expense/api/{{expense_id}}",
                    inputs={
                        "method": "DELETE",
                        "path": "/expense/api/{{expense_id}}",
                    },
                    postconditions=[_assertion("expense-delete-ok", "api", "status_code", 200)],
                    entity_ref="expense",
                ),
                _step(
                    "delete-linked-employee",
                    2,
                    "http",
                    "関連社員を削除する",
                    target="DELETE /employee/api/{{employee_id}}",
                    inputs={
                        "method": "DELETE",
                        "path": "/employee/api/{{employee_id}}",
                    },
                    depends_on=["delete-linked-expense"],
                    postconditions=[_assertion("employee-delete-ok", "api", "status_code", 200)],
                    entity_ref="employee",
                ),
                _step(
                    "verify-cleanup-database",
                    3,
                    "sql",
                    "DB 上の関連データ削除を確認する",
                    target="visiondemo.cleanup-by-ids",
                    inputs={
                        "expense_id": "{{expense_id}}",
                        "employee_id": "{{employee_id}}",
                    },
                    depends_on=["delete-linked-employee"],
                    postconditions=[
                        _assertion("expense-cleaned", "database", "expense_count", 0),
                        _assertion("employee-cleaned", "database", "employee_count", 0),
                    ],
                ),
            ],
        }],
        "blocking_reasons": [],
    }


def _step(
    step_id: str,
    sequence: int,
    channel: str,
    business_action: str,
    *,
    target: str | None = None,
    screen_ref: str | None = None,
    ui_action_ref: str | None = None,
    inputs: dict[str, object] | None = None,
    depends_on: list[str] | None = None,
    outputs: list[dict[str, object]] | None = None,
    postconditions: list[dict[str, object]] | None = None,
    entity_ref: str | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "step_id": step_id,
        "sequence": sequence,
        "channel": channel,
        "business_action": business_action,
        "inputs": inputs or {},
        "depends_on": depends_on or [],
        "output_bindings": outputs or [],
        "postconditions": postconditions or [],
    }
    if target is not None:
        value["target"] = target
    if screen_ref is not None:
        value["screen_ref"] = screen_ref
    if ui_action_ref is not None:
        value["ui_action_ref"] = ui_action_ref
    if entity_ref is not None:
        value["entity_ref"] = entity_ref
    return value


def _output(variable: str, source: str, path: str) -> dict[str, object]:
    return {"variable": variable, "source": source, "path": path, "required": True}


def _assertion(
    assertion_id: str,
    observe_via: str,
    subject: str,
    expected: object,
) -> dict[str, object]:
    return {
        "assertion_id": assertion_id,
        "observe_via": observe_via,
        "subject": subject,
        "operator": "equals",
        "expected": expected,
    }


def _substitute(value: object, parameters: dict[str, object]) -> Any:
    if isinstance(value, str):
        exact = _PARAMETER.fullmatch(value)
        if exact is not None:
            return parameters[exact.group(1)]
        return _PARAMETER.sub(lambda match: str(parameters[match.group(1)]), value)
    if isinstance(value, list):
        return [_substitute(item, parameters) for item in value]
    if isinstance(value, dict):
        return {key: _substitute(item, parameters) for key, item in value.items()}
    return value
