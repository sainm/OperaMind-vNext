from __future__ import annotations

import html
import http.server
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from operamind.application.test_data_execution import (
    TestDataExecutionEngine as DataExecutionEngine,
)
from operamind.application.test_data_execution import (
    TestDataExecutionRequest as DataExecutionRequest,
)
from operamind.contracts import ContractCatalog
from operamind.infrastructure.browser import LocalEvidenceStore
from operamind.infrastructure.test_data import (
    PlaywrightUiTestDataExecutor,
    ProjectSqlTestDataExecutor,
    TargetDataProfileRepository,
    TargetDataSecretStore,
)

ROOT = Path(__file__).parents[2]
DATABASE_URL = os.getenv("OPERAMIND_TEST_DATABASE_URL")
pytestmark = pytest.mark.integration


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
@pytest.mark.parametrize("existing_rows", [0, 2])
def test_real_postgresql_identity_readback_blocks_non_unique_match(
    tmp_path: Path,
    existing_rows: int,
) -> None:
    assert DATABASE_URL is not None
    project_id, schema, target_role = _prepare_target_project(
        database_url=DATABASE_URL,
        secret_root=tmp_path / "secrets",
        binding_factory=lambda schema: [_read_binding("read_by_status", schema)],
    )
    try:
        with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
            for index in range(existing_rows):
                cursor.execute(
                    f"INSERT INTO {schema}.expenses (expense_number, status) VALUES (%s, %s)",
                    (f"EXP-MATCH-{index}", "RETURNED"),
                )
        engine = DataExecutionEngine(
            contracts=ContractCatalog.load(ROOT / "contracts"),
            executors={
                "sql": ProjectSqlTestDataExecutor(
                    control_database_url=DATABASE_URL,
                    evidence_store=LocalEvidenceStore(tmp_path / "evidence"),
                    secret_store=TargetDataSecretStore(tmp_path / "secrets"),
                )
            },
        )

        result = engine.execute(
            plan=_identity_plan(
                project_id=project_id,
                source_binding="read_by_status",
                binding_mode="adopted",
                include_ui=False,
            ),
            request=DataExecutionRequest(
                execution_result_id=f"result-{project_id}",
                run_id=f"run-{project_id}",
                project_id=project_id,
            ),
        )

        assert result["status"] == "blocked", result["failure_reasons"]
        assert result["data_bindings"] == []
        assert result["data_coverage"]["coverage_percent"] == 0
        assert result["data_coverage"]["status"] == "failed"
        assert any(
            f"count={existing_rows}" in reason for reason in result["failure_reasons"]
        )
    finally:
        _drop_target_project(DATABASE_URL, project_id, schema, target_role)


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_real_database_condition_failure_blocks_before_testplan_ui(
    tmp_path: Path,
) -> None:
    assert DATABASE_URL is not None
    project_id, schema, target_role = _prepare_target_project(
        database_url=DATABASE_URL,
        secret_root=tmp_path / "secrets",
        binding_factory=lambda schema: [
            _write_binding("create_expense", schema),
            _cleanup_binding("cleanup_expense", schema),
        ],
    )
    browser = PlaywrightUiTestDataExecutor(
        evidence_store=LocalEvidenceStore(tmp_path / "evidence")
    )
    try:
        result = DataExecutionEngine(
            contracts=ContractCatalog.load(ROOT / "contracts"),
            executors={
                "sql": ProjectSqlTestDataExecutor(
                    control_database_url=DATABASE_URL,
                    evidence_store=LocalEvidenceStore(tmp_path / "evidence"),
                    secret_store=TargetDataSecretStore(tmp_path / "secrets"),
                ),
                "ui": browser,
            },
        ).execute(
            plan=_identity_plan(
                project_id=project_id,
                source_binding="create_expense",
                binding_mode="generated",
                include_ui=True,
                coverage_expected_status="APPROVED",
            ),
            request=DataExecutionRequest(
                execution_result_id=f"result-{project_id}",
                run_id=f"run-{project_id}",
                project_id=project_id,
                base_url="http://127.0.0.1:1",
            ),
        )

        assert result["status"] == "blocked"
        assert result["data_coverage"]["coverage_percent"] == 0
        proof = {
            value["condition_id"]: value
            for value in result["data_coverage"]["proofs"]
        }["expense-returned-condition"]
        assert proof["actual"] == "RETURNED"
        assert proof["expected"] == "APPROVED"
        assert proof["status"] == "failed"
        assert result["flow_results"][0]["step_results"][0]["status"] == "blocked"
        assert not any(
            item["evidence_type"] == "screenshot" for item in result["evidence"]
        )
    finally:
        browser.close()
        _drop_target_project(DATABASE_URL, project_id, schema, target_role)


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
@pytest.mark.skipif(
    os.getenv("OPERAMIND_PLAYWRIGHT_EXECUTOR_LIVE") != "1",
    reason="OPERAMIND_PLAYWRIGHT_EXECUTOR_LIVE is not set",
)
@pytest.mark.parametrize(
    ("screen_row_mode", "expected_status", "expected_match_count"),
    [
        ("unique", "passed", 1),
        ("missing", "blocked", 0),
        ("duplicate", "blocked", 2),
    ],
)
def test_real_postgresql_binding_drives_exact_playwright_record_scope(
    tmp_path: Path,
    screen_row_mode: str,
    expected_status: str,
    expected_match_count: int,
) -> None:
    assert DATABASE_URL is not None
    project_id, schema, target_role = _prepare_target_project(
        database_url=DATABASE_URL,
        secret_root=tmp_path / "secrets",
        binding_factory=lambda schema: [
            _write_binding("create_expense", schema),
            _cleanup_binding("cleanup_expense", schema),
        ],
    )
    with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {schema}.expenses (expense_number, status) VALUES (%s, %s)",
            ("EXP-DISTRACTOR-999", "APPROVED"),
        )
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0),
        _database_page_handler(DATABASE_URL, schema, row_mode=screen_row_mode),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    browser = PlaywrightUiTestDataExecutor(
        evidence_store=LocalEvidenceStore(tmp_path / "evidence"),
        browser_channel=os.getenv("OPERAMIND_PLAYWRIGHT_CHANNEL", "chrome"),
    )
    try:
        engine = DataExecutionEngine(
            contracts=ContractCatalog.load(ROOT / "contracts"),
            executors={
                "sql": ProjectSqlTestDataExecutor(
                    control_database_url=DATABASE_URL,
                    evidence_store=LocalEvidenceStore(tmp_path / "evidence"),
                    secret_store=TargetDataSecretStore(tmp_path / "secrets"),
                ),
                "ui": browser,
            },
        )
        result = engine.execute(
            plan=_identity_plan(
                project_id=project_id,
                source_binding="create_expense",
                binding_mode="generated",
                include_ui=True,
            ),
            request=DataExecutionRequest(
                execution_result_id=f"result-{project_id}",
                run_id=f"run-{project_id}",
                project_id=project_id,
                base_url=f"http://127.0.0.1:{server.server_port}",
            ),
        )

        assert result["status"] == expected_status
        assert result["data_coverage"]["status"] == "passed"
        assert result["data_coverage"]["coverage_percent"] == 100
        proofs = {
            value["condition_id"]: value
            for value in result["data_coverage"]["proofs"]
        }
        assert {key: value["actual"] for key, value in proofs.items()} == {
            "expense-amount-boundary": 150,
            "expense-owner-relationship": 41,
            "expense-returned-condition": "RETURNED",
            "expense-title-condition": "東京旅費",
        }
        assert all(value["status"] == "passed" for value in proofs.values())
        binding = result["data_bindings"][0]
        assert binding["primary_key"]["value"] > 0
        assert binding["business_unique_keys"] == [
            {"name": "expense_number", "value": "EXP-BOUND-001"}
        ]
        assert binding["screen_locator"] == {
            "by": "css",
            "value": "[data-expense-number='EXP-BOUND-001']",
            "exact": True,
        }
        assert any(
            item["evidence_type"] == "data_binding"
            and item["content_digest"] == binding["content_digest"]
            for item in result["evidence"]
        )
        bound_step = result["flow_results"][0]["step_results"][2]
        assert bound_step["status"] == expected_status
        if expected_status == "blocked":
            assert any(
                f"count={expected_match_count}" in reason
                for reason in result["failure_reasons"]
            )
        with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT expense_number, status FROM {schema}.expenses ORDER BY expense_number"
            )
            assert cursor.fetchall() == [("EXP-DISTRACTOR-999", "APPROVED")]
    finally:
        browser.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        _drop_target_project(DATABASE_URL, project_id, schema, target_role)


