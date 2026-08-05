from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from operamind.application.copilot_coding_task import _public_target_data_profile
from operamind.application.test_data_execution import (
    TestDataExecutionRequest as DataExecutionRequest,
)
from operamind.infrastructure.browser import LocalEvidenceStore
from operamind.infrastructure.test_data import target_data as target_data_module
from operamind.infrastructure.test_data.target_data import (
    ProjectSqlTestDataExecutor,
    TargetDataBinding,
    TargetDataProfile,
    TargetDataProfileRepository,
    TargetDataSecretStore,
    _validate_binding_inputs,
    _validate_profile_input,
)


def _binding(operation: str = "write") -> dict[str, object]:
    binding_id = "upsert_expense" if operation == "write" else "cleanup_expense"
    statement = (
        "INSERT INTO expenses (expense_id, status) VALUES (%(expense_id)s, %(status)s) "
        "ON CONFLICT (expense_id) DO UPDATE SET status = EXCLUDED.status"
        if operation == "write"
        else "DELETE FROM expenses WHERE expense_id = %(expense_id)s AND status = %(status)s"
    )
    return {
        "query_binding_id": binding_id,
        "operation": operation,
        "statement_text": statement,
        "target_schema": "public",
        "target_table": "expenses",
        "parameter_columns": {"expense_id": "expense_id", "status": "status"},
        "input_constraints": {
            "expense_id": {
                "type": "string",
                "required": True,
                "max_length": 20,
                "pattern": "^EXP-[0-9]{3}$",
            },
            "status": {
                "type": "string",
                "required": True,
                "max_length": 20,
                "enum": ["DRAFT", "SUBMITTED"],
            },
        },
        "read_after_write_statement": (
            "SELECT expense_id, expense_number, status FROM expenses "
            "WHERE expense_id = %(expense_id)s AND status = %(status)s"
        ),
        "read_assertion": {
            "mode": "row_count" if operation == "write" else "rows_absent",
            **({"count": 1} if operation == "write" else {}),
        },
        "identity_contract": {
            "primary_key": "expense_id",
            "business_unique_keys": ["expense_number"],
            "screen_key": "expense_number",
            "coverage_columns": ["status"],
        },
        "cleanup_binding_id": "cleanup_expense" if operation == "write" else None,
        "idempotency_policy": "upsert" if operation == "write" else "natural_key",
    }


def _record(value: dict[str, object]) -> TargetDataBinding:
    return TargetDataBinding(
        project_id="expense",
        connection_alias="expense_test_db",
        dialect="postgresql",
        query_binding_id=str(value["query_binding_id"]),
        operation=str(value["operation"]),
        statement_text=str(value["statement_text"]),
        target_schema=str(value["target_schema"]),
        target_table=str(value["target_table"]),
        parameter_columns=value["parameter_columns"],  # type: ignore[arg-type]
        input_constraints=value["input_constraints"],  # type: ignore[arg-type]
        read_after_write_statement=str(value["read_after_write_statement"]),
        read_assertion=value["read_assertion"],  # type: ignore[arg-type]
        identity_contract=value["identity_contract"],  # type: ignore[arg-type]
        cleanup_binding_id=(
            str(value["cleanup_binding_id"])
            if value.get("cleanup_binding_id") is not None
            else None
        ),
        idempotency_policy=str(value["idempotency_policy"]),
    )


def test_secret_store_keeps_password_outside_workspace_and_owner_only(tmp_path: Path) -> None:
    store = TargetDataSecretStore(tmp_path / "secrets")
    dsn = "postgresql://tester:local-password@127.0.0.1:5432/expense"

    store.put(project_id="expense", connection_alias="expense_test_db", connection_dsn=dsn)

    assert store.get(project_id="expense", connection_alias="expense_test_db") == dsn
    secret_files = list((tmp_path / "secrets").glob("*.secret"))
    assert len(secret_files) == 1
    assert "expense" not in secret_files[0].name
    assert "local-password" not in secret_files[0].name
    if os.name != "nt":
        assert secret_files[0].stat().st_mode & 0o777 == 0o600
    store.delete(project_id="expense", connection_alias="expense_test_db")
    assert not store.configured(project_id="expense", connection_alias="expense_test_db")


