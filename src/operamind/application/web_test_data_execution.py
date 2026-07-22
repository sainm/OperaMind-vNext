"""Background execution of Web-reserved TestDataPlan Runs."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from pathlib import Path

import psycopg

from operamind.application.change_closure_service import ChangeClosureService
from operamind.application.test_data_execution import (
    TestDataChannelExecutor,
    TestDataExecutionProgress,
)
from operamind.application.test_data_execution_service import (
    TestDataExecutionService,
    TestDataExecutionServiceRequest,
)
from operamind.contracts import ContractCatalog
from operamind.infrastructure.browser import LocalEvidenceStore
from operamind.infrastructure.postgres.test_data_execution_repository import (
    TestDataExecutionEventWrite,
    TestDataExecutionRepository,
)
from operamind.infrastructure.test_data import (
    BoundFixtureTestDataExecutor,
    BoundSqlTestDataExecutor,
    BoundUiTestDataExecutor,
    SafeHttpTestDataExecutor,
)

LOGGER = logging.getLogger(__name__)

TestDataExecutorFactory = Callable[[Path], Mapping[str, TestDataChannelExecutor]]


def default_test_data_executor_factory(
    repository_root: Path,
) -> Mapping[str, TestDataChannelExecutor]:
    """Expose only restricted adapters; deployment-specific bindings start empty."""
    profile = os.getenv("OPERAMIND_TEST_DATA_BINDING_PROFILE")
    if profile:
        from operamind.infrastructure.test_data.visiondemo import (
            configured_visiondemo_profile,
            visiondemo_test_data_executor_factory,
        )

        configured_visiondemo_profile()
        return visiondemo_test_data_executor_factory(repository_root)
    store = LocalEvidenceStore(repository_root / "readiness" / "evidence" / "test-data")
    return {
        "http": SafeHttpTestDataExecutor(evidence_store=store),
        "fixture": BoundFixtureTestDataExecutor(evidence_store=store, bindings={}),
        "sql": BoundSqlTestDataExecutor(evidence_store=store, bindings={}),
        "ui": BoundUiTestDataExecutor(evidence_store=store, bindings={}),
    }


def execute_reserved_test_data_run(
    *,
    database_url: str,
    repository_root: Path,
    run_id: str,
    executor_factory: TestDataExecutorFactory = default_test_data_executor_factory,
) -> None:
    """Execute one persisted reservation and evaluate Closure after completion."""
    try:
        with psycopg.connect(database_url) as connection:
            contracts = ContractCatalog.load(repository_root / "contracts")
            repository = TestDataExecutionRepository(connection, contracts)
            record = repository.get_record(run_id)
            if record is None:
                raise ValueError("Reserved Test data Run does not exist")

            def progress(value: TestDataExecutionProgress) -> None:
                repository.append_event(
                    TestDataExecutionEventWrite(
                        run_id=record.run_id,
                        project_id=record.project_id,
                        event_type=value.event_type,
                        flow_id=value.flow_id,
                        phase=value.phase,
                        step_id=value.step_id,
                        status=value.status,
                        message=value.message,
                    )
                )

            service = TestDataExecutionService(
                connection=connection,
                contracts=contracts,
                executors=executor_factory(repository_root),
                progress_sink=progress,
            )
            service.execute_reserved(
                TestDataExecutionServiceRequest(
                    execution_result_id=record.execution_result_id,
                    run_id=record.run_id,
                    orchestration_id=record.orchestration_id,
                    test_data_plan_id=record.test_data_plan_id,
                    approval_grant_id=record.approval_grant_id,
                    project_id=record.project_id,
                    actor="web-test-data-worker",
                    base_url=repository.base_url_for_orchestration(
                        orchestration_id=record.orchestration_id,
                        project_id=record.project_id,
                    ),
                    started_at=record.started_at,
                    replay_of_run_id=record.replay_of_run_id,
                )
            )
            closure = ChangeClosureService(connection, contracts).close(
                orchestration_id=record.orchestration_id,
                actor="web-test-data-worker",
            )
            repository.append_event(
                TestDataExecutionEventWrite(
                    run_id=record.run_id,
                    project_id=record.project_id,
                    event_type="closure_generated",
                    message=f"ChangeClosureResult: {closure.record.closure_result_id}",
                )
            )
    except Exception:
        LOGGER.exception("Web TestDataPlan background Run failed: %s", run_id)
