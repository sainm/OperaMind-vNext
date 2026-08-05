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
from operamind.run_context_values import build_test_data_token, canonical_digest


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
    test_data_token: str | None = None
    runtime_variables: dict[str, object] | None = None
    replay_of_run_id: str | None = None
    execution_owner: str | None = None
    heartbeat_at: datetime | None = None
    lease_expires_at: datetime | None = None
    attempt_count: int = 0
    max_attempts: int = 3


@dataclass(frozen=True, slots=True)
class TestDataExecutionClaim:
    """Atomic execution-lease decision for one persisted TestData Run."""

    outcome: str
    record: TestDataExecutionRecord


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
        test_data_token = build_test_data_token(
            project_id=write.project_id,
            run_id=write.run_id,
            started_at=started_at,
        )
        runtime_variables = {
            "operamind_run_id": write.run_id,
            "test_data_token": test_data_token,
            "execution_started_at": started_at.isoformat().replace("+00:00", "Z"),
        }
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
                    replay_of_run_id, test_data_token, runtime_variables
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, 'running', %s, %s, %s,
                    %s, %s::jsonb
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
                    test_data_token,
                    _json(runtime_variables),
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

    def complete(
        self,
        artifact: dict[str, Any],
        *,
        execution_owner: str | None = None,
    ) -> TestDataExecutionRecord:
        self._contracts.validate_artifact(artifact)
        if artifact.get("artifact_type") != "TestDataExecutionResult":
            raise ValueError("Test data completion requires TestDataExecutionResult")
        if execution_owner is not None and not execution_owner.strip():
            raise ValueError("Test data completion owner must not be blank")
        run_id = str(artifact["run_id"])
        project_id = str(artifact["project_id"])
        completed_at = _timestamp(str(artifact["completed_at"]))
        _validate_evidence_bindings(artifact)
        _validate_coverage_evidence(artifact)
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
            plan = self._artifacts.get(current.test_data_plan_id)
            if (
                plan is None
                or plan.get("artifact_type") != "TestDataPlan"
                or plan.get("project_id") != current.project_id
            ):
                raise PersistenceConflictError(
                    "Test data coverage requires the reserved TestDataPlan"
                )
            _validate_coverage_evidence(artifact, plan=plan)
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
                        status, deferred_assertion_ids, test_data_binding_refs
                    ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                    """,
                    (
                        run_id,
                        project_id,
                        flow["flow_id"],
                        flow_order,
                        flow["status"],
                        _json(flow["deferred_assertion_ids"]),
                        _json(flow.get("test_data_binding_refs", [])),
                    ),
                )
                for step in _flow_steps(flow):
                    cursor.execute(
                        """
                        INSERT INTO test_data_step_results (
                            run_id, project_id, flow_id, phase, step_id,
                            sequence, channel, status, output_variables,
                            evidence_refs, failure_reason, test_data_binding_refs
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s,
                            %s::jsonb, %s::jsonb, %s, %s::jsonb
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
                            _json(step.get("test_data_binding_refs", [])),
                        ),
                    )
            all_evidence = cast(list[dict[str, Any]], artifact["evidence"])
            # Binding provenance Evidence must exist before its Binding FK is
            # inserted. Evidence that points at a Binding is inserted only
            # after the Binding exists, so the reverse FK can stay immediate
            # and never leave pending trigger events in caller transactions.
            for evidence in (
                value
                for value in all_evidence
                if value.get("test_data_binding_ref") is None
            ):
                cursor.execute(
                    """
                    INSERT INTO test_data_execution_evidence (
                        evidence_id, run_id, project_id, flow_id, phase,
                        step_id, evidence_type, evidence_ref,
                        content_digest, sanitized, test_data_binding_ref
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                        evidence.get("test_data_binding_ref"),
                    ),
                )
            run_context = artifact.get("run_context")
            if isinstance(run_context, dict):
                runtime_variables = cast(dict[str, object], run_context["runtime_variables"])
                if (
                    runtime_variables != current.runtime_variables
                    or runtime_variables.get("test_data_token") != current.test_data_token
                ):
                    raise ValueError("RunContext variables differ from the reserved Run")
                context_payload = {
                    "run_id": run_id,
                    "project_id": project_id,
                    **run_context,
                }
                cursor.execute(
                    """
                    INSERT INTO test_data_run_contexts (
                        run_id, project_id, runtime_variables,
                        flow_dependencies, evidence_refs, content_digest
                    ) VALUES (%s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s)
                    """,
                    (
                        run_id,
                        project_id,
                        _json(runtime_variables),
                        _json(run_context["flow_dependencies"]),
                        _json(run_context["evidence_refs"]),
                        canonical_digest(context_payload),
                    ),
                )
            for binding in cast(list[dict[str, Any]], artifact["data_bindings"]):
                cursor.execute(
                    """
                    INSERT INTO test_data_identity_bindings (
                        binding_id, run_id, project_id, test_data_id, binding_mode,
                        source_flow_id, source_phase, source_step_id,
                        identity_provider_type, identity_provider_ref, primary_key,
                        business_unique_keys, screen_key, screen_locator, match_count,
                        screen_identity_values, record_scope_locator,
                        identity_observations, identity_digest,
                        frozen_at, content_digest, evidence_ref
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, 'setup', %s, %s, %s, %s::jsonb,
                        %s::jsonb, %s::jsonb, %s::jsonb, %s,
                        %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s
                    )
                    """,
                    (
                        binding["binding_id"],
                        run_id,
                        project_id,
                        binding["test_data_id"],
                        binding["binding_mode"],
                        binding["source_flow_id"],
                        binding["source_step_id"],
                        binding.get("identity_provider_type"),
                        binding.get("identity_provider_ref"),
                        _json(binding["primary_key"]),
                        _json(binding["business_unique_keys"]),
                        _json(binding["screen_key"]),
                        _json(binding["screen_locator"]),
                        binding["match_count"],
                        _optional_json(binding.get("screen_identity_values")),
                        _optional_json(binding.get("record_scope_locator")),
                        _optional_json(binding.get("identity_observations")),
                        binding.get("identity_digest"),
                        _timestamp(str(binding["frozen_at"])),
                        binding["content_digest"],
                        binding["evidence_ref"],
                    ),
                )
            for evidence in (
                value
                for value in all_evidence
                if value.get("test_data_binding_ref") is not None
            ):
                cursor.execute(
                    """
                    INSERT INTO test_data_execution_evidence (
                        evidence_id, run_id, project_id, flow_id, phase,
                        step_id, evidence_type, evidence_ref,
                        content_digest, sanitized, test_data_binding_ref
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                        evidence["test_data_binding_ref"],
                    ),
                )
            cursor.execute(
                """
                UPDATE test_data_execution_runs
                SET status = %s, result_artifact_id = execution_result_id,
                    completed_at = %s, execution_owner = NULL,
                    heartbeat_at = NULL, lease_expires_at = NULL
                WHERE run_id = %s AND status = 'running'
                  AND (
                      %s::text IS NULL
                      OR (
                          execution_owner = %s
                          AND lease_expires_at > now()
                      )
                  )
                """,
                (
                    artifact["status"],
                    completed_at,
                    run_id,
                    execution_owner,
                    execution_owner,
                ),
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
            test_data_token=record.test_data_token,
            runtime_variables=record.runtime_variables,
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

    def claim_execution(
        self,
        *,
        run_id: str,
        executor_id: str,
        at: datetime,
        lease_seconds: int,
    ) -> TestDataExecutionClaim:
        """Claim an unowned Run, or report a live/stale/terminal reservation."""

        if not run_id.strip() or not executor_id.strip():
            raise ValueError("Test data execution Claim fields must not be blank")
        if at.utcoffset() is None:
            raise ValueError("Test data execution Claim time must include a timezone")
        if not 30 <= lease_seconds <= 3600:
            raise ValueError("Test data execution lease_seconds must be between 30 and 3600")
        claimed_at = at.astimezone(UTC)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            record = self._load(cursor, run_id, for_update=True)
            if record is None:
                raise ValueError("Reserved Test data Run does not exist")
            if record.status != "running":
                return TestDataExecutionClaim("terminal", record)
            if record.execution_owner is not None:
                if record.lease_expires_at is None:
                    raise PersistenceConflictError("Test data execution lease is incomplete")
                if record.lease_expires_at > claimed_at:
                    return TestDataExecutionClaim("busy", record)
                self._take_recovery_claim(
                    cursor,
                    run_id=run_id,
                    executor_id=executor_id,
                    claimed_at=claimed_at,
                    lease_seconds=lease_seconds,
                )
                return TestDataExecutionClaim("stale", record)
            if record.attempt_count >= record.max_attempts:
                self._take_recovery_claim(
                    cursor,
                    run_id=run_id,
                    executor_id=executor_id,
                    claimed_at=claimed_at,
                    lease_seconds=lease_seconds,
                )
                return TestDataExecutionClaim("exhausted", record)
            cursor.execute(
                """
                UPDATE test_data_execution_runs
                SET execution_owner = %s,
                    heartbeat_at = %s,
                    lease_expires_at = %s + make_interval(secs => %s),
                    attempt_count = attempt_count + 1
                WHERE run_id = %s AND status = 'running'
                  AND execution_owner IS NULL
                """,
                (executor_id, claimed_at, claimed_at, lease_seconds, run_id),
            )
            if cursor.rowcount != 1:
                raise PersistenceConflictError("Test data execution Claim lost its lock")
            claimed = self._load(cursor, run_id, for_update=False)
        if claimed is None:
            raise RuntimeError("Test data execution Claim was not persisted")
        return TestDataExecutionClaim("claimed", claimed)

    @staticmethod
    def _take_recovery_claim(
        cursor: Cursor[Any],
        *,
        run_id: str,
        executor_id: str,
        claimed_at: datetime,
        lease_seconds: int,
    ) -> None:
        cursor.execute(
            """
            UPDATE test_data_execution_runs
            SET execution_owner = %s,
                heartbeat_at = %s,
                lease_expires_at = %s + make_interval(secs => %s)
            WHERE run_id = %s AND status = 'running'
            """,
            (executor_id, claimed_at, claimed_at, lease_seconds, run_id),
        )
        if cursor.rowcount != 1:
            raise PersistenceConflictError("Test data recovery Claim lost its lock")

    def heartbeat_execution(
        self,
        *,
        run_id: str,
        executor_id: str,
        at: datetime,
        lease_seconds: int,
    ) -> bool:
        """Renew only the live Claim owned by the current executor."""

        if not run_id.strip() or not executor_id.strip():
            raise ValueError("Test data execution heartbeat fields must not be blank")
        if at.utcoffset() is None:
            raise ValueError("Test data execution heartbeat time must include a timezone")
        if not 30 <= lease_seconds <= 3600:
            raise ValueError("Test data execution lease_seconds must be between 30 and 3600")
        heartbeat_at = at.astimezone(UTC)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE test_data_execution_runs
                SET heartbeat_at = %s,
                    lease_expires_at = %s + make_interval(secs => %s)
                WHERE run_id = %s AND status = 'running'
                  AND execution_owner = %s
                  AND lease_expires_at > %s
                """,
                (
                    heartbeat_at,
                    heartbeat_at,
                    lease_seconds,
                    run_id,
                    executor_id,
                    heartbeat_at,
                ),
            )
            return cursor.rowcount == 1

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
                "downstream_publication_failed",
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
        return {
            "approval_grant_id": str(authorization["approval_grant_id"]),
            "authorization_status": authorization["status"],
            "authorization_id": authorization["authorization_id"],
        }

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
                   started_at, completed_at, replay_of_run_id,
                   execution_owner, heartbeat_at, lease_expires_at,
                   attempt_count, max_attempts, test_data_token, runtime_variables
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
            execution_owner=str(row[10]) if row[10] is not None else None,
            heartbeat_at=cast(datetime | None, row[11]),
            lease_expires_at=cast(datetime | None, row[12]),
            attempt_count=int(row[13]),
            max_attempts=int(row[14]),
            test_data_token=str(row[15]) if row[15] is not None else None,
            runtime_variables=(
                cast(dict[str, object], row[16]) if row[16] is not None else None
            ),
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
            SELECT flow_id, execution_order, status, deferred_assertion_ids,
                   test_data_binding_refs
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
                flow.get("test_data_binding_refs", []),
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
                   output_variables, evidence_refs, failure_reason,
                   test_data_binding_refs
            FROM test_data_step_results
            WHERE run_id = %s
            ORDER BY flow_id, phase, sequence
            """,
            (run_id,),
        )
        actual_steps = cursor.fetchall()
        expected_steps = sorted(
            (
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
                    step.get("test_data_binding_refs", []),
                )
                for flow in cast(list[dict[str, Any]], artifact["flow_results"])
                for step in _flow_steps(flow)
            ),
            key=lambda value: (value[0], value[1], value[3]),
        )
        if actual_steps != expected_steps:
            return False
        cursor.execute(
            """
            SELECT binding_id, test_data_id, binding_mode, source_flow_id,
                   source_step_id, identity_provider_type, identity_provider_ref,
                   primary_key, business_unique_keys, screen_key,
                   screen_locator, match_count, screen_identity_values,
                   record_scope_locator, identity_observations, identity_digest,
                   frozen_at, content_digest, evidence_ref
            FROM test_data_identity_bindings
            WHERE run_id = %s ORDER BY test_data_id
            """,
            (run_id,),
        )
        actual_bindings = cursor.fetchall()
        expected_bindings = sorted(
            (
                (
                    value["binding_id"],
                    value["test_data_id"],
                    value["binding_mode"],
                    value["source_flow_id"],
                    value["source_step_id"],
                    value.get("identity_provider_type"),
                    value.get("identity_provider_ref"),
                    value["primary_key"],
                    value["business_unique_keys"],
                    value["screen_key"],
                    value["screen_locator"],
                    value["match_count"],
                    value.get("screen_identity_values"),
                    value.get("record_scope_locator"),
                    value.get("identity_observations"),
                    value.get("identity_digest"),
                    _timestamp(str(value["frozen_at"])),
                    value["content_digest"],
                    value["evidence_ref"],
                )
                for value in cast(list[dict[str, Any]], artifact["data_bindings"])
            ),
            key=lambda value: value[1],
        )
        if actual_bindings != expected_bindings:
            return False
        cursor.execute(
            """
            SELECT evidence_id, flow_id, phase, step_id, evidence_type,
                   evidence_ref, content_digest, sanitized, test_data_binding_ref
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
                value.get("test_data_binding_ref"),
            )
            for value in cast(list[dict[str, Any]], artifact["evidence"])
        )
        if actual_evidence != expected_evidence:
            return False
        run_context = artifact.get("run_context")
        if not isinstance(run_context, dict):
            return True
        cursor.execute(
            """
            SELECT runtime_variables, flow_dependencies, evidence_refs
            FROM test_data_run_contexts
            WHERE run_id = %s
            """,
            (run_id,),
        )
        row = cursor.fetchone()
        return row == (
            run_context["runtime_variables"],
            run_context["flow_dependencies"],
            run_context["evidence_refs"],
        )


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
    bindings = cast(list[dict[str, Any]], artifact["data_bindings"])
    test_data_ids = [str(value["test_data_id"]) for value in bindings]
    binding_ids = [str(value["binding_id"]) for value in bindings]
    if len(test_data_ids) != len(set(test_data_ids)) or len(binding_ids) != len(
        set(binding_ids)
    ):
        raise ValueError("Test data bindings must be unique within one Run")
    for binding in bindings:
        evidence_value = by_ref.get(str(binding["evidence_ref"]))
        if evidence_value is None or (
            evidence_value.get("evidence_type") != "data_binding"
            or evidence_value.get("flow_id") != binding["source_flow_id"]
            or evidence_value.get("step_id") != binding["source_step_id"]
            or evidence_value.get("phase") != "setup"
            or evidence_value.get("content_digest") != binding["content_digest"]
        ):
            raise ValueError(
                f"Frozen data binding has no matching Evidence: {binding['test_data_id']}"
            )
        payload = {
            key: value
            for key, value in binding.items()
            if key not in {"content_digest", "evidence_ref"}
        }
        if hashlib.sha256(_json(payload).encode()).hexdigest() != binding["content_digest"]:
            raise ValueError(f"Frozen data binding digest differs: {binding['test_data_id']}")
        if artifact.get("schema_version") in {"v2", "v3"}:
            screen_values = binding["screen_identity_values"]
            if (
                not isinstance(screen_values, list)
                or not screen_values
                or screen_values[0] != binding["screen_key"]
                or binding["record_scope_locator"] != binding["screen_locator"]
            ):
                raise ValueError(
                    f"Frozen data binding compatibility aliases differ: "
                    f"{binding['test_data_id']}"
                )
            identity_payload = {
                "business_unique_keys": binding["business_unique_keys"],
                "screen_identity_values": screen_values,
            }
            if (
                hashlib.sha256(_json(identity_payload).encode()).hexdigest()
                != binding["identity_digest"]
            ):
                raise ValueError(
                    f"Frozen data binding identity digest differs: {binding['test_data_id']}"
                )

    if artifact.get("schema_version") == "v3":
        _validate_run_binding_refs(artifact, bindings=bindings)


def _validate_run_binding_refs(
    artifact: dict[str, Any], *, bindings: list[dict[str, Any]]
) -> None:
    run_id = str(artifact["run_id"])
    project_id = str(artifact["project_id"])
    binding_ids = {str(value["binding_id"]) for value in bindings}
    for binding in bindings:
        if binding.get("run_id") != run_id or binding.get("project_id") != project_id:
            raise ValueError("Frozen data Binding belongs to another Run or Project")
    allowed_unbound_screenshot_refs: set[str] = set()
    for flow in cast(list[dict[str, Any]], artifact["flow_results"]):
        observed: set[str] = set()
        for step in _flow_steps(flow):
            refs = set(cast(list[str], step.get("test_data_binding_refs", [])))
            if not refs.issubset(binding_ids):
                raise ValueError("StepResult references a foreign TestDataBinding")
            observed.update(refs)
            if (
                step.get("channel") == "ui"
                and step.get("status") == "blocked"
                and not refs
                and isinstance(step.get("failure_stage"), str)
                and str(step["failure_stage"]).strip()
            ):
                allowed_unbound_screenshot_refs.update(
                    str(value)
                    for value in cast(list[object], step.get("evidence_refs", []))
                    if isinstance(value, str)
                )
        if observed != set(cast(list[str], flow.get("test_data_binding_refs", []))):
            raise ValueError("FlowResult TestDataBinding refs differ from its Steps")
    for evidence in cast(list[dict[str, Any]], artifact["evidence"]):
        binding_ref = evidence.get("test_data_binding_ref")
        if binding_ref is not None and str(binding_ref) not in binding_ids:
            raise ValueError("Evidence references a foreign TestDataBinding")
        if (
            evidence.get("evidence_type") == "screenshot"
            and binding_ref is None
            and evidence.get("evidence_ref") not in allowed_unbound_screenshot_refs
        ):
            raise ValueError("Screenshot Evidence has no current TestDataBinding")


def _validate_coverage_evidence(
    artifact: dict[str, Any], *, plan: dict[str, Any] | None = None
) -> None:
    coverage = cast(dict[str, Any], artifact["data_coverage"])
    proofs = cast(list[dict[str, Any]], coverage["proofs"])
    required_count = int(coverage["required_criterion_count"])
    covered_count = int(coverage["covered_criterion_count"])
    condition_count = int(coverage["condition_count"])
    passed_count = int(coverage["passed_condition_count"])
    coverage_percent = float(coverage["coverage_percent"])
    coverage_status = str(coverage["status"])
    if not (0 <= covered_count <= required_count):
        raise ValueError("Test data coverage criterion counts are invalid")
    expected_percent = covered_count * 100 / required_count if required_count else 0
    if coverage_percent != expected_percent:
        raise ValueError("Test data coverage percent differs from criterion counts")
    if condition_count < len(proofs):
        raise ValueError("Test data coverage contains more proofs than planned conditions")
    condition_ids = [str(value["condition_id"]) for value in proofs]
    proof_ids = [str(value["proof_id"]) for value in proofs]
    if len(condition_ids) != len(set(condition_ids)) or len(proof_ids) != len(
        set(proof_ids)
    ):
        raise ValueError("Test data coverage proofs must be unique within one Run")
    evidence_by_ref = {
        str(value["evidence_ref"]): value
        for value in cast(list[dict[str, Any]], artifact["evidence"])
    }
    for proof in proofs:
        evidence = evidence_by_ref.get(str(proof["evidence_ref"]))
        if (
            evidence is None
            or evidence.get("evidence_type") != "data_coverage"
            or evidence.get("flow_id") != proof["source_flow_id"]
            or evidence.get("step_id") != proof["source_step_id"]
            or evidence.get("phase") != "setup"
            or evidence.get("content_digest") != proof["content_digest"]
        ):
            raise ValueError(
                "Test data coverage proof has no matching Evidence: "
                f"{proof['condition_id']}"
            )
        payload = {
            key: value
            for key, value in proof.items()
            if key not in {"content_digest", "evidence_ref"}
        }
        if hashlib.sha256(_json(payload).encode()).hexdigest() != proof["content_digest"]:
            raise ValueError(
                f"Test data coverage proof digest differs: {proof['condition_id']}"
            )
    passed = [value for value in proofs if value["status"] == "passed"]
    if passed_count != len(passed):
        raise ValueError("Test data coverage passed condition count differs")
    passed_criteria = {str(value["criterion_ref"]) for value in passed}
    if covered_count > len(passed_criteria):
        raise ValueError("Test data coverage covered criteria have no passed proof")
    if coverage_status == "not_applicable":
        if any(
            value != 0
            for value in (
                required_count,
                covered_count,
                condition_count,
                passed_count,
                len(proofs),
            )
        ):
            raise ValueError("Not-applicable Test data coverage must be empty")
    elif coverage_status == "passed":
        if (
            required_count == 0
            or covered_count != required_count
            or condition_count == 0
            or len(proofs) != condition_count
            or passed_count != condition_count
            or len(passed_criteria) != required_count
        ):
            raise ValueError("Passed Test data coverage requires complete passed proofs")
    elif coverage_status == "failed" and coverage_percent >= 100:
        raise ValueError("Failed Test data coverage cannot be 100%")
    if (
        artifact.get("schema_version") == "v2"
        and artifact["status"] == "passed"
        and coverage_status != "passed"
    ):
        raise ValueError("Passed v2 Test data execution requires 100% data coverage")
    if artifact["status"] == "passed" and coverage_status == "failed":
        raise ValueError("Passed Test data execution cannot contain failed data coverage")
    if plan is None:
        return
    planned_conditions = [
        (data_set, condition)
        for data_set in cast(list[dict[str, Any]], plan.get("data_sets", []))
        for condition in cast(list[dict[str, Any]], data_set.get("coverage_conditions", []))
    ]
    planned_by_id = {
        str(condition["condition_id"]): (data_set, condition)
        for data_set, condition in planned_conditions
    }
    if len(planned_by_id) != len(planned_conditions):
        raise ValueError("Reserved TestDataPlan coverage condition IDs are not unique")
    if condition_count != len(planned_conditions):
        raise ValueError("Test data coverage condition count differs from reserved plan")
    planned_criteria = {
        str(condition["criterion_ref"]) for _data_set, condition in planned_conditions
    }
    if required_count != len(planned_criteria):
        raise ValueError("Test data coverage criterion count differs from reserved plan")
    for proof in proofs:
        planned = planned_by_id.get(str(proof["condition_id"]))
        if planned is None:
            raise ValueError(
                f"Test data coverage proof is outside reserved plan: {proof['condition_id']}"
            )
        data_set, condition = planned
        for key in (
            "criterion_ref",
            "test_case_ref",
            "test_data_id",
            "condition_kind",
            "source_flow_id",
            "source_step_id",
            "path",
            "operator",
        ):
            if proof[key] != condition[key]:
                raise ValueError(
                    f"Test data coverage proof differs from reserved plan: "
                    f"{proof['condition_id']}/{key}"
                )
        if "expected" in condition and proof.get("expected") != condition["expected"]:
            raise ValueError(
                "Test data coverage expected value differs from reserved plan: "
                f"{proof['condition_id']}"
            )
        expected_source = _coverage_observation_source(data_set, condition)
        if not expected_source or proof.get("observation_source") != expected_source:
            raise ValueError(
                "Test data coverage observation source differs from reserved plan: "
                f"{proof['condition_id']}"
            )
    derived = _derive_coverage_summary(plan=plan, proofs=proofs)
    for key in (
        "required_criterion_count",
        "covered_criterion_count",
        "coverage_percent",
        "condition_count",
        "passed_condition_count",
        "status",
    ):
        if coverage[key] != derived[key]:
            raise ValueError(f"Test data coverage {key} was not engine-derived")


def _coverage_observation_source(
    data_set: dict[str, Any], condition: dict[str, Any]
) -> str:
    explicit = str(condition.get("source") or "")
    if explicit:
        return explicit
    identity = cast(dict[str, Any], data_set.get("identity_binding") or {})
    provider = cast(dict[str, Any], identity.get("provider") or {})
    return {
        "database": "database",
        "api": "response",
        "ui": "ui",
    }.get(str(provider.get("type") or ""), "")


def _derive_coverage_summary(
    *, plan: dict[str, Any], proofs: list[dict[str, Any]]
) -> dict[str, object]:
    conditions = [
        condition
        for data_set in cast(list[dict[str, Any]], plan.get("data_sets", []))
        for condition in cast(list[dict[str, Any]], data_set.get("coverage_conditions", []))
    ]
    required_by_criterion: dict[str, set[str]] = {}
    for condition in conditions:
        required_by_criterion.setdefault(str(condition["criterion_ref"]), set()).add(
            str(condition["condition_id"])
        )
    passed_ids = {
        str(value["condition_id"]) for value in proofs if value.get("status") == "passed"
    }
    covered = sum(
        bool(required) and required.issubset(passed_ids)
        for required in required_by_criterion.values()
    )
    required_count = len(required_by_criterion)
    return {
        "required_criterion_count": required_count,
        "covered_criterion_count": covered,
        "coverage_percent": covered * 100 / required_count if required_count else 0,
        "condition_count": len(conditions),
        "passed_condition_count": len(passed_ids),
        "status": (
            "not_applicable"
            if not required_count
            else "passed"
            if covered == required_count
            else "failed"
        ),
    }


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


def _optional_json(value: object | None) -> str | None:
    return None if value is None else _json(value)


def _event_id(run_id: str, sequence: int, event_type: str) -> str:
    digest = hashlib.sha256(f"{run_id}\0{sequence}\0{event_type}".encode()).hexdigest()[:24]
    return f"test-data-event-{digest}"