def _prepare_target_project(
    *,
    database_url: str,
    secret_root: Path,
    binding_factory: Callable[[str], list[dict[str, object]]],
) -> tuple[str, str, str | None]:
    suffix = uuid4().hex
    project_id = f"identity-binding-{suffix}"
    schema = f"td_{suffix}"
    connection_parameters = conninfo_to_dict(database_url)
    target_role: str | None = None
    target_dsn = database_url
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(f"CREATE SCHEMA {schema}")
        cursor.execute(
            f"""
            CREATE TABLE {schema}.expenses (
                id bigserial PRIMARY KEY,
                expense_number varchar(40) NOT NULL UNIQUE,
                status varchar(20) NOT NULL,
                title varchar(80) NOT NULL DEFAULT '東京旅費',
                amount integer NOT NULL DEFAULT 150,
                employee_id bigint NOT NULL DEFAULT 41,
                owner_id bigint NOT NULL DEFAULT 41
            )
            """
        )
        if not connection_parameters.get("password"):
            target_role = f"td_role_{suffix[:16]}"
            target_password = uuid4().hex
            cursor.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(target_role),
                    sql.Literal(target_password),
                )
            )
            cursor.execute(f"GRANT USAGE ON SCHEMA {schema} TO {target_role}")
            cursor.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON {schema}.expenses TO {target_role}"
            )
            cursor.execute(
                f"GRANT USAGE, SELECT ON SEQUENCE {schema}.expenses_id_seq TO {target_role}"
            )
            target_dsn = (
                f"postgresql://{quote(target_role)}:{quote(target_password)}@127.0.0.1:"
                f"{connection_parameters.get('port', '5432')}/"
                f"{quote(str(connection_parameters['dbname']))}"
            )
        cursor.execute(
            "INSERT INTO projects (project_id, name) VALUES (%s, 'Identity Binding')",
            (project_id,),
        )
        cursor.execute(
            """
            INSERT INTO project_workspaces (
                project_id, workspace_root, source_control_kind, configured_by
            ) VALUES (%s, '/tmp/identity-binding', 'git', 'integration')
            """,
            (project_id,),
        )
        TargetDataProfileRepository(connection).replace(
            project_id=project_id,
            connection_alias="target_test_db",
            transaction_policy="per_binding_transaction",
            bindings=binding_factory(schema),
            reviewed_by="integration",
        )
    TargetDataSecretStore(secret_root).put(
        project_id=project_id,
        connection_alias="target_test_db",
        connection_dsn=target_dsn,
    )
    return project_id, schema, target_role


