from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from operamind.application.test_data_execution import (
    TestDataExecutionRequest as DataExecutionRequest,
)
from operamind.infrastructure.browser import LocalEvidenceStore
from operamind.infrastructure.test_data import target_data as target_data_module
from operamind.infrastructure.test_data.target_data import (
    ProjectSqlTestDataExecutor,
    TargetDataProfileRepository,
    TargetDataSecretStore,
    _validate_profile_input,
)
from operamind.infrastructure.test_data.target_database import (
    PostgresqlTargetDatabaseAdapter,
    TargetDatabaseAdapterRegistry,
    TargetDatabaseBinding,
    TargetDatabaseExecutionResult,
    default_target_database_adapters,
)
from tests.infrastructure.test_target_data_profile import _binding, _Context, _record


@dataclass(frozen=True)
class _FutureOracleAdapter:
    dialect: str = "oracle"

    def validate_connection_secret(self, value: str) -> None:
        if not value.startswith("future-oracle-secret:"):
            raise ValueError("future Oracle secret is invalid")

    def validate_binding_definition(self, value: Mapping[str, object]) -> None:
        if ":expense_id" not in str(value.get("statement_text", "")):
            raise ValueError("future Oracle binding must use named binds")

    def execute_binding(
        self,
        *,
        connection_secret: str,
        binding: TargetDatabaseBinding,
        parameters: Mapping[str, object],
    ) -> TargetDatabaseExecutionResult:
        del connection_secret, binding, parameters
        return TargetDatabaseExecutionResult(affected_rows=1, rows=({"ID": "EXP-001"},))


def test_default_registry_exposes_only_the_real_postgresql_adapter() -> None:
    registry = default_target_database_adapters()

    assert registry.dialects == ("postgresql",)
    assert isinstance(registry.require("postgresql"), PostgresqlTargetDatabaseAdapter)
    with pytest.raises(ValueError, match="no registered Adapter: oracle"):
        registry.require("oracle")


def test_registry_rejects_empty_duplicate_and_unsafe_dialects() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        TargetDatabaseAdapterRegistry(())
    with pytest.raises(ValueError, match="Duplicate"):
        TargetDatabaseAdapterRegistry(
            (PostgresqlTargetDatabaseAdapter(), PostgresqlTargetDatabaseAdapter())
        )
    with pytest.raises(ValueError, match="dialect is invalid"):
        TargetDatabaseAdapterRegistry((_FutureOracleAdapter(dialect="Oracle SQL"),))


def test_future_adapter_can_extend_profile_and_secret_without_changing_main_flow(
    tmp_path: Path,
) -> None:
    future = _FutureOracleAdapter()
    registry = TargetDatabaseAdapterRegistry((PostgresqlTargetDatabaseAdapter(), future))
    write = _binding("write")
    cleanup = _binding("cleanup")
    for value in (write, cleanup):
        value["statement_text"] = (
            "MERGE INTO expenses target USING (SELECT :expense_id expense_id FROM dual) "
            "source ON (target.expense_id = source.expense_id) WHEN MATCHED THEN UPDATE "
            "SET target.status = :status"
        )
        value["read_after_write_statement"] = (
            "SELECT expense_id FROM expenses WHERE expense_id = :expense_id "
            "AND status = :status"
        )

    _validate_profile_input(
        project_id="expense",
        connection_alias="expense_oracle",
        dialect="oracle",
        transaction_policy="per_binding_transaction",
        bindings=(write, cleanup),
        reviewed_by="qa",
        adapters=registry,
    )
    secrets = TargetDataSecretStore(tmp_path / "secrets", adapters=registry)
    secrets.put(
        project_id="expense",
        connection_alias="expense_oracle",
        connection_dsn="future-oracle-secret:opaque",
        dialect="oracle",
    )

    assert secrets.configured(
        project_id="expense",
        connection_alias="expense_oracle",
        dialect="oracle",
    )


def test_unregistered_future_dialect_is_blocked_before_secret_is_written(
    tmp_path: Path,
) -> None:
    secrets = TargetDataSecretStore(tmp_path / "secrets")

    with pytest.raises(ValueError, match="no registered Adapter: oracle"):
        secrets.put(
            project_id="expense",
            connection_alias="expense_oracle",
            connection_dsn="future-oracle-secret:opaque",
            dialect="oracle",
        )
    assert not (tmp_path / "secrets").exists()

    with pytest.raises(ValueError, match="no registered Adapter: oracle"):
        _validate_profile_input(
            project_id="expense",
            connection_alias="expense_oracle",
            dialect="oracle",
            transaction_policy="per_binding_transaction",
            bindings=(_binding("write"), _binding("cleanup")),
            reviewed_by="qa",
        )


def test_project_sql_executor_dispatches_through_registered_future_adapter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    registry = TargetDatabaseAdapterRegistry(
        (PostgresqlTargetDatabaseAdapter(), _FutureOracleAdapter())
    )
    binding = replace(_record(_binding("write")), dialect="oracle")
    secrets = TargetDataSecretStore(tmp_path / "secrets", adapters=registry)
    secrets.put(
        project_id="expense",
        connection_alias="expense_test_db",
        connection_dsn="future-oracle-secret:opaque",
        dialect="oracle",
    )
    monkeypatch.setattr(
        target_data_module.psycopg,
        "connect",
        lambda *_args, **_kwargs: _Context(),
    )
    monkeypatch.setattr(
        TargetDataProfileRepository,
        "binding",
        lambda _self, **_values: binding,
    )
    executor = ProjectSqlTestDataExecutor(
        control_database_url="postgresql://control:secret@127.0.0.1/control",
        evidence_store=LocalEvidenceStore(tmp_path / "evidence"),
        secret_store=secrets,
        adapters=registry,
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

    observed = result.source_values["database"]
    assert observed["database_dialect"] == "oracle"  # type: ignore[index]
    assert observed["rows"] == [{"ID": "EXP-001"}]  # type: ignore[index]
