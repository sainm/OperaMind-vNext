"""Reserve, execute, and persist one approved TestDataPlan run."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from psycopg import Connection

from operamind.application.test_data_execution import (
    TestDataChannelExecutor,
    TestDataExecutionEngine,
    TestDataExecutionProgress,
    TestDataExecutionRequest,
)
from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres.test_data_execution_repository import (
    TestDataExecutionRecord,
    TestDataExecutionRecoveryWrite,
    TestDataExecutionRepository,
    TestDataExecutionRunWrite,
)


@dataclass(frozen=True, slots=True)
class TestDataExecutionServiceRequest:
    execution_result_id: str
    run_id: str
    orchestration_id: str
    test_data_plan_id: str
    approval_grant_id: str
    project_id: str
    actor: str
    base_url: str | None = None
    started_at: datetime | None = None
    replay_of_run_id: str | None = None

    def __post_init__(self) -> None:
        values = (
            self.execution_result_id,
            self.run_id,
            self.orchestration_id,
            self.test_data_plan_id,
            self.approval_grant_id,
            self.project_id,
            self.actor,
        )
        if any(not value.strip() for value in values):
            raise ValueError("Test data execution service fields must not be blank")
        if self.started_at is not None and self.started_at.utcoffset() is None:
            raise ValueError("Test data execution service started_at must include a timezone")
        if self.replay_of_run_id is not None and not self.replay_of_run_id.strip():
            raise ValueError("Test data replay Run ID must not be blank")


@dataclass(frozen=True, slots=True)
class TestDataExecutionServiceResult:
    created: bool
    artifact: dict[str, Any]
    record: TestDataExecutionRecord


@dataclass(frozen=True, slots=True)
class TestDataExecutionRecoveryRequest:
    recovery_id: str
    run_id: str
    project_id: str
    actor: str
    reason: str
    stale_before: datetime

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (
                self.recovery_id,
                self.run_id,
                self.project_id,
                self.actor,
                self.reason,
            )
        ):
            raise ValueError("Test data recovery fields must not be blank")
        if self.stale_before.utcoffset() is None:
            raise ValueError("Test data recovery stale_before must include a timezone")


class TestDataExecutionService:
    """Execute only the canonical plan bound to a live Approval Grant."""

    def __init__(
        self,
        *,
        connection: Connection[Any],
        contracts: ContractCatalog,
        executors: Mapping[str, TestDataChannelExecutor],
        clock: Callable[[], datetime] | None = None,
        progress_sink: Callable[[TestDataExecutionProgress], None] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._repository = TestDataExecutionRepository(connection, contracts)
        self._engine = TestDataExecutionEngine(
            contracts=contracts,
            executors=executors,
            clock=self._clock,
            progress_sink=progress_sink,
        )

    def execute(
        self, request: TestDataExecutionServiceRequest
    ) -> TestDataExecutionServiceResult:
        started_at = request.started_at or self._clock()
        reservation = self._repository.reserve(
            TestDataExecutionRunWrite(
                run_id=request.run_id,
                execution_result_id=request.execution_result_id,
                orchestration_id=request.orchestration_id,
                test_data_plan_id=request.test_data_plan_id,
                approval_grant_id=request.approval_grant_id,
                project_id=request.project_id,
                created_by=request.actor,
                started_at=started_at,
                replay_of_run_id=request.replay_of_run_id,
            )
        )
        if not reservation.created:
            if reservation.record.status == "running":
                raise ValueError(
                    "Test data execution Run is already running; recover it before replay"
                )
            existing = self._repository.get_result(request.run_id)
            if existing is None:
                raise RuntimeError("Completed Test data Run has no result Artifact")
            return TestDataExecutionServiceResult(
                created=False,
                artifact=existing,
                record=reservation.record,
            )
        return self._execute_record(request, reservation.record)

    def execute_reserved(
        self, request: TestDataExecutionServiceRequest
    ) -> TestDataExecutionServiceResult:
        record = self._repository.get_record(request.run_id)
        if record is None:
            raise ValueError("Reserved Test data Run does not exist")
        expected = (
            request.execution_result_id,
            request.orchestration_id,
            request.test_data_plan_id,
            request.approval_grant_id,
            request.project_id,
        )
        actual = (
            record.execution_result_id,
            record.orchestration_id,
            record.test_data_plan_id,
            record.approval_grant_id,
            record.project_id,
        )
        if actual != expected:
            raise ValueError("Reserved Test data Run scope differs")
        if record.status != "running":
            existing = self._repository.get_result(record.run_id)
            if existing is None:
                raise RuntimeError("Completed Test data Run has no result Artifact")
            return TestDataExecutionServiceResult(False, existing, record)
        return self._execute_record(request, record)

    def _execute_record(
        self,
        request: TestDataExecutionServiceRequest,
        record: TestDataExecutionRecord,
    ) -> TestDataExecutionServiceResult:
        plan = self._repository.load_plan(
            orchestration_id=request.orchestration_id,
            project_id=request.project_id,
        )
        if plan["test_data_plan_id"] != request.test_data_plan_id:
            raise RuntimeError("Reserved TestDataPlan changed before execution")
        artifact = self._engine.execute(
            plan=plan,
            request=TestDataExecutionRequest(
                execution_result_id=request.execution_result_id,
                run_id=request.run_id,
                project_id=request.project_id,
                base_url=request.base_url,
                started_at=record.started_at,
            ),
        )
        record = self._repository.complete(artifact)
        return TestDataExecutionServiceResult(
            created=True,
            artifact=artifact,
            record=record,
        )

    def recover(
        self, request: TestDataExecutionRecoveryRequest
    ) -> TestDataExecutionServiceResult:
        record = self._repository.get_record(request.run_id)
        if record is None or record.project_id != request.project_id:
            raise ValueError("Test data recovery Run does not exist")
        plan = self._repository.load_plan(
            orchestration_id=record.orchestration_id,
            project_id=record.project_id,
        )
        artifact = self._engine.interrupted_result(
            plan=plan,
            request=TestDataExecutionRequest(
                execution_result_id=record.execution_result_id,
                run_id=record.run_id,
                project_id=record.project_id,
                started_at=record.started_at,
            ),
            reason=f"Execution was interrupted and recovered: {request.reason}",
        )
        completed = self._repository.recover(
            artifact=artifact,
            recovery=TestDataExecutionRecoveryWrite(
                recovery_id=request.recovery_id,
                run_id=request.run_id,
                project_id=request.project_id,
                actor=request.actor,
                reason=request.reason,
                stale_before=request.stale_before,
            ),
        )
        return TestDataExecutionServiceResult(True, artifact, completed)