@pytest.mark.parametrize(
    "dsn",
    (
        "mysql://tester:secret@127.0.0.1/expense",
        "oracle://tester:secret@127.0.0.1/expense",
    ),
)
def test_secret_store_rejects_non_postgresql_connections(
    tmp_path: Path, dsn: str
) -> None:
    store = TargetDataSecretStore(tmp_path / "secrets")
    with pytest.raises(ValueError, match="PostgreSQL URL"):
        store.put(
            project_id="expense",
            connection_alias="expense_test_db",
            connection_dsn=dsn,
        )


def test_secret_store_rejects_passwordless_connections(tmp_path: Path) -> None:
    store = TargetDataSecretStore(tmp_path / "secrets")
    with pytest.raises(ValueError, match="include a password"):
        store.put(
            project_id="expense",
            connection_alias="expense_test_db",
            connection_dsn="postgresql://tester@127.0.0.1/expense",
        )


def test_profile_requires_named_inputs_cleanup_readback_and_idempotency() -> None:
    bindings = [_binding("write"), _binding("cleanup")]
    _validate_profile_input(
        project_id="expense",
        connection_alias="expense_test_db",
        transaction_policy="per_binding_transaction",
        bindings=bindings,
        reviewed_by="qa",
    )

    invalid = _binding("write")
    invalid["cleanup_binding_id"] = "missing_cleanup"
    with pytest.raises(ValueError, match="reviewed cleanup binding"):
        _validate_profile_input(
            project_id="expense",
            connection_alias="expense_test_db",
            transaction_policy="per_binding_transaction",
            bindings=[invalid, _binding("cleanup")],
            reviewed_by="qa",
        )

    unsafe_cleanup = _binding("cleanup")
    unsafe_cleanup["read_assertion"] = {"mode": "rows_present"}
    with pytest.raises(ValueError, match="must prove that the target row is absent"):
        _validate_profile_input(
            project_id="expense",
            connection_alias="expense_test_db",
            transaction_policy="per_binding_transaction",
            bindings=[_binding("write"), unsafe_cleanup],
            reviewed_by="qa",
        )


def test_binding_inputs_enforce_field_business_and_enum_constraints() -> None:
    binding = _record(_binding("write"))

    assert _validate_binding_inputs(
        binding, {"expense_id": "EXP-001", "status": "SUBMITTED"}
    ) == {"expense_id": "EXP-001", "status": "SUBMITTED"}
    with pytest.raises(ValueError, match="violates pattern"):
        _validate_binding_inputs(binding, {"expense_id": "bad", "status": "SUBMITTED"})
    with pytest.raises(ValueError, match="outside reviewed enum"):
        _validate_binding_inputs(binding, {"expense_id": "EXP-001", "status": "UNKNOWN"})
    with pytest.raises(ValueError, match="match reviewed parameters exactly"):
        _validate_binding_inputs(
            binding,
            {
                "expense_id": "EXP-001",
                "status": "SUBMITTED",
                "query": "DELETE FROM expenses",
            },
        )


def test_plan_coverage_condition_must_use_a_reviewed_readback_column() -> None:
    binding = _record(_binding("write"))
    plan = {
        "data_sets": [
            {
                "test_data_id": "expense-returned-data",
                "identity_binding": {
                    "provider": {"type": "database", "provider_ref": "database.v1"},
                    "source_flow_id": "expense-flow",
                    "source_step_id": "read-expense",
                    "primary_key": {
                        "name": "expense_id",
                        "source": "database",
                        "path": "rows[0].expense_id",
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
                    },
                },
                "coverage_conditions": [
                        {
                            "condition_id": "unreviewed-owner-condition",
                            "source_flow_id": "expense-flow",
                            "source_step_id": "read-expense",
                            "path": "rows[0].owner_password",
                        }
                ],
            }
        ],
        "generation_flows": [
            {
                "flow_id": "expense-flow",
                "steps": [
                    {
                        "step_id": "read-expense",
                        "channel": "sql",
                        "target": "upsert_expense",
                    }
                ],
                "cleanup_steps": [],
            }
        ],
    }

    reasons = target_data_module._validate_plan_identity_contracts(
        plan=plan,
        bindings={"upsert_expense": binding},
    )

    assert reasons == [
        "expense-returned-data: coverage condition column is not reviewed by "
        "upsert_expense: owner_password"
    ]


