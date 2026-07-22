"""Deterministic cross-screen TestDataPlan shape for VisionDemo deployment proof."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from operamind.application.business_data_template import (
    BusinessDataTemplateInstantiator,
    BusinessDataTemplateRequest,
)
from operamind.contracts import ContractCatalog


def build_visiondemo_cross_screen_plan(*, force_business_failure: bool = False) -> dict[str, Any]:
    """Instantiate the approved reusable VisionDemo master/detail data template."""

    suffix = "failure" if force_business_failure else "passed"
    expected_employee = "意図的失敗" if force_business_failure else "{{employee_name}}"
    root = Path(__file__).resolve().parents[3]
    return BusinessDataTemplateInstantiator(ContractCatalog.load(root / "contracts")).instantiate(
        template=build_visiondemo_cross_screen_template(),
        request=BusinessDataTemplateRequest(
            instance_id="visiondemo-cross-screen-runtime",
            test_data_plan_id=f"test-data-plan-visiondemo-target-{suffix}",
            test_plan_id="test-plan-visiondemo-target-e2e",
            project_id="visiondemo",
            test_case_refs=("visiondemo-cross-screen-target-e2e",),
            parameters={
                "department_id": 7,
                "business_date": "2026-07-19",
                "expense_status": "差戻し",
                "expense_amount": 4321,
                "expected_employee_name": expected_employee,
            },
        ),
    )


def build_visiondemo_cross_screen_template() -> dict[str, Any]:
    """Return the approved reusable template without instance parameter values."""

    return {
        "artifact_type": "BusinessDataTemplate",
        "schema_version": "v1",
        "template_id": "business-data-template-visiondemo-employee-expense-v1",
        "template_key": "visiondemo.employee-expense-cross-screen",
        "template_version": "1.0.0",
        "project_id": "visiondemo",
        "name_ja": "社員と経費の画面横断データ",
        "status": "approved",
        "parameters": [
            _parameter("department_id", "社員の所属部門 ID"),
            _parameter("business_date", "登録する業務日付"),
            _parameter("expense_status", "登録する経費ステータス"),
            _parameter("expense_amount", "登録する経費金額"),
            _parameter("expected_employee_name", "最終確認する社員名"),
        ],
        "entities": [
            {
                "entity_ref": "employee",
                "role": "master",
                "depends_on": [],
                "producer_step_id": "create-linked-employee",
                "cleanup_step_id": "delete-linked-employee",
                "identifier_variables": ["employee_id"],
            },
            {
                "entity_ref": "expense",
                "role": "detail",
                "depends_on": ["employee"],
                "producer_step_id": "create-linked-expense",
                "cleanup_step_id": "delete-linked-expense",
                "identifier_variables": ["expense_id"],
            },
        ],
        "preconditions": [
            _precondition(
                "department-required", "所属部門 ID が指定されている", "department_id", "exists"
            ),
            _precondition(
                "business-date-required", "業務日付が指定されている", "business_date", "non_blank"
            ),
            _precondition(
                "expense-status-supported",
                "経費ステータスが対象システムで利用できる",
                "expense_status",
                "one_of",
                ["申請中", "承認済", "差戻し"],
            ),
            _precondition(
                "expense-amount-positive",
                "経費金額が 0 より大きい",
                "expense_amount",
                "greater_than",
                0,
            ),
        ],
        "shared_variables": [
            _shared(
                "employee_no",
                "allocate-runtime-identities",
                ["create-linked-employee", "verify-employee-screen"],
            ),
            _shared(
                "employee_name",
                "allocate-runtime-identities",
                ["create-linked-employee", "verify-employee-screen", "verify-expense-screen"],
            ),
            _shared("employee_email", "allocate-runtime-identities", ["create-linked-employee"]),
            _shared(
                "expense_no",
                "allocate-runtime-identities",
                ["create-linked-expense", "verify-expense-screen"],
            ),
            _shared(
                "employee_id",
                "create-linked-employee",
                [
                    "create-linked-expense",
                    "verify-database-link",
                    "delete-linked-employee",
                    "verify-cleanup-database",
                ],
            ),
            _shared(
                "expense_id",
                "create-linked-expense",
                ["verify-database-link", "delete-linked-expense", "verify-cleanup-database"],
            ),
        ],
        "data_sets": [
            {
                "test_data_id": "data",
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
        "generation_flow": {
            "flow_id": "flow",
            "title": "社員画面と経費画面を同一データ系列で検証する",
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
        },
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


def _parameter(name: str, description_ja: str) -> dict[str, object]:
    return {"name": name, "required": True, "description_ja": description_ja}


def _precondition(
    precondition_id: str,
    description_ja: str,
    parameter: str,
    operator: str,
    expected: object | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "precondition_id": precondition_id,
        "description_ja": description_ja,
        "parameter": parameter,
        "operator": operator,
    }
    if expected is not None:
        value["expected"] = expected
    return value


def _shared(
    variable: str, producer_step_id: str, consumer_step_ids: list[str]
) -> dict[str, object]:
    return {
        "variable": variable,
        "producer_step_id": producer_step_id,
        "consumer_step_ids": consumer_step_ids,
    }


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
