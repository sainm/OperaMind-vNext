from __future__ import annotations

import os
from collections.abc import Mapping
from uuid import uuid4

import psycopg
import pytest

from operamind.infrastructure.test_data.target_data import TargetDataProfileRepository
from operamind.infrastructure.test_data.target_database import (
    PostgresqlTargetDatabaseAdapter,
    TargetDatabaseAdapterRegistry,
    TargetDatabaseBinding,
    TargetDatabaseExecutionResult,
)
from tests.infrastructure.test_target_data_profile import _binding

DATABASE_URL = os.getenv("OPERAMIND_TEST_DATABASE_URL")
pytestmark = pytest.mark.integration


class _ReservedDialectAdapter:
    dialect = "reserved_db"

    def validate_connection_secret(self, value: str) -> None:
        del value

    def validate_binding_definition(self, value: Mapping[str, object]) -> None:
        del value

    def execute_binding(
        self,
        *,
        connection_secret: str,
        binding: TargetDatabaseBinding,
        parameters: Mapping[str, object],
    ) -> TargetDatabaseExecutionResult:
        del connection_secret, binding, parameters
        raise AssertionError("Reserved Adapter must not execute in this persistence test")


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_reviewed_project_profile_drives_plan_validation_without_storing_secret() -> None:
    assert DATABASE_URL is not None
    project_id = f"target-data-{uuid4().hex}"
    with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO projects (project_id, name) VALUES (%s, 'Target Data')",
            (project_id,),
        )
        cursor.execute(
            """
            INSERT INTO project_workspaces (
                project_id, workspace_root, source_control_kind, configured_by
            ) VALUES (%s, '/tmp/target-data', 'git', 'qa')
            """,
            (project_id,),
        )
        repository = TargetDataProfileRepository(connection)
        profile = repository.replace(
            project_id=project_id,
            connection_alias="expense_test_db",
            transaction_policy="per_binding_transaction",
            bindings=[_binding("write"), _binding("cleanup")],
            reviewed_by="qa",
        )

        assert profile.connection_alias == "expense_test_db"
        assert [value.query_binding_id for value in profile.bindings] == [
            "cleanup_expense",
            "upsert_expense",
        ]
        assert "password" not in str(profile.public_view(include_statements=True)).lower()
        plan = {
            "generation_flows": [
                {
                    "flow_id": "expense-flow",
                    "steps": [
                        {
                            "step_id": "create",
                            "channel": "sql",
                            "target": "upsert_expense",
                            "data_effect": "creates",
                        }
                    ],
                    "cleanup_steps": [
                        {
                            "step_id": "cleanup",
                            "channel": "sql",
                            "target": "cleanup_expense",
                        }
                    ],
                }
            ]
        }
        assert repository.validate_plan(project_id=project_id, plan=plan) == []
        plan["generation_flows"][0]["cleanup_steps"] = []  # type: ignore[index]
        assert repository.validate_plan(project_id=project_id, plan=plan) == [
            "expense-flow/create: reviewed cleanup binding 'cleanup_expense' is not present "
            "in cleanup_steps"
        ]
        connection.rollback()


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_sql_plan_without_target_profile_fails_closed() -> None:
    assert DATABASE_URL is not None
    project_id = f"target-data-missing-{uuid4().hex}"
    with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO projects (project_id, name) VALUES (%s, 'Missing')",
            (project_id,),
        )
        cursor.execute(
            """
            INSERT INTO project_workspaces (
                project_id, workspace_root, source_control_kind, configured_by
            ) VALUES (%s, '/tmp/target-data-missing', 'git', 'qa')
            """,
            (project_id,),
        )
        reasons = TargetDataProfileRepository(connection).validate_plan(
            project_id=project_id,
            plan={
                "generation_flows": [
                    {
                        "flow_id": "missing-flow",
                        "steps": [{"step_id": "seed", "channel": "sql"}],
                        "cleanup_steps": [],
                    }
                ]
            },
        )
        assert reasons == [
            "Project has no reviewed Target Data Profile for SQL TestDataPlan execution"
        ]
        connection.rollback()


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_profile_persists_registered_future_dialect_without_a_new_control_db_migration() -> None:
    assert DATABASE_URL is not None
    project_id = f"target-data-adapter-{uuid4().hex}"
    registry = TargetDatabaseAdapterRegistry(
        (PostgresqlTargetDatabaseAdapter(), _ReservedDialectAdapter())
    )
    with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO projects (project_id, name) VALUES (%s, 'Adapter')",
            (project_id,),
        )
        cursor.execute(
            """
            INSERT INTO project_workspaces (
                project_id, workspace_root, source_control_kind, configured_by
            ) VALUES (%s, '/tmp/target-data-adapter', 'git', 'qa')
            """,
            (project_id,),
        )
        repository = TargetDataProfileRepository(connection, adapters=registry)
        profile = repository.replace(
            project_id=project_id,
            connection_alias="reserved_target",
            dialect="reserved_db",
            transaction_policy="per_binding_transaction",
            bindings=[_binding("write"), _binding("cleanup")],
            reviewed_by="qa",
        )

        assert profile.dialect == "reserved_db"
        assert all(binding.dialect == "reserved_db" for binding in profile.bindings)
        reasons = TargetDataProfileRepository(connection).validate_plan(
            project_id=project_id,
            plan={
                "generation_flows": [
                    {
                        "flow_id": "reserved-flow",
                        "steps": [
                            {
                                "step_id": "write",
                                "channel": "sql",
                                "target": "upsert_expense",
                                "data_effect": "creates",
                            }
                        ],
                        "cleanup_steps": [
                            {
                                "step_id": "cleanup",
                                "channel": "sql",
                                "target": "cleanup_expense",
                            }
                        ],
                    }
                ]
            },
        )
        assert reasons == [
            "Target database dialect has no registered Adapter: reserved_db"
        ]
        connection.rollback()