@pytest.mark.parametrize("provider_type", ["api", "hybrid"])
def test_non_database_identity_uses_its_separate_sql_step_for_coverage_validation(
    provider_type: str,
) -> None:
    identity_source = "api" if provider_type == "api" else "ui"
    identity_step = "read-api" if provider_type == "api" else "read-ui"
    channel = "http" if provider_type == "api" else "ui"
    identity = {
        "provider": {"type": provider_type, "provider_ref": f"{provider_type}.v1"},
        "source_flow_id": "expense-flow",
        "source_step_id": identity_step,
        "primary_key": {
            "name": "expense_id",
            "source": "database" if provider_type == "hybrid" else identity_source,
            "path": "rows[0].expense_id" if provider_type == "hybrid" else "record.id",
        },
        "business_unique_keys": [
            {
                "name": "expense_number",
                "source": "database" if provider_type == "hybrid" else identity_source,
                "path": (
                    "rows[0].expense_number"
                    if provider_type == "hybrid"
                    else "record.expense_number"
                ),
            }
        ],
        "screen_key": {
            "name": "expense_number",
            "source": identity_source,
            "path": "record.expense_number",
        },
        "match_count": {
            "source": "database" if provider_type == "hybrid" else identity_source,
            "path": "row_count" if provider_type == "hybrid" else "match_count",
        },
    }
    plan = {
        "data_sets": [
            {
                "test_data_id": "expense-returned-data",
                "identity_binding": identity,
                "coverage_conditions": [
                    {
                        "condition_id": "status-condition",
                        "source_flow_id": "expense-flow",
                        "source_step_id": "read-database",
                        "path": "rows[0].status",
                    }
                ],
            }
        ],
        "generation_flows": [
            {
                "flow_id": "expense-flow",
                "steps": [
                    {
                        "step_id": "read-database",
                        "channel": "sql",
                        "target": "upsert_expense",
                    },
                    {
                        "step_id": identity_step,
                        "channel": channel,
                        "target": "GET /api/expense" if channel == "http" else None,
                    },
                ],
                "cleanup_steps": [],
            }
        ],
    }

    reasons = target_data_module._validate_plan_identity_contracts(
        plan=plan,
        bindings={"upsert_expense": _record(_binding("write"))},
    )

    assert reasons == []


def test_copilot_target_data_context_excludes_statements_and_connection_secret() -> None:
    binding = _record(_binding("write"))
    profile = TargetDataProfile(
        project_id="expense",
        connection_alias="expense_test_db",
        dialect="postgresql",
        transaction_policy="per_binding_transaction",
        reviewed_by="qa",
        reviewed_at=datetime.now(UTC),
        bindings=(binding,),
    )

    context = _public_target_data_profile(profile)
    serialized = str(context)

    assert context["available"] is True
    assert context["dialect"] == "postgresql"
    assert "upsert_expense" in serialized
    assert "expense_number" in serialized
    assert "statement_text" not in serialized
    assert "INSERT INTO" not in serialized
    assert "password" not in serialized.lower()