def _drop_target_project(
    database_url: str,
    project_id: str,
    schema: str,
    target_role: str | None,
) -> None:
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM project_target_data_profiles WHERE project_id = %s",
            (project_id,),
        )
        cursor.execute("DELETE FROM project_workspaces WHERE project_id = %s", (project_id,))
        cursor.execute("DELETE FROM projects WHERE project_id = %s", (project_id,))
        cursor.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        if target_role is not None:
            cursor.execute(f"DROP ROLE IF EXISTS {target_role}")


def _identity_contract() -> dict[str, object]:
    return {
        "primary_key": "id",
        "business_unique_keys": ["expense_number"],
        "screen_key": "expense_number",
        "coverage_columns": ["status", "title", "amount", "employee_id", "owner_id"],
    }


def _constraints(*names: str) -> dict[str, object]:
    return {
        name: {
            "type": "string",
            "required": True,
            "max_length": 40 if name == "expense_number" else 20,
        }
        for name in names
    }


def _write_binding(binding_id: str, schema: str) -> dict[str, object]:
    return {
        "query_binding_id": binding_id,
        "operation": "write",
        "statement_text": (
            f"INSERT INTO {schema}.expenses (expense_number, status) "
            "VALUES (%(expense_number)s, %(status)s)"
        ),
        "target_schema": schema,
        "target_table": "expenses",
        "parameter_columns": {"expense_number": "expense_number", "status": "status"},
        "input_constraints": _constraints("expense_number", "status"),
        "read_after_write_statement": (
            f"SELECT id, expense_number, status, title, amount, employee_id, owner_id "
            f"FROM {schema}.expenses "
            "WHERE expense_number = %(expense_number)s AND status = %(status)s"
        ),
        "read_assertion": {"mode": "row_count", "count": 1},
        "identity_contract": _identity_contract(),
        "cleanup_binding_id": "cleanup_expense",
        "idempotency_policy": "natural_key",
    }


