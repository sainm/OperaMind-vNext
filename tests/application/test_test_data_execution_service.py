from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest

from operamind.application import test_data_execution_service as service_module
from operamind.application.test_data_execution import (
    TestDataExecutionEvidence as DataExecutionEvidence,
)
from operamind.application.test_data_execution import (
    TestDataExecutionRequest as DataExecutionRequest,
)
from operamind.application.test_data_execution import (
    TestDataStepExecution as DataStepExecution,
)
from operamind.application.test_data_execution_service import (
    TestDataExecutionRecoveryRequest as DataExecutionRecoveryRequest,
)
from operamind.application.test_data_execution_service import (
    TestDataExecutionService as DataExecutionService,
)
from operamind.application.test_data_execution_service import (
    TestDataExecutionServiceRequest as DataExecutionServiceRequest,
)
from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres.test_data_execution_repository import (
    TestDataExecutionRecord as DataExecutionRecord,
)
from operamind.infrastructure.postgres.test_data_execution_repository import (
    TestDataExecutionRecoveryWrite as DataExecutionRecoveryWrite,
)
from operamind.infrastructure.postgres.test_data_execution_repository import (
    TestDataExecutionReservation as DataExecutionReservation,
)
from operamind.infrastructure.postgres.test_data_execution_repository import (
    TestDataExecutionRunWrite as DataExecutionRunWrite,
)

ROOT = Path(__file__).parents[2]
STARTED = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
COMPLETED = datetime(2026, 7, 18, 12, 1, tzinfo=UTC)


class FakeRepository:
    instances: ClassVar[list[FakeRepository]] = []
    reservation: ClassVar[DataExecutionReservation | None] = None
    record: ClassVar[DataExecutionRecord | None] = None
    result: ClassVar[dict[str, Any] | None] = None

    def __init__(self, connection: object, contracts: ContractCatalog) -> None:
        del connection, contracts
        self.reserved: DataExecutionRunWrite | None = None
        self.completed: dict[str, Any] | None = None
        self.completed_owner: str | None = None
        self.recovered: tuple[dict[str, Any], DataExecutionRecoveryWrite] | None = None
        self.__class__.instances.append(self)

    def reserve(self, write: DataExecutionRunWrite) -> DataExecutionReservation:
        self.reserved = write
        return self.__class__.reservation or DataExecutionReservation(
            True, _record("running", None, created=False)
        )

    def load_plan(self, *, orchestration_id: str, project_id: str) -> dict[str, Any]:
        assert (orchestration_id, project_id) == ("orchestration-001", "visiondemo")
        return _plan()

    def complete(
        self, artifact: dict[str, Any], *, execution_owner: str | None = None
    ) -> DataExecutionRecord:
        self.completed = artifact
        self.completed_owner = execution_owner
        return _record("passed", COMPLETED, created=True)

    def get_result(self, run_id: str) -> dict[str, Any] | None:
        del run_id
        return self.__class__.result if self.__class__.result is not None else self.completed

    def get_record(self, run_id: str) -> DataExecutionRecord | None:
        assert run_id == "run-001"
        return self.__class__.record or _record("running", None, created=False)

    def recover(
        self,
        *,
        artifact: dict[str, Any],
        recovery: DataExecutionRecoveryWrite,
    ) -> DataExecutionRecord:
        self.recovered = (artifact, recovery)
        return _record("interrupted", COMPLETED, created=True)


class FixtureExecutor:
    def execute(
        self,
        *,
        request: DataExecutionRequest,
        flow_id: str,
        step: Mapping[str, object],
        resolved_inputs: Mapping[str, object],
        variables: Mapping[str, object],
        phase: str,
    ) -> DataStepExecution:
        del resolved_inputs, variables
        return DataStepExecution(
            source_values={"fixture": {"loaded": True}},
            evidence=(
                DataExecutionEvidence(
                    evidence_id="fixture-evidence-001",
                    flow_id=flow_id,
                    step_id=str(step["step_id"]),
                    phase=phase,
                    evidence_type="fixture",
                    evidence_ref="evidence://visiondemo/run-001/fixture-evidence-001",
                    content_digest="a" * 64,
                ),
            ),
        )


@pytest.fixture(autouse=True)
def reset_fake_repository() -> None:
    FakeRepository.instances.clear()
    FakeRepository.reservation = None
    FakeRepository.record = None
    FakeRepository.result = None