class _Context:
    def __enter__(self) -> _Context:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _Cursor(_Context):
    rowcount = 1

    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def execute(self, statement: str, parameters: object) -> None:
        assert "local-password" not in statement
        if "information_schema.columns" in statement:
            self.rows = [
                {
                    "column_name": "expense_id",
                    "is_nullable": "NO",
                    "data_type": "character varying",
                    "character_maximum_length": 20,
                },
                {
                    "column_name": "status",
                    "is_nullable": "NO",
                    "data_type": "character varying",
                    "character_maximum_length": 20,
                },
                {
                    "column_name": "expense_number",
                    "is_nullable": "NO",
                    "data_type": "character varying",
                    "character_maximum_length": 40,
                },
            ]
        elif "pg_catalog.pg_constraint" in statement:
            self.rows = [
                {
                    "constraint_type": "p",
                    "constraint_name": "expenses_pkey",
                    "column_name": "expense_id",
                    "ordinal_position": 1,
                },
                {
                    "constraint_type": "u",
                    "constraint_name": "expenses_number_key",
                    "column_name": "expense_number",
                    "ordinal_position": 1,
                },
            ]
        elif statement.startswith("SELECT expense_id"):
            self.rows = [
                {
                    "expense_id": "EXP-001",
                    "expense_number": "EX-NO-001",
                    "status": "SUBMITTED",
                }
            ]

    def fetchall(self) -> list[dict[str, object]]:
        return self.rows


class _TargetConnection(_Context):
    def __init__(self) -> None:
        self.sql_cursor = _Cursor()

    def cursor(self) -> _Cursor:
        return self.sql_cursor


def test_project_sql_executor_uses_reviewed_binding_and_records_readback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    binding = _record(_binding("write"))
    secret_store = TargetDataSecretStore(tmp_path / "secrets")
    target_dsn = "postgresql://tester:local-password@127.0.0.1:5432/expense"
    secret_store.put(
        project_id="expense",
        connection_alias="expense_test_db",
        connection_dsn=target_dsn,
    )
    connections: list[str] = []

    def connect(dsn: str, **_kwargs: object) -> _Context:
        connections.append(dsn)
        return _Context() if len(connections) == 1 else _TargetConnection()

    monkeypatch.setattr(target_data_module.psycopg, "connect", connect)
    monkeypatch.setattr(
        TargetDataProfileRepository,
        "binding",
        lambda _self, **_values: binding,
    )
    executor = ProjectSqlTestDataExecutor(
        control_database_url="postgresql://control:secret@127.0.0.1/control",
        evidence_store=LocalEvidenceStore(tmp_path / "evidence"),
        secret_store=secret_store,
    )

    result = executor.execute(
        request=DataExecutionRequest(
            execution_result_id="result-1",
            run_id="run-1",
            project_id="expense",
        ),
        flow_id="expense-flow",
        step={"step_id": "create-expense", "target": "upsert_expense"},
        resolved_inputs={"expense_id": "EXP-001", "status": "SUBMITTED"},
        variables={},
        phase="setup",
    )

    assert connections == ["postgresql://control:secret@127.0.0.1/control", target_dsn]
    assert result.source_values["database"]["read_after_write"] == "passed"  # type: ignore[index]
    evidence_text = next((tmp_path / "evidence").rglob("*.json")).read_text(encoding="utf-8")
    assert "local-password" not in evidence_text


def test_project_sql_executor_rejects_raw_sql_input_before_target_connection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    binding = _record(_binding("write"))
    monkeypatch.setattr(target_data_module.psycopg, "connect", lambda *_args, **_kw: _Context())
    monkeypatch.setattr(
        TargetDataProfileRepository,
        "binding",
        lambda _self, **_values: binding,
    )
    executor = ProjectSqlTestDataExecutor(
        control_database_url="postgresql://control:secret@127.0.0.1/control",
        evidence_store=LocalEvidenceStore(tmp_path / "evidence"),
        secret_store=TargetDataSecretStore(tmp_path / "secrets"),
    )

    with pytest.raises(ValueError, match="forbidden raw SQL"):
        executor.execute(
            request=DataExecutionRequest(
                execution_result_id="result-1", run_id="run-1", project_id="expense"
            ),
            flow_id="expense-flow",
            step={"step_id": "create-expense", "target": "upsert_expense"},
            resolved_inputs={
                "expense_id": "EXP-001",
                "status": "SUBMITTED",
                "sql": "DROP TABLE expenses",
            },
            variables={},
            phase="setup",
        )