def _read_binding(binding_id: str, schema: str) -> dict[str, object]:
    statement = (
        f"SELECT id, expense_number, status, title, amount, employee_id, owner_id "
        f"FROM {schema}.expenses "
        "WHERE status = %(status)s ORDER BY id"
    )
    return {
        "query_binding_id": binding_id,
        "operation": "read",
        "statement_text": statement,
        "target_schema": schema,
        "target_table": "expenses",
        "parameter_columns": {"status": "status"},
        "input_constraints": _constraints("status"),
        "read_after_write_statement": statement,
        "read_assertion": {"mode": "row_count", "count": 1},
        "identity_contract": _identity_contract(),
        "cleanup_binding_id": None,
        "idempotency_policy": "read_only",
    }


def _cleanup_binding(binding_id: str, schema: str) -> dict[str, object]:
    return {
        "query_binding_id": binding_id,
        "operation": "cleanup",
        "statement_text": (
            f"DELETE FROM {schema}.expenses "
            "WHERE expense_number = %(expense_number)s AND status = %(status)s"
        ),
        "target_schema": schema,
        "target_table": "expenses",
        "parameter_columns": {"expense_number": "expense_number", "status": "status"},
        "input_constraints": _constraints("expense_number", "status"),
        "read_after_write_statement": (
            f"SELECT id, expense_number, status, title, amount, employee_id, owner_id "
            f"FROM {schema}.expenses "
            "WHERE expense_number = %(expense_number)s AND status = %(status)s"
        ),
        "read_assertion": {"mode": "rows_absent"},
        "identity_contract": _identity_contract(),
        "cleanup_binding_id": None,
        "idempotency_policy": "natural_key",
    }