@pytest.mark.parametrize(
    "values",
    [
        {"actor": " "},
        {"started_at": datetime(2026, 7, 18, 12, 0)},
        {"replay_of_run_id": " "},
        {"execution_owner": " "},
    ],
)
def test_execution_service_request_rejects_invalid_values(values: dict[str, object]) -> None:
    request: dict[str, object] = {
        "execution_result_id": "result-001",
        "run_id": "run-001",
        "orchestration_id": "orchestration-001",
        "test_data_plan_id": "test-data-plan-001",
        "approval_grant_id": "grant-001",
        "project_id": "visiondemo",
        "actor": "tester@example.invalid",
    }
    request.update(values)

    with pytest.raises(ValueError):
        DataExecutionServiceRequest(**request)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "values",
    [
        {"reason": ""},
        {"stale_before": datetime(2026, 7, 18, 12, 0)},
    ],
)
def test_recovery_request_rejects_invalid_values(values: dict[str, object]) -> None:
    request: dict[str, object] = {
        "recovery_id": "recovery-001",
        "run_id": "run-001",
        "project_id": "visiondemo",
        "actor": "operator@example.invalid",
        "reason": "worker stopped",
        "stale_before": COMPLETED,
    }
    request.update(values)

    with pytest.raises(ValueError):
        DataExecutionRecoveryRequest(**request)  # type: ignore[arg-type]


def test_service_reserves_executes_and_persists_canonical_plan(monkeypatch: Any) -> None:
    monkeypatch.setattr(service_module, "TestDataExecutionRepository", FakeRepository)
    service = DataExecutionService(
        connection=object(),  # type: ignore[arg-type]
        contracts=ContractCatalog.load(ROOT / "contracts"),
        executors={"fixture": FixtureExecutor()},
        clock=lambda: COMPLETED,
    )

    result = service.execute(_request())

    repository = FakeRepository.instances[-1]
    assert repository.reserved is not None
    assert repository.reserved.started_at == STARTED
    assert repository.completed == result.artifact
    assert result.created
    assert result.artifact["status"] == "passed"
    assert result.artifact["started_at"] == "2026-07-18T12:00:00Z"
    assert result.artifact["completed_at"] == "2026-07-18T12:01:00Z"


def test_service_recovers_stale_run_with_fail_closed_artifact(monkeypatch: Any) -> None:
    monkeypatch.setattr(service_module, "TestDataExecutionRepository", FakeRepository)
    service = DataExecutionService(
        connection=object(),  # type: ignore[arg-type]
        contracts=ContractCatalog.load(ROOT / "contracts"),
        executors={},
        clock=lambda: COMPLETED,
    )

    result = service.recover(
        DataExecutionRecoveryRequest(
            recovery_id="recovery-001",
            run_id="run-001",
            project_id="visiondemo",
            actor="operator@example.invalid",
            reason="worker heartbeat stopped",
            stale_before=COMPLETED,
        )
    )

    repository = FakeRepository.instances[-1]
    assert result.artifact["status"] == "interrupted"
    assert result.artifact["cleanup_status"] == "interrupted"
    assert repository.recovered is not None
    assert repository.recovered[1].reason == "worker heartbeat stopped"


def test_service_publishes_outer_worker_failure_as_canonical_result(monkeypatch: Any) -> None:
    monkeypatch.setattr(service_module, "TestDataExecutionRepository", FakeRepository)
    service = DataExecutionService(
        connection=object(),  # type: ignore[arg-type]
        contracts=ContractCatalog.load(ROOT / "contracts"),
        executors={},
        clock=lambda: COMPLETED,
    )

    result = service.fail_reserved(
        _request(), reason="Background TestDataPlan worker failed before completion (TimeoutError)"
    )

    repository = FakeRepository.instances[-1]
    assert result.artifact["status"] == "failed"
    assert result.artifact["cleanup_status"] == "failed"
    assert result.artifact["failure_reasons"]
    assert repository.completed == result.artifact


def test_service_rejects_duplicate_running_reservation(monkeypatch: Any) -> None:
    FakeRepository.reservation = DataExecutionReservation(
        False, _record("running", None, created=False)
    )
    service = _service(monkeypatch)

    with pytest.raises(ValueError, match="already running"):
        service.execute(_request())


def test_service_returns_existing_completed_reservation(monkeypatch: Any) -> None:
    completed_record = _record("passed", COMPLETED, created=False)
    FakeRepository.reservation = DataExecutionReservation(False, completed_record)
    FakeRepository.result = {"artifact_type": "TestDataExecutionResult", "status": "passed"}
    service = _service(monkeypatch)

    result = service.execute(_request())

    assert result.created is False
    assert result.record == completed_record
    assert result.artifact["status"] == "passed"


def test_service_fails_closed_when_completed_reservation_has_no_artifact(
    monkeypatch: Any,
) -> None:
    FakeRepository.reservation = DataExecutionReservation(
        False, _record("passed", COMPLETED, created=False)
    )
    service = _service(monkeypatch)

    with pytest.raises(RuntimeError, match="no result Artifact"):
        service.execute(_request())


