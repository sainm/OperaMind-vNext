"""Approval-bound TestDataPlan run and evidence persistence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from psycopg import Connection, Cursor

from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres.artifact_repository import ArtifactRepository
from operamind.infrastructure.postgres.errors import PersistenceConflictError
from operamind.infrastructure.postgres.test_case_execution_authorization_repository import (
    TestCaseExecutionAuthorizationRepository,
)


@dataclass(frozen=True, slots=True)
class TestDataExecutionRunWrite:
    run_id: str
    execution_result_id: str
    orchestration_id: str
    test_data_plan_id: str
    approval_grant_id: str
    project_id: str
    created_by: str
    started_at: datetime
    replay_of_run_id: str | None = None

    def __post_init__(self) -> None:
        values = (
            self.run_id,
            self.execution_result_id,
            self.orchestration_id,
            self.test_data_plan_id,
            self.approval_grant_id,
            self.project_id,
            self.created_by,
        )
        if any(not value.strip() for value in values):
            raise ValueError("Test data execution reservation fields must not be blank")
        if self.started_at.utcoffset() is None:
            raise ValueError("Test data execution started_at must include a timezone")
        if self.replay_of_run_id is not None and not self.replay_of_run_id.strip():
            raise ValueError("Test data replay Run ID must not be blank")
        if self.replay_of_run_id == self.run_id:
            raise ValueError("Test data Run cannot replay itself")


@dataclass(frozen=True, slots=True)
class TestDataExecutionRecord:
    created: bool
    run_id: str
    execution_result_id: str
    orchestration_id: str
    test_data_plan_id: str
    approval_grant_id: str
    project_id: str
    analysis_case_id: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    replay_of_run_id: str | None = None


@dataclass(frozen=True, slots=True)
class TestDataExecutionEventWrite:
    run_id: str
    project_id: str
    event_type: str
    flow_id: str | None = None
    phase: str | None = None
    step_id: str | None = None
    status: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class TestDataExecutionRecoveryWrite:
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


@dataclass(frozen=True, slots=True)
class TestDataExecutionReservation:
    created: bool
    record: TestDataExecutionRecord


class TestDataExecutionRepository:
    """Reserve one approved run and publish one immutable normalized result."""

    def __init__(self, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._connection = connection
        self._contracts = contracts
        self._artifacts = ArtifactRepository(connection, contracts)
        self._case_execution_authorizations = TestCaseExecutionAuthorizationRepository(
            connection, contracts
        )

    def reserve(self, write: TestDataExecutionRunWrite) -> TestDataExecutionReservation:
        started_at = write.started_at.astimezone(UTC)
        identity = (
            write.execution_result_id,
            write.orchestration_id,
            write.test_data_plan_id,
            write.approval_grant_id,
            write.project_id,
            write.created_by,
            started_at,
            write.replay_of_run_id,
        )
        with self._connection.transaction(), self._connection.cursor() as cursor:
            existing = self._load(cursor, write.run_id, for_update=True)
            if existing is not None:
                actual = (
                    existing.execution_result_id,
                    existing.orchestration_id,
                    existing.test_data_plan_id,
                    existing.approval_grant_id,
                    existing.project_id,
                    self._created_by(cursor, write.run_id),
                    existing.started_at,
                    existing.replay_of_run_id,
                )
                if actual != identity:
                    raise PersistenceConflictError(
                        f"Test data execution identity has different content: {write.run_id}"
                    )
                return TestDataExecutionReservation(False, existing)
            cursor.execute(
                """
                SELECT orchestration.analysis_case_id, orchestration.status,
                       grant_record.analysis_case_id, grant_record.expires_at,
                       grant_record.allowed_actions,
                       NOT EXISTS (
                           SELECT 1 FROM approval_grant_events AS event
                           WHERE event.approval_grant_id = grant_record.approval_grant_id
                             AND event.project_id = grant_record.project_id
                             AND event.event_type = 'revoked'
                       ) AS grant_not_revoked,
                       case_record.status,
                       orchestration.test_plan_id
                FROM change_orchestrations AS orchestration
                JOIN approval_grants AS grant_record
                  ON grant_record.approval_grant_id = %s
                 AND grant_record.project_id = orchestration.project_id
                JOIN analysis_cases AS case_record
                  ON case_record.analysis_case_id = orchestration.analysis_case_id
                 AND case_record.project_id = orchestration.project_id
                WHERE orchestration.orchestration_id = %s
                  AND orchestration.test_data_plan_id = %s
                  AND orchestration.project_id = %s
                FOR UPDATE OF orchestration, grant_record, case_record
                """,
                (
                    write.approval_grant_id,
                    write.orchestration_id,
                    write.test_data_plan_id,
                    write.project_id,
                ),
            )
            scope = cursor.fetchone()
            if scope is None:
                raise ValueError("Test data execution scope does not exist")
            cursor.execute(
                """
                SELECT reason FROM profile_drift_impacts
                WHERE project_id = %s
                  AND artifact_type = 'TestPlan'
                  AND artifact_id = %s
                  AND resolved_at IS NULL
                ORDER BY profile_drift_event_id
                LIMIT 1
                """,
                (write.project_id, str(scope[7])),
            )
            drift = cursor.fetchone()
            if drift is not None:
                raise ValueError(f"TestDataPlan is blocked by Profile drift: {drift[0]}")
            analysis_case_id = str(scope[0])
            actions = _strings(scope[4])
            if str(scope[1]) != "ready":
                raise ValueError("Test data execution requires a ready Orchestration")
            if str(scope[2]) != analysis_case_id:
                raise ValueError("Approval Grant does not match Orchestration Case")
            if cast(datetime, scope[3]) <= started_at:
                raise ValueError("Approval Grant expired before TestDataPlan execution")
            if not bool(scope[5]):
                raise ValueError("Approval Grant is revoked")
            if str(scope[6]) not in {"editing", "verifying_ui"}:
                raise ValueError("Analysis Case does not permit test data execution")
            required_actions = {"run_test", "record_evidence"}
            if not required_actions.issubset(actions):
                raise ValueError("Approval Grant must allow run_test and record_evidence")
            self._case_execution_authorizations.authorize_for_run(
                target_orchestration_id=write.orchestration_id,
                approval_grant_id=write.approval_grant_id,
                actor=write.created_by,
                at=started_at,
            )
            if write.replay_of_run_id is not None:
                replay = self._load(cursor, write.replay_of_run_id, for_update=True)
                if replay is None or (
                    replay.project_id,
                    replay.orchestration_id,
                    replay.test_data_plan_id,
                ) != (
                    write.project_id,
                    write.orchestration_id,
                    write.test_data_plan_id,
                ):
                    raise ValueError("Replay source Run does not match TestDataPlan scope")
                if replay.status == "running":
                    raise ValueError("A running Test data Run cannot be replayed")
            cursor.execute(
                """
                INSERT INTO test_data_execution_runs (
                    run_id, execution_result_id, orchestration_id,
                    test_data_plan_id, approval_grant_id, project_id,
                    analysis_case_id, status, created_by, started_at,
                    replay_of_run_id
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, 'running', %s, %s, %s
                )
                """,
                (
                    write.run_id,
                    write.execution_result_id,
                    write.orchestration_id,
                    write.test_data_plan_id,
                    write.approval_grant_id,
                    write.project_id,
                    analysis_case_id,
                    write.created_by,
                    started_at,
                    write.replay_of_run_id,
                ),
            )
            record = self._load(cursor, write.run_id, for_update=False)
        if record is None:
            raise RuntimeError("Test data execution reservation was not persisted")
        return TestDataExecutionReservation(True, record)

    def load_plan(self, *, orchestration_id: str, project_id: str) -> dict[str, Any]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT test_data_plan_id, status
                FROM change_orchestrations
                WHERE orchestration_id = %s AND project_id = %s
                """,
                (orchestration_id, project_id),
            )
            row = cursor.fetchone()
        if row is None or str(row[1]) != "ready":
            raise ValueError("Ready Test data Orchestration does not exist")
        artifact = self._artifacts.get(str(row[0]))
        if artifact is None or artifact.get("artifact_type") != "TestDataPlan":
            raise PersistenceConflictError("Orchestration TestDataPlan Artifact is missing")
        if artifact.get("project_id") != project_id:
            raise PersistenceConflictError("Orchestration TestDataPlan scope differs")
        return artifact

    def complete(self, artifact: dict[str, Any]) -> TestDataExecutionRecord:
        self._contracts.validate_artifact(artifact)
        if artifact.get("artifact_type") != "TestDataExecutionResult":
            raise ValueError("Test data completion requires TestDataExecutionResult")
        run_id = str(artifact["run_id"])
        project_id = str(artifact["project_id"])
        completed_at = _timestamp(str(artifact["completed_at"]))
        _validate_evidence_bindings(artifact)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            current = self._load(cursor, run_id, for_update=True)
            if current is None or current.project_id != project_id:
                raise ValueError("Test data execution reservation does not exist")
            expected_scope = (
                artifact["execution_result_id"],
                artifact["test_data_plan_id"],
                artifact["project_id"],
            )
            actual_scope = (
                current.execution_result_id,
                current.test_data_plan_id,
                current.project_id,
            )
            if actual_scope != expected_scope:
                raise ValueError("TestDataExecutionResult does not match reserved scope")
            if _timestamp(str(artifact["started_at"])) != current.started_at:
                raise ValueError("TestDataExecutionResult started_at differs from reservation")
            self._artifacts.store(
                artifact_id=current.execution_result_id,
                project_id=current.project_id,
                analysis_case_id=current.analysis_case_id,
                artifact=artifact,
            )
            if current.status != "running":
                if not self._normalized_matches(cursor, run_id, artifact):
                    raise PersistenceConflictError(
                        f"Test data execution normalized content differs: {run_id}"
                    )
                return current
            for flow_order, flow in enumerate(
                cast(list[dict[str, Any]], artifact["flow_results"]), start=1
            ):
                cursor.execute(
                    """
                    INSERT INTO test_data_flow_results (
                        run_id, project_id, flow_id, execution_order,
                        status, deferred_assertion_ids
                    ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        run_id,
                        project_id,
                        flow["flow_id"],
                        flow_order,
                        flow["status"],
                        _json(flow["deferred_assertion_ids"]),
                    ),
                )
                for step in _flow_steps(flow):
                    cursor.execute(
                        """
                        INSERT INTO test_data_step_results (
                            run_id, project_id, flow_id, phase, step_id,
                            sequence, channel, status, output_variables,
                            evidence_refs, failure_reason
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s,
                            %s::jsonb, %s::jsonb, %s
                        )
                        """,
                        (
                            run_id,
                            project_id,
                            flow["flow_id"],
                            step["phase"],
                            step["step_id"],
                            step["sequence"],
                            step["channel"],
                            step["status"],
                            _json(step["output_variables"]),
                            _json(step["evidence_refs"]),
                            step.get("failure_reason"),
                        ),
                    )
            for evidence in cast(list[dict[str, Any]], artifact["evidence"]):
                cursor.execute(
                    """
                    INSERT INTO test_data_execution_evidence (
                        evidence_id, run_id, project_id, flow_id, phase,
                        step_id, evidence_type, evidence_ref,
                        content_digest, sanitized
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        evidence["evidence_id"],
                        run_id,
                        project_id,
                        evidence["flow_id"],
                        evidence["phase"],
                        evidence["step_id"],
                        evidence["evidence_type"],
                        evidence["evidence_ref"],
                        evidence["content_digest"],
                        evidence["sanitized"],
                    ),
                )
            cursor.execute(
                """
                UPDATE test_data_execution_runs
                SET status = %s, result_artifact_id = execution_result_id,
                    completed_at = %s
                WHERE run_id = %s AND status = 'running'
                """,
                (artifact["status"], completed_at, run_id),
            )
            if cursor.rowcount != 1:
                raise PersistenceConflictError("Test data execution completion lost its lock")
            record = self._load(cursor, run_id, for_update=False)
        if record is None:
            raise RuntimeError("Test data execution completion was not persisted")
        return TestDataExecutionRecord(
            created=True,
            run_id=record.run_id,
            execution_result_id=record.execution_result_id,
            orchestration_id=record.orchestration_id,
            test_data_plan_id=record.test_data_plan_id,
            approval_grant_id=record.approval_grant_id,
            project_id=record.project_id,
            analysis_case_id=record.analysis_case_id,
            status=record.status,
            started_at=record.started_at,
            completed_at=record.completed_at,
            replay_of_run_id=record.replay_of_run_id,
        )

    def get_result(self, run_id: str) -> dict[str, Any] | None:
        with self._connection.cursor() as cursor:
            record = self._load(cursor, run_id, for_update=False)
        if record is None or record.status == "running":
            return None
        artifact = self._artifacts.get(record.execution_result_id)
        if artifact is None or artifact.get("artifact_type") != "TestDataExecutionResult":
            raise PersistenceConflictError("Test data result Artifact is missing")
        with self._connection.cursor() as cursor:
            if not self._normalized_matches(cursor, run_id, artifact):
                raise PersistenceConflictError(
                    f"Test data execution normalized content differs: {run_id}"
                )
        return artifact

    def get_record(self, run_id: str) -> TestDataExecutionRecord | None:
        with self._connection.cursor() as cursor:
            return self._load(cursor, run_id, for_update=False)

    def append_event(self, write: TestDataExecutionEventWrite) -> dict[str, Any]:
        if any(not value.strip() for value in (write.run_id, write.project_id, write.event_type)):
            raise ValueError("Test data progress identity must not be blank")
        if write.phase is not None and write.phase not in {"setup", "cleanup"}:
            raise ValueError("Test data progress phase is invalid")
        if write.step_id is not None and (write.flow_id is None or write.phase is None):
            raise ValueError("Test data step progress requires flow and phase")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            record = self._load(cursor, write.run_id, for_update=True)
            if record is None or record.project_id != write.project_id:
                raise ValueError("Test data progress Run does not exist")
            if record.status != "running" and write.event_type not in {
                "recovered",
                "closure_generated",
            }:
                raise ValueError("Completed Test data Run does not accept progress events")
            cursor.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1
                FROM test_data_execution_events
                WHERE run_id = %s
                """,
                (write.run_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("Test data progress sequence was not calculated")
            sequence = int(row[0])
            event_id = _event_id(write.run_id, sequence, write.event_type)
            cursor.execute(
                """
                INSERT INTO test_data_execution_events (
                    event_id, run_id, project_id, sequence, event_type,
                    flow_id, phase, step_id, status, message
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    event_id,
                    write.run_id,
                    write.project_id,
                    sequence,
                    write.event_type,
                    write.flow_id,
                    write.phase,
                    write.step_id,
                    write.status,
                    write.message,
                ),
            )
            cursor.execute(
                """
                SELECT created_at
                FROM test_data_execution_events
                WHERE event_id = %s
                """,
                (event_id,),
            )
            created = cursor.fetchone()
        if created is None:
            raise RuntimeError("Test data progress event was not persisted")
        return {
            "event_id": event_id,
            "sequence": sequence,
            "event_type": write.event_type,
            "flow_id": write.flow_id,
            "phase": write.phase,
            "step_id": write.step_id,
            "status": write.status,
            "message": write.message,
            "created_at": cast(datetime, created[0]).isoformat(),
        }

    def events(self, run_id: str) -> list[dict[str, Any]]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT event_id, sequence, event_type, flow_id, phase, step_id,
                       status, message, created_at
                FROM test_data_execution_events
                WHERE run_id = %s
                ORDER BY sequence
                """,
                (run_id,),
            )
            rows = cursor.fetchall()
        return [
            {
                "event_id": str(row[0]),
                "sequence": int(row[1]),
                "event_type": str(row[2]),
                "flow_id": str(row[3]) if row[3] is not None else None,
                "phase": str(row[4]) if row[4] is not None else None,
                "step_id": str(row[5]) if row[5] is not None else None,
                "status": str(row[6]) if row[6] is not None else None,
                "message": str(row[7]) if row[7] is not None else None,
                "created_at": cast(datetime, row[8]).isoformat(),
            }
            for row in rows
        ]

    def recovery(self, run_id: str) -> dict[str, Any] | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT recovery_id, actor, reason, stale_before, created_at
                FROM test_data_execution_recoveries
                WHERE run_id = %s
                """,
                (run_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return {
            "recovery_id": str(row[0]),
            "actor": str(row[1]),
            "reason": str(row[2]),
            "stale_before": cast(datetime, row[3]).isoformat(),
            "created_at": cast(datetime, row[4]).isoformat(),
        }

    def recover(
        self,
        *,
        artifact: dict[str, Any],
        recovery: TestDataExecutionRecoveryWrite,
    ) -> TestDataExecutionRecord:
        if artifact.get("status") != "interrupted":
            raise ValueError("Recovered Test data Run must be interrupted")
        boundary = recovery.stale_before.astimezone(UTC)
        payload = {
            "recovery_id": recovery.recovery_id,
            "run_id": recovery.run_id,
            "project_id": recovery.project_id,
            "actor": recovery.actor,
            "reason": recovery.reason,
            "stale_before": boundary.isoformat(),
        }
        digest = hashlib.sha256(_json(payload).encode()).hexdigest()
        with self._connection.transaction(), self._connection.cursor() as cursor:
            current = self._load(cursor, recovery.run_id, for_update=True)
            if current is None or current.project_id != recovery.project_id:
                raise ValueError("Test data recovery Run does not exist")
            cursor.execute(
                """
                SELECT recovery_id, actor, reason, stale_before, payload_digest
                FROM test_data_execution_recoveries
                WHERE run_id = %s OR recovery_id = %s
                """,
                (recovery.run_id, recovery.recovery_id),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if tuple(existing) != (
                    recovery.recovery_id,
                    recovery.actor,
                    recovery.reason,
                    boundary,
                    digest,
                ):
                    raise PersistenceConflictError(
                        "Test data recovery identity has different content"
                    )
                completed = self.complete(artifact)
                return completed
            if current.status != "running":
                raise ValueError("Only a running Test data Run can be recovered")
            cursor.execute("SELECT %s <= clock_timestamp()", (boundary,))
            boundary_row = cursor.fetchone()
            if boundary_row is None or not bool(boundary_row[0]):
                raise ValueError("Test data recovery boundary must not be in the future")
            if current.started_at > boundary:
                raise ValueError("Test data Run is newer than the recovery boundary")
            cursor.execute(
                """
                SELECT COALESCE(MAX(created_at), %s)
                FROM test_data_execution_events
                WHERE run_id = %s
                """,
                (current.started_at, current.run_id),
            )
            progress_row = cursor.fetchone()
            if progress_row is None or cast(datetime, progress_row[0]) > boundary:
                raise ValueError("Test data Run has progress newer than the recovery boundary")
            completed = self.complete(artifact)
            cursor.execute(
                """
                INSERT INTO test_data_execution_recoveries (
                    recovery_id, run_id, project_id, actor, reason,
                    stale_before, payload_digest
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    recovery.recovery_id,
                    recovery.run_id,
                    recovery.project_id,
                    recovery.actor,
                    recovery.reason,
                    boundary,
                    digest,
                ),
            )
            self.append_event(
                TestDataExecutionEventWrite(
                    run_id=recovery.run_id,
                    project_id=recovery.project_id,
                    event_type="recovered",
                    status="interrupted",
                    message="Stale Run was explicitly recovered.",
                )
            )
        return completed

    def latest_active_scope(
        self, *, orchestration_id: str, project_id: str, at: datetime
    ) -> dict[str, Any]:
        if at.utcoffset() is None:
            raise ValueError("Test data authorization time must include a timezone")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM change_orchestrations
                WHERE orchestration_id = %s AND project_id = %s
                """,
                (orchestration_id, project_id),
            )
            if cursor.fetchone() is None:
                raise ValueError("No active Approval Grant permits TestDataPlan execution")
        authorization = self._case_execution_authorizations.state(
            target_orchestration_id=orchestration_id,
            at=at,
        )
        if authorization["project_id"] != project_id:
            raise ValueError("Test data execution project differs from Orchestration")
        if authorization["authorized"] is not True:
            raise ValueError(str(authorization["blocking_reason"]))
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT environment.base_url
                FROM change_orchestrations AS orchestration
                JOIN ui_execution_plans AS plan
                  ON plan.project_id = orchestration.project_id
                 AND plan.analysis_case_id = orchestration.analysis_case_id
                JOIN ui_environments AS environment
                  ON environment.environment_id = plan.environment_id
                 AND environment.project_id = plan.project_id
                JOIN ui_deployments AS deployment
                  ON deployment.deployment_revision = plan.deployment_revision
                 AND deployment.environment_id = plan.environment_id
                 AND deployment.project_id = plan.project_id
                WHERE orchestration.orchestration_id = %s
                  AND orchestration.project_id = %s
                  AND environment.status = 'active'
                  AND deployment.status = 'ready'
                  AND plan.status IN ('ready', 'completed')
                ORDER BY plan.created_at DESC, plan.ui_execution_plan_id DESC
                LIMIT 1
                """,
                (orchestration_id, project_id),
            )
            environment = cursor.fetchone()
        return {
            "approval_grant_id": str(authorization["approval_grant_id"]),
            "base_url": str(environment[0]) if environment is not None else None,
            "authorization_status": authorization["status"],
            "authorization_id": authorization["authorization_id"],
        }

    def base_url_for_orchestration(self, *, orchestration_id: str, project_id: str) -> str | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT environment.base_url
                FROM change_orchestrations AS orchestration
                JOIN ui_execution_plans AS plan
                  ON plan.project_id = orchestration.project_id
                 AND plan.analysis_case_id = orchestration.analysis_case_id
                JOIN ui_environments AS environment
                  ON environment.environment_id = plan.environment_id
                 AND environment.project_id = plan.project_id
                JOIN ui_deployments AS deployment
                  ON deployment.deployment_revision = plan.deployment_revision
                 AND deployment.environment_id = plan.environment_id
                 AND deployment.project_id = plan.project_id
                WHERE orchestration.orchestration_id = %s
                  AND orchestration.project_id = %s
                  AND environment.status = 'active'
                  AND deployment.status = 'ready'
                  AND plan.status IN ('ready', 'completed')
                ORDER BY plan.created_at DESC, plan.ui_execution_plan_id DESC
                LIMIT 1
                """,
                (orchestration_id, project_id),
            )
            row = cursor.fetchone()
        return str(row[0]) if row is not None else None

    def latest_for_orchestration(self, orchestration_id: str) -> dict[str, Any] | None:
        """Return the latest bounded Run and its immutable Result, when complete."""
        if not orchestration_id.strip():
            raise ValueError("Orchestration ID must not be blank")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT run_id
                FROM test_data_execution_runs
                WHERE orchestration_id = %s
                ORDER BY started_at DESC, run_id DESC
                LIMIT 1
                """,
                (orchestration_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            record = self._load(cursor, str(row[0]), for_update=False)
        if record is None:
            raise RuntimeError("Latest Test data Run disappeared during read")
        result = None if record.status == "running" else self.get_result(record.run_id)
        return {
            "run_id": record.run_id,
            "execution_result_id": record.execution_result_id,
            "orchestration_id": record.orchestration_id,
            "test_data_plan_id": record.test_data_plan_id,
            "approval_grant_id": record.approval_grant_id,
            "project_id": record.project_id,
            "analysis_case_id": record.analysis_case_id,
            "status": record.status,
            "started_at": record.started_at.isoformat(),
            "completed_at": (
                record.completed_at.isoformat() if record.completed_at is not None else None
            ),
            "replay_of_run_id": record.replay_of_run_id,
            "events": self.events(record.run_id),
            "recovery": self.recovery(record.run_id),
            "result": result,
        }

    @staticmethod
    def _load(
        cursor: Cursor[Any], run_id: str, *, for_update: bool
    ) -> TestDataExecutionRecord | None:
        locking = " FOR UPDATE" if for_update else ""
        cursor.execute(
            """
            SELECT execution_result_id, orchestration_id, test_data_plan_id,
                   approval_grant_id, project_id, analysis_case_id, status,
                   started_at, completed_at, replay_of_run_id
            FROM test_data_execution_runs
            WHERE run_id = %s
            """
            + locking,
            (run_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return TestDataExecutionRecord(
            created=False,
            run_id=run_id,
            execution_result_id=str(row[0]),
            orchestration_id=str(row[1]),
            test_data_plan_id=str(row[2]),
            approval_grant_id=str(row[3]),
            project_id=str(row[4]),
            analysis_case_id=str(row[5]),
            status=str(row[6]),
            started_at=cast(datetime, row[7]),
            completed_at=cast(datetime | None, row[8]),
            replay_of_run_id=str(row[9]) if row[9] is not None else None,
        )

    @staticmethod
    def _created_by(cursor: Cursor[Any], run_id: str) -> str:
        cursor.execute(
            "SELECT created_by FROM test_data_execution_runs WHERE run_id = %s",
            (run_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Test data execution reservation disappeared")
        return str(row[0])

    @staticmethod
    def _normalized_matches(cursor: Cursor[Any], run_id: str, artifact: dict[str, Any]) -> bool:
        cursor.execute(
            """
            SELECT flow_id, execution_order, status, deferred_assertion_ids
            FROM test_data_flow_results
            WHERE run_id = %s ORDER BY execution_order
            """,
            (run_id,),
        )
        actual_flows = cursor.fetchall()
        expected_flows = [
            (
                flow["flow_id"],
                order,
                flow["status"],
                flow["deferred_assertion_ids"],
            )
            for order, flow in enumerate(
                cast(list[dict[str, Any]], artifact["flow_results"]), start=1
            )
        ]
        if actual_flows != expected_flows:
            return False
        cursor.execute(
            """
            SELECT flow_id, phase, step_id, sequence, channel, status,
                   output_variables, evidence_refs, failure_reason
            FROM test_data_step_results
            WHERE run_id = %s
            ORDER BY flow_id, phase, sequence
            """,
            (run_id,),
        )
        actual_steps = cursor.fetchall()
        expected_steps = sorted(
            (
                flow["flow_id"],
                step["phase"],
                step["step_id"],
                step["sequence"],
                step["channel"],
                step["status"],
                step["output_variables"],
                step["evidence_refs"],
                step.get("failure_reason"),
            )
            for flow in cast(list[dict[str, Any]], artifact["flow_results"])
            for step in _flow_steps(flow)
        )
        if actual_steps != expected_steps:
            return False
        cursor.execute(
            """
            SELECT evidence_id, flow_id, phase, step_id, evidence_type,
                   evidence_ref, content_digest, sanitized
            FROM test_data_execution_evidence
            WHERE run_id = %s ORDER BY evidence_id
            """,
            (run_id,),
        )
        actual_evidence = cursor.fetchall()
        expected_evidence = sorted(
            (
                value["evidence_id"],
                value["flow_id"],
                value["phase"],
                value["step_id"],
                value["evidence_type"],
                value["evidence_ref"],
                value["content_digest"],
                value["sanitized"],
            )
            for value in cast(list[dict[str, Any]], artifact["evidence"])
        )
        return actual_evidence == expected_evidence


def _flow_steps(flow: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    return tuple(
        cast(list[dict[str, Any]], flow["step_results"])
        + cast(list[dict[str, Any]], flow["cleanup_results"])
    )


def _validate_evidence_bindings(artifact: dict[str, Any]) -> None:
    evidence = cast(list[dict[str, Any]], artifact["evidence"])
    by_ref = {str(value["evidence_ref"]): value for value in evidence}
    evidence_ids = [str(value["evidence_id"]) for value in evidence]
    if len(by_ref) != len(evidence) or len(set(evidence_ids)) != len(evidence_ids):
        raise ValueError("Test data Evidence IDs and refs must be unique")
    referenced: set[str] = set()
    step_scopes: set[tuple[str, str, str]] = set()
    for flow in cast(list[dict[str, Any]], artifact["flow_results"]):
        flow_id = str(flow["flow_id"])
        for step in _flow_steps(flow):
            scope = (flow_id, str(step["phase"]), str(step["step_id"]))
            step_scopes.add(scope)
            for reference in cast(list[str], step["evidence_refs"]):
                value = by_ref.get(reference)
                if value is None:
                    raise ValueError(f"Step Evidence ref is missing: {reference}")
                if (
                    str(value["flow_id"]),
                    str(value["phase"]),
                    str(value["step_id"]),
                ) != scope:
                    raise ValueError(f"Step Evidence ref has a different scope: {reference}")
                referenced.add(reference)
    if set(by_ref) != referenced:
        raise ValueError("Every Test data Evidence record must be referenced by its step")
    if any(
        (str(value["flow_id"]), str(value["phase"]), str(value["step_id"])) not in step_scopes
        for value in evidence
    ):
        raise ValueError("Test data Evidence refers to an unknown step")


def _strings(value: object) -> frozenset[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PersistenceConflictError("Approval Grant allowed_actions are invalid")
    return frozenset(cast(list[str], value))


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("Test data result timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _event_id(run_id: str, sequence: int, event_type: str) -> str:
    digest = hashlib.sha256(f"{run_id}\0{sequence}\0{event_type}".encode()).hexdigest()[:24]
    return f"test-data-event-{digest}"
