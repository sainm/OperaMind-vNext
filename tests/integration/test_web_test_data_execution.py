from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres import (
    MigrationCatalog,
    MigrationRunner,
)
from operamind.infrastructure.postgres import (
    TestDataExecutionRepository as DataExecutionRepository,
)
from operamind.infrastructure.postgres import (
    TestDataExecutionRunWrite as DataExecutionRunWrite,
)

ROOT = Path(__file__).parents[2]
DATABASE_URL = os.getenv("OPERAMIND_TEST_DATABASE_URL")
pytestmark = pytest.mark.integration


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_web_test_data_authorization_queries_fail_closed_for_missing_scope() -> None:
    """Exercise PostgreSQL parsing for the Web scope and reservation queries."""
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        repository = DataExecutionRepository(
            connection, ContractCatalog.load(ROOT / "contracts")
        )

        with pytest.raises(ValueError, match="No active Approval Grant"):
            repository.latest_active_scope(
                orchestration_id="missing-orchestration",
                project_id="missing-project",
                at=datetime.now(UTC),
            )
        assert (
            repository.base_url_for_orchestration(
                orchestration_id="missing-orchestration",
                project_id="missing-project",
            )
            is None
        )
        with pytest.raises(ValueError, match="scope does not exist"):
            repository.reserve(
                DataExecutionRunWrite(
                    run_id="missing-run",
                    execution_result_id="missing-result",
                    orchestration_id="missing-orchestration",
                    test_data_plan_id="missing-plan",
                    approval_grant_id="missing-grant",
                    project_id="missing-project",
                    created_by="integration-test",
                    started_at=datetime.now(UTC),
                )
            )
        connection.rollback()