def test_execute_reserved_validates_existence_and_scope(monkeypatch: Any) -> None:
    service = _service(monkeypatch)
    FakeRepository.record = None
    repository = FakeRepository.instances[-1]
    repository.get_record = lambda run_id: None  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="does not exist"):
        service.execute_reserved(_request())

    repository.get_record = lambda run_id: _record(  # type: ignore[method-assign]
        "running", None, created=False, project_id="other-project"
    )
    with pytest.raises(ValueError, match="scope differs"):
        service.execute_reserved(_request())


def test_execute_reserved_returns_the_existing_terminal_result(monkeypatch: Any) -> None:
    completed = _record("passed", COMPLETED, created=False)
    FakeRepository.record = completed
    FakeRepository.result = {"artifact_type": "TestDataExecutionResult", "status": "passed"}
    service = _service(monkeypatch)

    result = service.execute_reserved(_request())

    assert result.created is False
    assert result.record is completed
    assert result.artifact == FakeRepository.result


def test_execute_reserved_fails_closed_when_terminal_result_is_missing(
    monkeypatch: Any,
) -> None:
    FakeRepository.record = _record("failed", COMPLETED, created=False)
    service = _service(monkeypatch)

    with pytest.raises(RuntimeError, match="no result Artifact"):
        service.execute_reserved(_request())


def test_execute_reserved_binds_completion_to_the_claim_owner(monkeypatch: Any) -> None:
    service = _service(monkeypatch)

    result = service.execute_reserved(
        replace(_request(), execution_owner="main-flow:worker-1")
    )

    repository = FakeRepository.instances[-1]
    assert result.created
    assert repository.completed_owner == "main-flow:worker-1"


def _service(monkeypatch: Any) -> DataExecutionService:
    monkeypatch.setattr(service_module, "TestDataExecutionRepository", FakeRepository)
    return DataExecutionService(
        connection=object(),  # type: ignore[arg-type]
        contracts=ContractCatalog.load(ROOT / "contracts"),
        executors={"fixture": FixtureExecutor()},
        clock=lambda: COMPLETED,
    )


def _request() -> DataExecutionServiceRequest:
    return DataExecutionServiceRequest(
        execution_result_id="result-001",
        run_id="run-001",
        orchestration_id="orchestration-001",
        test_data_plan_id="test-data-plan-001",
        approval_grant_id="grant-001",
        project_id="visiondemo",
        actor="tester@example.invalid",
        started_at=STARTED,
    )


def _record(
    status: str,
    completed_at: datetime | None,
    *,
    created: bool,
    project_id: str = "visiondemo",
) -> DataExecutionRecord:
    return DataExecutionRecord(
        created=created,
        run_id="run-001",
        execution_result_id="result-001",
        orchestration_id="orchestration-001",
        test_data_plan_id="test-data-plan-001",
        approval_grant_id="grant-001",
        project_id=project_id,
        analysis_case_id="case-001",
        status=status,
        started_at=STARTED,
        completed_at=completed_at,
    )


def _plan() -> dict[str, Any]:
    return {
        "artifact_type": "TestDataPlan",
        "schema_version": "v1",
        "test_data_plan_id": "test-data-plan-001",
        "test_plan_id": "test-plan-001",
        "project_id": "visiondemo",
        "status": "ready",
        "data_sets": [
            {
                "test_data_id": "seed-001",
                "test_case_refs": ["case-001"],
                "setup_actions": [],
                "cleanup_policy": "isolated_environment",
            }
        ],
        "generation_flows": [
            {
                "flow_id": "flow-001",
                "title": "Load the target seed",
                "test_data_refs": ["seed-001"],
                "test_case_refs": ["case-001"],
                "steps": [
                    {
                        "step_id": "load-seed",
                        "sequence": 1,
                        "channel": "fixture",
                        "business_action": "load seed",
                        "target": "seed-v1",
                        "inputs": {},
                        "depends_on": [],
                        "output_bindings": [],
                        "postconditions": [
                            {
                                "assertion_id": "seed-loaded",
                                "observe_via": "fixture",
                                "subject": "loaded",
                                "operator": "equals",
                                "expected": True,
                            }
                        ],
                    }
                ],
                "final_assertions": [
                    {
                        "assertion_id": "test-case-001",
                        "observe_via": "test",
                        "subject": "case-001",
                        "operator": "satisfies",
                        "expected": "passed",
                    }
                ],
                "cleanup_policy": "isolated_environment",
                "cleanup_steps": [],
            }
        ],
        "blocking_reasons": [],
    }
