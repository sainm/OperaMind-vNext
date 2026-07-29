"""Background execution of internally reserved TestDataPlan Runs."""

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
from operamind.application.test_data_ui_verification import (
    TestDataUiVerificationService,
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
_TARGET_BASE_URL_ENV = "OPERAMIND_TEST_TARGET_BASE_URL"

TestDataExecutorFactory = Callable[[Path], Mapping[str, TestDataChannelExecutor]]


def default_test_data_executor_factory(
    repository_root: Path,
) -> Mapping[str, TestDataChannelExecutor]:
    """Expose only restricted adapters; deployment-specific bindings start empty."""
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
            execution = service.execute_reserved(
                TestDataExecutionServiceRequest(
                    execution_result_id=record.execution_result_id,
                    run_id=record.run_id,
                    orchestration_id=record.orchestration_id,
                    test_data_plan_id=record.test_data_plan_id,
                    approval_grant_id=record.approval_grant_id,
                    project_id=record.project_id,
                    actor="main-flow-worker",
                    base_url=_target_base_url(),
                    started_at=record.started_at,
                    replay_of_run_id=record.replay_of_run_id,
                )
            )
            TestDataUiVerificationService(connection, contracts).publish(
                orchestration_id=record.orchestration_id,
                execution_result=execution.artifact,
            )
            closure = ChangeClosureService(connection, contracts).close(
                orchestration_id=record.orchestration_id,
                actor="main-flow-worker",
            )
            repository.append_event(
                TestDataExecutionEventWrite(
                    run_id=record.run_id,
                    project_id=record.project_id,
                    event_type="closure_generated",
                    message=f"ChangeClosureResult: {closure.record.closure_result_id}",
                )
            )
    except Exception as error:
        LOGGER.exception("Main-flow TestDataPlan background Run failed: %s", run_id)
        _publish_background_failure(
            database_url=database_url,
            repository_root=repository_root,
            run_id=run_id,
            error=error,
        )


def _publish_background_failure(
    *,
    database_url: str,
    repository_root: Path,
    run_id: str,
    error: Exception,
) -> None:
    """Make an outer-worker failure visible without persisting exception details."""
    reason = f"Background TestDataPlan worker failed before completion ({type(error).__name__})"
    try:
        with psycopg.connect(database_url) as connection:
            contracts = ContractCatalog.load(repository_root / "contracts")
            repository = TestDataExecutionRepository(connection, contracts)
            record = repository.get_record(run_id)
            if record is None or record.status != "running":
                return
            service = TestDataExecutionService(
                connection=connection,
                contracts=contracts,
                executors={},
            )
            repository.append_event(
                TestDataExecutionEventWrite(
                    run_id=record.run_id,
                    project_id=record.project_id,
                    event_type="background_failed",
                    status="failed",
                    message=reason,
                )
            )
            service.fail_reserved(
                TestDataExecutionServiceRequest(
                    execution_result_id=record.execution_result_id,
                    run_id=record.run_id,
                    orchestration_id=record.orchestration_id,
                    test_data_plan_id=record.test_data_plan_id,
                    approval_grant_id=record.approval_grant_id,
                    project_id=record.project_id,
                    actor="main-flow-worker",
                    started_at=record.started_at,
                ),
                reason=reason,
            )
            closure = ChangeClosureService(connection, contracts).close(
                orchestration_id=record.orchestration_id,
                actor="main-flow-worker",
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
        LOGGER.exception("Could not publish background failure for Run: %s", run_id)


def _target_base_url() -> str | None:
    value = os.getenv(_TARGET_BASE_URL_ENV, "").strip()
    return value or None