def _identity_plan(
    *,
    project_id: str,
    source_binding: str,
    binding_mode: str,
    include_ui: bool,
    coverage_expected_status: str = "RETURNED",
) -> dict[str, Any]:
    generated = binding_mode == "generated"
    inputs = (
        {"expense_number": "EXP-BOUND-001", "status": "RETURNED"}
        if generated
        else {"status": "RETURNED"}
    )
    steps: list[dict[str, Any]] = [
        {
            "step_id": "read-and-bind-expense",
            "sequence": 1,
            "channel": "sql",
            "business_action": "対象経費を DB から一意に読み戻す",
            "data_effect": "creates" if generated else "none",
            "test_step_refs": [],
            "target": source_binding,
            "inputs": inputs,
            "depends_on": [],
            "output_bindings": (
                [
                    {
                        "variable": "created_expense_id",
                        "source": "database",
                        "path": "rows[0].id",
                        "required": True,
                    }
                ]
                if generated
                else []
            ),
            "postconditions": [
                {
                    "assertion_id": "identity-readback-passed",
                    "observe_via": "database",
                    "subject": "read_after_write",
                    "operator": "equals",
                    "expected": "passed",
                }
            ],
        }
    ]
    if include_ui:
        steps.extend(
            [
                {
                    "step_id": "open-expense-list",
                    "sequence": 2,
                    "channel": "ui",
                    "business_action": "経費一覧を開く",
                    "test_step_refs": ["verify-returned-status"],
                    "screen_ref": "expense-list",
                    "ui_action_ref": "open",
                    "operation_scope": "screen",
                    "playwright": {
                        "action": "goto",
                        "path": "/",
                        "observations": [{"key": "title", "kind": "title"}],
                        "mask_locators": [],
                    },
                    "inputs": {},
                    "depends_on": ["read-and-bind-expense"],
                    "output_bindings": [],
                    "postconditions": [
                        {
                            "assertion_id": "expense-list-opened",
                            "observe_via": "ui",
                            "subject": "title",
                            "operator": "equals",
                            "expected": "Expense list",
                        }
                    ],
                },
                {
                    "step_id": "verify-bound-expense-row",
                    "sequence": 3,
                    "channel": "ui",
                    "business_action": "結合した経費行の状態を確認する",
                    "test_step_refs": [],
                    "screen_ref": "expense-list",
                    "ui_action_ref": "verify-bound-row",
                    "operation_scope": "bound_record",
                    "data_binding_ref": "expense-bound",
                    "playwright": {
                        "action": "click",
                        "locator": {
                            "by": "css",
                            "value": ".select-record",
                            "exact": True,
                        },
                        "observations": [
                            {
                                "key": "status",
                                "kind": "text",
                                "locator": {"by": "css", "value": ".status", "exact": True},
                            },
                            {
                                "key": "selected_record",
                                "kind": "text",
                                "locator": {
                                    "by": "css",
                                    "value": ".selected-record",
                                    "exact": True,
                                },
                            }
                        ],
                        "mask_locators": [],
                    },
                    "inputs": {},
                    "depends_on": ["open-expense-list"],
                    "output_bindings": [],
                    "postconditions": [
                        {
                            "assertion_id": "bound-expense-returned",
                            "observe_via": "ui",
                            "subject": "status",
                            "operator": "equals",
                            "expected": "RETURNED",
                        },
                        {
                            "assertion_id": "only-bound-expense-selected",
                            "observe_via": "ui",
                            "subject": "selected_record",
                            "operator": "equals",
                            "expected": "selected",
                        }
                    ],
                },
            ]
        )
    cleanup = (
        [
            {
                "step_id": "cleanup-expense",
                "sequence": 1,
                "channel": "sql",
                "business_action": "作成した経費を削除する",
                "data_effect": "deletes",
                "test_step_refs": [],
                "target": "cleanup_expense",
                "inputs": {"expense_number": "EXP-BOUND-001", "status": "RETURNED"},
                "depends_on": ["read-and-bind-expense"],
                "output_bindings": [],
                "postconditions": [
                    {
                        "assertion_id": "expense-cleaned",
                        "observe_via": "database",
                        "subject": "row_count",
                        "operator": "equals",
                        "expected": 0,
                    }
                ],
            }
        ]
        if generated
        else []
    )
    return {
        "artifact_type": "TestDataPlan",
        "schema_version": "v2",
        "test_data_plan_id": f"plan-{project_id}",
        "test_plan_id": f"test-plan-{project_id}",
        "project_id": project_id,
        "status": "ready",
        "data_sets": [
            {
                "test_data_id": "expense-bound",
                "test_case_refs": ["expense-ui"],
                "setup_actions": [],
                "cleanup_policy": "delete_after_run" if generated else "retain",
                "identity_binding": {
                    "binding_mode": binding_mode,
                    "source_flow_id": "identity-flow",
                    "source_step_id": "read-and-bind-expense",
                    "primary_key": {
                        "name": "id",
                        "source": "database",
                        "path": "rows[0].id",
                    },
                    "business_unique_keys": [
                        {
                            "name": "expense_number",
                            "source": "database",
                            "path": "rows[0].expense_number",
                        }
                    ],
                    "screen_key": {
                        "name": "expense_number",
                        "source": "database",
                        "path": "rows[0].expense_number",
                        "locator_template": {
                            "by": "css",
                            "value": "[data-expense-number='{{value}}']",
                            "exact": True,
                        },
                    },
                    "match_count": {"source": "database", "path": "row_count"},
                },
                "coverage_conditions": [
                    {
                        "condition_id": "expense-returned-condition",
                        "criterion_ref": "expense-returned-criterion",
                        "test_case_ref": "expense-ui",
                        "test_data_id": "expense-bound",
                        "condition_kind": "status",
                        "source_flow_id": "identity-flow",
                        "source_step_id": "read-and-bind-expense",
                        "path": "rows[0].status",
                        "operator": "equals",
                        "expected": coverage_expected_status,
                    },
                    {
                        "condition_id": "expense-title-condition",
                        "criterion_ref": "expense-returned-criterion",
                        "test_case_ref": "expense-ui",
                        "test_data_id": "expense-bound",
                        "condition_kind": "field",
                        "source_flow_id": "identity-flow",
                        "source_step_id": "read-and-bind-expense",
                        "path": "rows[0].title",
                        "operator": "contains",
                        "expected": "旅費",
                    },
                    {
                        "condition_id": "expense-amount-boundary",
                        "criterion_ref": "expense-returned-criterion",
                        "test_case_ref": "expense-ui",
                        "test_data_id": "expense-bound",
                        "condition_kind": "boundary",
                        "source_flow_id": "identity-flow",
                        "source_step_id": "read-and-bind-expense",
                        "path": "rows[0].amount",
                        "operator": "between",
                        "expected": [100, 200],
                    },
                    {
                        "condition_id": "expense-owner-relationship",
                        "criterion_ref": "expense-returned-criterion",
                        "test_case_ref": "expense-ui",
                        "test_data_id": "expense-bound",
                        "condition_kind": "relationship",
                        "source_flow_id": "identity-flow",
                        "source_step_id": "read-and-bind-expense",
                        "path": "rows[0].employee_id",
                        "operator": "equals_path",
                        "expected_path": "rows[0].owner_id",
                    },
                ],
            }
        ],
        "generation_flows": [
            {
                "flow_id": "identity-flow",
                "title": "Bind one real target record",
                "test_data_refs": ["expense-bound"],
                "test_case_refs": ["expense-ui"],
                "steps": steps,
                "final_assertions": [
                    {
                        "assertion_id": "identity-flow-finished",
                        "observe_via": "test",
                        "subject": "expense-ui",
                        "operator": "satisfies",
                        "expected": "passed",
                    }
                ],
                "cleanup_policy": "delete_after_run" if generated else "retain",
                "cleanup_steps": cleanup,
            }
        ],
        "blocking_reasons": [],
    }


def _database_page_handler(
    database_url: str,
    schema: str,
    *,
    row_mode: str,
) -> type[http.server.BaseHTTPRequestHandler]:
    class DatabasePageHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT expense_number, status FROM {schema}.expenses ORDER BY id"
                )
                rows = cursor.fetchall()
            if row_mode == "missing":
                rows = []
            elif row_mode == "duplicate":
                rows = [*rows, *rows]
            rendered_rows = "".join(
                (
                    f"<tr data-expense-number='{html.escape(str(number), quote=True)}'>"
                    f"<td>{html.escape(str(number))}</td>"
                    f"<td class='status'>{html.escape(str(status))}</td>"
                    "<td><button class='select-record' type='button' "
                    "onclick=\"this.parentElement.querySelector('.selected-record')"
                    ".textContent='selected'\">select</button>"
                    "<span class='selected-record'></span></td></tr>"
                )
                for number, status in rows
            )
            body = (
                "<!doctype html><html><head><title>Expense list</title></head>"
                f"<body><table><tbody>{rendered_rows}</tbody></table></body></html>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    return DatabasePageHandler
