"""Background execution of internally reserved TestDataPlan Runs."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread, get_ident
from typing import Literal
from uuid import uuid4

import psycopg

from operamind.application.change_closure_service import ChangeClosureService
from operamind.application.test_data_execution import (
    TestDataChannelExecutor,
    TestDataExecutionProgress,
)
from operamind.application.test_data_execution_service import (
    TestDataExecutionRecoveryRequest,
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
from operamind.infrastructure.postgres.web_control_plane_repository import (
    WebControlPlaneRepository,
)
from operamind.infrastructure.test_data import (
    PlaywrightUiTestDataExecutor,
    SafeHttpTestDataExecutor,
)
from operamind.infrastructure.test_data.target_data import ProjectSqlTestDataExecutor

LOGGER = logging.getLogger(__name__)
TestDataExecutorFactory = Callable[[Path], Mapping[str, TestDataChannelExecutor]]
MainFlowExecutionOutcome = Literal["executed", "published", "recovered", "busy", "failed"]
_EXECUTION_LEASE_SECONDS = 300
_EXECUTION_HEARTBEAT_SECONDS = 60


def default_test_data_executor_factory(
    repository_root: Path,
    *,
    control_database_url: str | None = None,
) -> Mapping[str, TestDataChannelExecutor]:
    """Expose restricted adapters; SQL resolves only reviewed Project bindings."""
    store = LocalEvidenceStore(repository_root / "readiness" / "evidence" / "test-data")
    executors: dict[str, TestDataChannelExecutor] = {
        "http": SafeHttpTestDataExecutor(evidence_store=store),
        "ui": PlaywrightUiTestDataExecutor(evidence_store=store),
    }
    if control_database_url is not None:
        executors["sql"] = ProjectSqlTestDataExecutor(
            control_database_url=control_database_url,
            evidence_store=store,
        )
    return executors


def execute_reserved_test_data_run(
    *,
    database_url: str,
    repository_root: Path,
    run_id: str,
    executor_factory: TestDataExecutorFactory = default_test_data_executor_factory,
) -> MainFlowExecutionOutcome:
    """Execute one persisted reservation and evaluate Closure after completion."""
    executor_id = f"main-flow:{uuid4().hex}:{get_ident()}"
    stop_heartbeat = Event()
    lease_lost = Event()
    heartbeat_thread: Thread | None = None
    try:
        execution_artifact: dict[str, object]
        with psycopg.connect(database_url) as connection:
            contracts = ContractCatalog.load(repository_root / "contracts")
            repository = TestDataExecutionRepository(connection, contracts)
            claim = repository.claim_execution(
                run_id=run_id,
                executor_id=executor_id,
                at=datetime.now(UTC),
                lease_seconds=_EXECUTION_LEASE_SECONDS,
            )
            record = claim.record
            if claim.outcome == "busy":
                return "busy"

            def progress(value: TestDataExecutionProgress) -> None:
                if lease_lost.is_set():
                    raise RuntimeError("TestDataPlan execution lease was lost")
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

            executors: Mapping[str, TestDataChannelExecutor]
            if claim.outcome != "claimed":
                executors = {}
            elif executor_factory is default_test_data_executor_factory:
                executors = default_test_data_executor_factory(
                    repository_root,
                    control_database_url=database_url,
                )
            else:
                executors = executor_factory(repository_root)
            service = TestDataExecutionService(
                connection=connection,
                contracts=contracts,
                executors=executors,
                progress_sink=progress,
            )
            if claim.outcome in {"stale", "exhausted"}:
                stale_before = record.lease_expires_at or datetime.now(UTC)
                execution = service.recover(
                    TestDataExecutionRecoveryRequest(
                        recovery_id=_recovery_id(record.run_id, stale_before),
                        run_id=record.run_id,
                        project_id=record.project_id,
                        actor="main-flow-worker",
                        reason="Persisted execution lease expired before completion",
                        stale_before=stale_before,
                    )
                )
                outcome: MainFlowExecutionOutcome = "recovered"
            else:
                if claim.outcome == "claimed":
                    heartbeat_thread = Thread(
                        target=_heartbeat_execution_lease,
                        kwargs={
                            "database_url": database_url,
                            "repository_root": repository_root,
                            "run_id": run_id,
                            "executor_id": executor_id,
                            "stop_event": stop_heartbeat,
                            "lease_lost_event": lease_lost,
                        },
                        name=f"operamind-test-data-heartbeat-{run_id[:16]}",
                        daemon=True,
                    )
                    heartbeat_thread.start()
                execution = service.execute_reserved(
                    TestDataExecutionServiceRequest(
                        execution_result_id=record.execution_result_id,
                        run_id=record.run_id,
                        orchestration_id=record.orchestration_id,
                        test_data_plan_id=record.test_data_plan_id,
                        approval_grant_id=record.approval_grant_id,
                        project_id=record.project_id,
                        actor="main-flow-worker",
                        base_url=WebControlPlaneRepository(
                            connection, contracts
                        ).project_test_base_url(record.project_id),
                        started_at=record.started_at,
                        replay_of_run_id=record.replay_of_run_id,
                        execution_owner=(
                            executor_id if claim.outcome == "claimed" else None
                        ),
                    )
                )
                outcome = "executed" if claim.outcome == "claimed" else "published"
            execution_artifact = execution.artifact

        stop_heartbeat.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=5)

        # Commit the immutable execution result before publishing downstream UI
        # verification. A publication or Closure failure must not roll a passed
        # browser run back and replace it with a synthetic not-run result.
        with psycopg.connect(database_url) as connection:
            contracts = ContractCatalog.load(repository_root / "contracts")
            repository = TestDataExecutionRepository(connection, contracts)
            TestDataUiVerificationService(connection, contracts).publish(
                orchestration_id=record.orchestration_id,
                execution_result=execution_artifact,
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
        return outcome
    except Exception as error:
        stop_heartbeat.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=5)
        LOGGER.exception("Main-flow TestDataPlan background Run failed: %s", run_id)
        _publish_background_failure(
            database_url=database_url,
            repository_root=repository_root,
            run_id=run_id,
            executor_id=executor_id,
            error=error,
        )
        return "failed"


def _heartbeat_execution_lease(
    *,
    database_url: str,
    repository_root: Path,
    run_id: str,
    executor_id: str,
    stop_event: Event,
    lease_lost_event: Event,
) -> None:
    """Renew a live execution Claim until completion or ownership loss."""

    while not stop_event.wait(_EXECUTION_HEARTBEAT_SECONDS):
        try:
            with psycopg.connect(database_url) as connection:
                contracts = ContractCatalog.load(repository_root / "contracts")
                renewed = TestDataExecutionRepository(connection, contracts).heartbeat_execution(
                    run_id=run_id,
                    executor_id=executor_id,
                    at=datetime.now(UTC),
                    lease_seconds=_EXECUTION_LEASE_SECONDS,
                )
            if not renewed:
                LOGGER.error("TestDataPlan execution lease was lost for Run: %s", run_id)
                lease_lost_event.set()
                return
        except (OSError, RuntimeError, ValueError, psycopg.Error):
            LOGGER.exception("Could not renew TestDataPlan execution lease for Run: %s", run_id)
            lease_lost_event.set()
            return


def _recovery_id(run_id: str, stale_before: datetime) -> str:
    stamp = stale_before.astimezone(UTC).isoformat()
    digest = hashlib.sha256(f"{run_id}\0{stamp}".encode()).hexdigest()[:32]
    return f"test-data-recovery-{digest}"


def _publish_background_failure(
    *,
    database_url: str,
    repository_root: Path,
    run_id: str,
    error: Exception,
    executor_id: str | None = None,
) -> None:
    """Make an outer-worker failure visible without persisting exception details."""
    reason = f"Background TestDataPlan worker failed before completion ({type(error).__name__})"
    try:
        with psycopg.connect(database_url) as connection:
            contracts = ContractCatalog.load(repository_root / "contracts")
            repository = TestDataExecutionRepository(connection, contracts)
            record = repository.get_record(run_id)
            if record is None:
                return
            if (
                record.status == "running"
                and executor_id is not None
                and record.execution_owner != executor_id
            ):
                LOGGER.warning(
                    "Ignoring failure from a TestDataPlan executor that no longer owns Run: %s",
                    run_id,
                )
                return
            if record.status != "running":
                downstream_reason = (
                    "Downstream UI Evidence or Change Closure publication failed "
                    f"({type(error).__name__})"
                )
                repository.append_event(
                    TestDataExecutionEventWrite(
                        run_id=record.run_id,
                        project_id=record.project_id,
                        event_type="downstream_publication_failed",
                        status=record.status,
                        message=downstream_reason,
                    )
                )
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
                    execution_owner=executor_id,
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
