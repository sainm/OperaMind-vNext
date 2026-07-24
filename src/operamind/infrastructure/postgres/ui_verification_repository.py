"""Versioned UI Scenario, Deployment, Plan, and Preflight persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from urllib.parse import urlsplit

from psycopg import Connection, Cursor

from operamind.infrastructure.postgres.errors import PersistenceConflictError

PREFLIGHT_TYPES = frozenset(
    {"environment", "authentication", "test_data", "trigger_path", "locator"}
)
UI_EVIDENCE_TYPES = frozenset({"screenshot", "assertion", "network_summary", "step_log"})


@dataclass(frozen=True, slots=True)
class VerificationScenarioWrite:
    scenario_version_id: str
    project_id: str
    scenario_id: str
    scenario_version: str
    title: str
    preconditions: tuple[str, ...]
    steps: tuple[str, ...]
    expected_visible_results: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    trigger_path: str
    data_recipe_ref: str | None
    review_status: str
    activate: bool
    test_case_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UiDeploymentWrite:
    project_id: str
    environment_id: str
    base_url: str
    deployment_revision: str
    repository_revision: str


@dataclass(frozen=True, slots=True)
class UiExecutionPlanWrite:
    plan_id: str
    project_id: str
    analysis_case_id: str
    edit_packet_id: str
    edit_result_id: str
    environment_id: str
    deployment_revision: str


@dataclass(frozen=True, slots=True)
class UiExecutionPlanRecord:
    created: bool
    plan_id: str
    status: str
    scenario_refs: tuple[str, ...]
    blocking_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UiPreflightCheckWrite:
    check_id: str
    check_type: str
    status: str
    evidence_ref: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class UiExecutionRunRecord:
    created: bool
    run_id: str
    status: str


@dataclass(frozen=True, slots=True)
class UiVerificationScope:
    project_id: str
    analysis_case_id: str
    edit_packet_id: str
    approval_grant_id: str
    plan_id: str
    run_id: str
    repository_revision: str
    deployment_revision: str
    environment_id: str
    plan_status: str
    run_status: str
    scenario_refs: tuple[str, ...]
    scenario_evidence_requirements: tuple[tuple[str, tuple[str, ...]], ...]
    impact_item_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UiExecutionEvidenceWrite:
    evidence_id: str
    scenario_id: str
    evidence_type: str
    evidence_ref: str
    content_digest: str
    sanitized: bool


@dataclass(frozen=True, slots=True)
class UiScenarioResultWrite:
    scenario_id: str
    status: str
    impact_item_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    failure_category: str
    summary: str | None = None


@dataclass(frozen=True, slots=True)
class UiVerificationCompletionWrite:
    verification_result_id: str
    status: str
    scenario_results: tuple[UiScenarioResultWrite, ...]
    evidence: tuple[UiExecutionEvidenceWrite, ...]
    unresolved_impact_item_ids: tuple[str, ...]
    out_of_scope_files: tuple[str, ...]
    failure_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UiVerificationResultRecord:
    created: bool
    verification_result_id: str
    status: str
    case_status: str


class UiVerificationRepository:
    """Guard UI execution with approved scenarios and exact deployment provenance."""

    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def store_scenario(self, write: VerificationScenarioWrite) -> bool:
        _validate_scenario(write)
        identity = (
            write.project_id,
            write.scenario_id,
            write.scenario_version,
            write.title,
            list(write.preconditions),
            list(write.steps),
            list(write.expected_visible_results),
            list(write.evidence_requirements),
            write.trigger_path,
            write.data_recipe_ref,
            write.review_status,
            list(write.test_case_refs),
        )
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT project_id, scenario_id, scenario_version, title, preconditions,
                       steps, expected_visible_results, evidence_requirements,
                       trigger_path, data_recipe_ref, review_status, test_case_refs
                FROM verification_scenarios WHERE scenario_version_id = %s
                """,
                (write.scenario_version_id,),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if tuple(existing) != identity:
                    raise PersistenceConflictError(
                        f"Scenario version has different content: {write.scenario_version_id}"
                    )
                return False
            if write.activate:
                cursor.execute(
                    """
                    UPDATE verification_scenarios SET is_active = false
                    WHERE project_id = %s AND scenario_id = %s AND is_active
                    """,
                    (write.project_id, write.scenario_id),
                )
            cursor.execute(
                """
                INSERT INTO verification_scenarios (
                    scenario_version_id, project_id, scenario_id, scenario_version,
                    title, preconditions, steps, expected_visible_results,
                    evidence_requirements, trigger_path, data_recipe_ref,
                    review_status, is_active, test_case_refs
                ) VALUES (
                    %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb,
                    %s::jsonb, %s, %s, %s, %s, %s::jsonb
                )
                """,
                (
                    write.scenario_version_id,
                    write.project_id,
                    write.scenario_id,
                    write.scenario_version,
                    write.title,
                    _json(list(write.preconditions)),
                    _json(list(write.steps)),
                    _json(list(write.expected_visible_results)),
                    _json(list(write.evidence_requirements)),
                    write.trigger_path,
                    write.data_recipe_ref,
                    write.review_status,
                    write.activate,
                    _json(list(write.test_case_refs)),
                ),
            )
        return True

    def register_deployment(self, write: UiDeploymentWrite) -> bool:
        if any(
            not value.strip()
            for value in (
                write.project_id,
                write.environment_id,
                write.base_url,
                write.deployment_revision,
                write.repository_revision,
            )
        ):
            raise ValueError("UI Deployment fields must not be blank")
        parsed_url = urlsplit(write.base_url)
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.netloc
            or parsed_url.path not in {"", "/"}
            or parsed_url.query
            or parsed_url.fragment
            or parsed_url.username is not None
            or parsed_url.password is not None
        ):
            raise ValueError(
                "UI Environment base_url must be an HTTP(S) origin without credentials"
            )
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO ui_environments (environment_id, project_id, base_url, status)
                VALUES (%s, %s, %s, 'active') ON CONFLICT DO NOTHING
                """,
                (write.environment_id, write.project_id, write.base_url),
            )
            cursor.execute(
                """
                SELECT project_id, base_url, status FROM ui_environments
                WHERE environment_id = %s
                """,
                (write.environment_id,),
            )
            if cursor.fetchone() != (write.project_id, write.base_url, "active"):
                raise PersistenceConflictError("UI Environment identity has different content")
            cursor.execute(
                """
                INSERT INTO ui_deployments (
                    deployment_revision, environment_id, project_id,
                    repository_revision, status
                ) VALUES (%s, %s, %s, %s, 'ready') ON CONFLICT DO NOTHING
                """,
                (
                    write.deployment_revision,
                    write.environment_id,
                    write.project_id,
                    write.repository_revision,
                ),
            )
            created = cursor.rowcount == 1
            cursor.execute(
                """
                SELECT project_id, repository_revision, status FROM ui_deployments
                WHERE environment_id = %s AND deployment_revision = %s
                """,
                (write.environment_id, write.deployment_revision),
            )
            if cursor.fetchone() != (write.project_id, write.repository_revision, "ready"):
                raise PersistenceConflictError("UI Deployment identity has different content")
        return created

    def create_plan(self, write: UiExecutionPlanWrite) -> UiExecutionPlanRecord:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            existing = self._load_plan(cursor, write.plan_id)
            if existing is not None:
                cursor.execute(
                    """
                    SELECT plan.project_id, plan.analysis_case_id, plan.edit_packet_id,
                           plan.edit_result_id, plan.environment_id,
                           plan.deployment_revision, plan.repository_binding_status,
                           plan.repository_revision, result.result_repository_revision,
                           deployment.repository_revision, plan.scenario_refs,
                           packet.required_ui_scenario_refs, result.edit_packet_id,
                           result.analysis_case_id
                    FROM ui_execution_plans AS plan
                    JOIN edit_results AS result
                      ON result.edit_result_id = plan.edit_result_id
                     AND result.project_id = plan.project_id
                    JOIN edit_packets AS packet
                      ON packet.edit_packet_id = plan.edit_packet_id
                     AND packet.project_id = plan.project_id
                    JOIN ui_deployments AS deployment
                      ON deployment.environment_id = plan.environment_id
                     AND deployment.project_id = plan.project_id
                     AND deployment.deployment_revision = plan.deployment_revision
                    WHERE plan.ui_execution_plan_id = %s
                    """,
                    (write.plan_id,),
                )
                identity = cursor.fetchone()
                expected_scope = (
                    write.project_id,
                    write.analysis_case_id,
                    write.edit_packet_id,
                    write.edit_result_id,
                    write.environment_id,
                    write.deployment_revision,
                    "verified",
                )
                if (
                    identity is None
                    or tuple(identity[:7]) != expected_scope
                    or not (str(identity[7]) == str(identity[8]) == str(identity[9]))
                    or tuple(identity[10]) != tuple(identity[11])
                    or (str(identity[12]), str(identity[13]))
                    != (write.edit_packet_id, write.analysis_case_id)
                    or not _plan_scenarios_match(
                        cursor,
                        plan_id=write.plan_id,
                        project_id=write.project_id,
                        scenario_refs=tuple(
                            str(value) for value in cast(list[object], identity[10])
                        ),
                        require_approved=True,
                    )
                ):
                    raise PersistenceConflictError(
                        f"UI Execution Plan has different scope: {write.plan_id}"
                    )
                return UiExecutionPlanRecord(False, write.plan_id, *existing)
            cursor.execute(
                """
                SELECT result.status, result.validation_mode, result.tests_passed,
                       result.command_evidence_status,
                       result.result_repository_revision, packet.required_ui_scenario_refs,
                       deployment.repository_revision, deployment.status, environment.status,
                       analysis_case.status
                FROM edit_results AS result
                JOIN edit_packets AS packet
                  ON packet.edit_packet_id = result.edit_packet_id
                 AND packet.project_id = result.project_id
                JOIN ui_environments AS environment
                  ON environment.environment_id = %s AND environment.project_id = result.project_id
                JOIN ui_deployments AS deployment
                  ON deployment.environment_id = environment.environment_id
                 AND deployment.project_id = environment.project_id
                 AND deployment.deployment_revision = %s
                JOIN analysis_cases AS analysis_case
                  ON analysis_case.analysis_case_id = result.analysis_case_id
                 AND analysis_case.project_id = result.project_id
                WHERE result.edit_result_id = %s AND result.project_id = %s
                  AND result.analysis_case_id = %s AND packet.edit_packet_id = %s
                FOR UPDATE OF analysis_case
                FOR SHARE OF result, packet, deployment, environment
                """,
                (
                    write.environment_id,
                    write.deployment_revision,
                    write.edit_result_id,
                    write.project_id,
                    write.analysis_case_id,
                    write.edit_packet_id,
                ),
            )
            source = cursor.fetchone()
            if source is None:
                raise ValueError("UI Plan source does not exist in requested scope")
            if tuple(source[:4]) != ("in_scope", "committed", True, "verified"):
                raise ValueError(
                    "UI Plan requires a committed in-scope passing Edit Result "
                    "with verified command evidence"
                )
            if str(source[4]) != str(source[6]):
                raise ValueError("Deployment Revision is not built from Edit Result commit")
            if tuple(source[7:9]) != ("ready", "active"):
                raise ValueError("UI Deployment or Environment is not ready")
            case_status = str(source[9])
            if case_status not in {"verifying_ui", "passed", "failed"}:
                raise ValueError("UI Plan requires an Analysis Case eligible for UI verification")
            if case_status != "verifying_ui":
                cursor.execute(
                    """
                    SELECT 1 FROM ui_execution_plans
                    WHERE project_id = %s AND analysis_case_id = %s
                      AND status IN ('preflight_pending', 'ready')
                    LIMIT 1
                    """,
                    (write.project_id, write.analysis_case_id),
                )
                if cursor.fetchone() is not None:
                    raise ValueError("UI re-verification already has an active Plan")
                cursor.execute(
                    """
                    UPDATE analysis_cases
                    SET status = 'verifying_ui', updated_at = now()
                    WHERE project_id = %s AND analysis_case_id = %s AND status = %s
                    """,
                    (write.project_id, write.analysis_case_id, case_status),
                )
                if cursor.rowcount != 1:
                    raise PersistenceConflictError(
                        "Analysis Case changed during UI re-verification"
                    )
            scenario_refs = tuple(str(value) for value in cast(list[object], source[5]))
            if not scenario_refs:
                raise ValueError("UI Plan requires at least one Packet Scenario")
            if len(scenario_refs) != len(set(scenario_refs)):
                raise RuntimeError("Edit Packet required UI Scenarios contain duplicates")
            cursor.execute(
                """
                SELECT scenario_id, scenario_version_id FROM verification_scenarios
                WHERE project_id = %s AND scenario_id = ANY(%s)
                  AND is_active AND review_status = 'approved'
                FOR SHARE
                """,
                (write.project_id, list(scenario_refs)),
            )
            version_by_scenario = {str(row[0]): str(row[1]) for row in cursor.fetchall()}
            missing = sorted(set(scenario_refs) - set(version_by_scenario))
            if missing:
                raise ValueError(f"Required UI Scenarios are not active and approved: {missing}")
            versions = tuple(
                (scenario_id, version_by_scenario[scenario_id]) for scenario_id in scenario_refs
            )
            cursor.execute(
                """
                INSERT INTO ui_execution_plans (
                    ui_execution_plan_id, project_id, analysis_case_id,
                    edit_packet_id, edit_result_id, environment_id,
                    deployment_revision, repository_revision, status,
                    scenario_refs, blocking_reasons, repository_binding_status
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, 'preflight_pending',
                    %s::jsonb, '[]'::jsonb, 'verified'
                )
                """,
                (
                    write.plan_id,
                    write.project_id,
                    write.analysis_case_id,
                    write.edit_packet_id,
                    write.edit_result_id,
                    write.environment_id,
                    write.deployment_revision,
                    source[4],
                    _json(list(scenario_refs)),
                ),
            )
            for order, (scenario_id, version_id) in enumerate(versions, start=1):
                cursor.execute(
                    """
                    INSERT INTO ui_execution_plan_scenarios (
                        ui_execution_plan_id, project_id, scenario_id,
                        scenario_version_id, execution_order
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (write.plan_id, write.project_id, scenario_id, version_id, order),
                )
        return UiExecutionPlanRecord(True, write.plan_id, "preflight_pending", scenario_refs, ())

    def record_preflight(
        self,
        *,
        project_id: str,
        plan_id: str,
        attempt_id: str,
        checks: tuple[UiPreflightCheckWrite, ...],
    ) -> UiExecutionPlanRecord:
        if not attempt_id.strip():
            raise ValueError("Preflight Attempt ID must not be blank")
        if {check.check_type for check in checks} != PREFLIGHT_TYPES or len(checks) != 5:
            raise ValueError("Preflight must contain each required check exactly once")
        for check in checks:
            if check.status not in {"passed", "failed", "blocked"}:
                raise ValueError("Invalid Preflight check status")
            if check.status != "passed" and (check.reason is None or not check.reason.strip()):
                raise ValueError("Failed or blocked Preflight check requires a reason")
        blocking = tuple(
            sorted(
                f"{check.check_type}:{check.status}:{check.reason}"
                for check in checks
                if check.status != "passed"
            )
        )
        attempt_status = "blocked" if blocking else "passed"
        plan_status = "blocked" if blocking else "ready"
        with self._connection.transaction(), self._connection.cursor() as cursor:
            current = self._load_plan(cursor, plan_id, lock=True)
            if current is None:
                raise ValueError("UI Execution Plan does not accept Preflight")
            cursor.execute(
                """
                SELECT project_id, repository_binding_status
                FROM ui_execution_plans WHERE ui_execution_plan_id = %s
                """,
                (plan_id,),
            )
            plan_scope = cursor.fetchone()
            if plan_scope is None or str(plan_scope[0]) != project_id:
                raise ValueError("UI Execution Plan does not belong to requested project")
            if str(plan_scope[1]) != "verified":
                raise ValueError("UI Execution Plan has an invalid Repository binding")
            cursor.execute(
                """
                SELECT ui_execution_plan_id, project_id, status, blocking_reasons
                FROM ui_preflight_attempts WHERE ui_preflight_attempt_id = %s
                """,
                (attempt_id,),
            )
            existing = cursor.fetchone()
            if existing is not None:
                expected_attempt = (plan_id, project_id, attempt_status, list(blocking))
                if tuple(existing) != expected_attempt:
                    raise PersistenceConflictError(
                        f"Preflight Attempt has different content: {attempt_id}"
                    )
                cursor.execute(
                    """
                    SELECT ui_preflight_check_id, check_type, status, evidence_ref, reason
                    FROM ui_preflight_checks
                    WHERE ui_preflight_attempt_id = %s ORDER BY check_type
                    """,
                    (attempt_id,),
                )
                actual_checks = tuple(tuple(row) for row in cursor.fetchall())
                expected_checks = tuple(
                    (
                        check.check_id,
                        check.check_type,
                        check.status,
                        check.evidence_ref,
                        check.reason,
                    )
                    for check in sorted(checks, key=lambda value: value.check_type)
                )
                if actual_checks != expected_checks:
                    raise PersistenceConflictError(
                        f"Preflight Attempt checks have different content: {attempt_id}"
                    )
                return UiExecutionPlanRecord(False, plan_id, *current)
            _assert_plan_current(cursor, plan_id=plan_id, project_id=project_id)
            if current[0] not in {"preflight_pending", "blocked"}:
                raise ValueError("UI Execution Plan does not accept a new Preflight Attempt")
            cursor.execute(
                """
                INSERT INTO ui_preflight_attempts (
                    ui_preflight_attempt_id, ui_execution_plan_id, project_id,
                    status, blocking_reasons
                ) VALUES (%s, %s, %s, %s, %s::jsonb)
                """,
                (attempt_id, plan_id, project_id, attempt_status, _json(list(blocking))),
            )
            for check in sorted(checks, key=lambda value: value.check_type):
                cursor.execute(
                    """
                    INSERT INTO ui_preflight_checks (
                        ui_preflight_check_id, ui_execution_plan_id, project_id,
                        ui_preflight_attempt_id, check_type, status, evidence_ref, reason
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        check.check_id,
                        plan_id,
                        project_id,
                        attempt_id,
                        check.check_type,
                        check.status,
                        check.evidence_ref,
                        check.reason,
                    ),
                )
            cursor.execute(
                """
                UPDATE ui_execution_plans SET status = %s, blocking_reasons = %s::jsonb
                WHERE ui_execution_plan_id = %s AND project_id = %s
                """,
                (plan_status, _json(list(blocking)), plan_id, project_id),
            )
            plan = self._load_plan(cursor, plan_id)
            if plan is None:
                raise RuntimeError("UI Execution Plan disappeared")
        return UiExecutionPlanRecord(False, plan_id, *plan)

    def start_run(
        self,
        *,
        project_id: str,
        plan_id: str,
        run_id: str,
        approval_grant_id: str,
    ) -> UiExecutionRunRecord:
        if any(not value.strip() for value in (project_id, plan_id, run_id)):
            raise ValueError("UI Execution Run fields must not be blank")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT ui_execution_plan_id, project_id, approval_grant_id, status
                FROM ui_execution_runs WHERE ui_execution_run_id = %s
                """,
                (run_id,),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if tuple(existing[:3]) != (plan_id, project_id, approval_grant_id):
                    raise PersistenceConflictError(
                        f"UI Execution Run has different scope: {run_id}"
                    )
                _assert_plan_static(
                    cursor,
                    plan_id=plan_id,
                    project_id=project_id,
                    approval_grant_id=approval_grant_id,
                )
                return UiExecutionRunRecord(False, run_id, str(existing[3]))
            cursor.execute(
                """
                SELECT status, repository_binding_status FROM ui_execution_plans
                WHERE ui_execution_plan_id = %s AND project_id = %s FOR UPDATE
                """,
                (plan_id, project_id),
            )
            plan = cursor.fetchone()
            if plan is None or tuple(plan) != ("ready", "verified"):
                raise ValueError("UI Execution Run requires a ready verified Plan")
            _assert_plan_current(
                cursor,
                plan_id=plan_id,
                project_id=project_id,
                approval_grant_id=approval_grant_id,
                lock_sources=True,
            )
            cursor.execute(
                """
                SELECT 1 FROM ui_execution_runs
                WHERE ui_execution_plan_id = %s AND project_id = %s AND status = 'running'
                """,
                (plan_id, project_id),
            )
            if cursor.fetchone() is not None:
                raise ValueError("UI Execution Plan already has a running Run")
            cursor.execute(
                """
                INSERT INTO ui_execution_runs (
                    ui_execution_run_id, ui_execution_plan_id, project_id,
                    approval_grant_id, status
                ) VALUES (%s, %s, %s, %s, 'running')
                """,
                (run_id, plan_id, project_id, approval_grant_id),
            )
        return UiExecutionRunRecord(True, run_id, "running")

    def find_run(
        self,
        *,
        project_id: str,
        plan_id: str,
        run_id: str,
        approval_grant_id: str,
    ) -> UiExecutionRunRecord | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT ui_execution_plan_id, project_id, approval_grant_id, status
                FROM ui_execution_runs WHERE ui_execution_run_id = %s
                """,
                (run_id,),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if tuple(existing[:3]) != (plan_id, project_id, approval_grant_id):
                    raise PersistenceConflictError(
                        f"UI Execution Run has different scope: {run_id}"
                    )
                _assert_plan_static(
                    cursor,
                    plan_id=plan_id,
                    project_id=project_id,
                    approval_grant_id=approval_grant_id,
                )
        if existing is None:
            return None
        return UiExecutionRunRecord(False, run_id, str(existing[3]))

    def assert_run_recoverable(
        self,
        *,
        project_id: str,
        run_id: str,
        stale_before: datetime,
    ) -> None:
        """Reject recovery unless a Run is still running and older than a fixed boundary."""

        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status, started_at <= %s, %s <= clock_timestamp()
                FROM ui_execution_runs
                WHERE ui_execution_run_id = %s AND project_id = %s
                """,
                (stale_before, stale_before, run_id, project_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise ValueError("UI Execution Run does not exist in requested project")
        if str(row[0]) != "running":
            raise ValueError("Only a running UI Execution Run can be recovered")
        if not bool(row[1]) or not bool(row[2]):
            raise ValueError("UI Execution Run is newer than the recovery boundary")

    def load_run_scope(self, *, project_id: str, run_id: str) -> UiVerificationScope:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT plan.analysis_case_id, plan.edit_packet_id,
                       plan.ui_execution_plan_id, plan.repository_revision,
                       plan.deployment_revision, plan.environment_id,
                       plan.status, run.status, plan.scenario_refs, packet.allowed_items,
                       run.approval_grant_id, plan.repository_binding_status,
                       packet.test_files
                FROM ui_execution_runs AS run
                JOIN ui_execution_plans AS plan
                  ON plan.ui_execution_plan_id = run.ui_execution_plan_id
                 AND plan.project_id = run.project_id
                JOIN edit_packets AS packet
                  ON packet.edit_packet_id = plan.edit_packet_id
                 AND packet.project_id = plan.project_id
                WHERE run.ui_execution_run_id = %s AND run.project_id = %s
                """,
                (run_id, project_id),
            )
            row = cursor.fetchone()
            if row is not None:
                cursor.execute(
                    """
                    SELECT plan_scenario.scenario_id, scenario.evidence_requirements
                    FROM ui_execution_plan_scenarios AS plan_scenario
                    JOIN verification_scenarios AS scenario
                      ON scenario.scenario_version_id = plan_scenario.scenario_version_id
                     AND scenario.project_id = plan_scenario.project_id
                    WHERE plan_scenario.ui_execution_plan_id = %s
                      AND plan_scenario.project_id = %s
                    ORDER BY plan_scenario.execution_order
                    """,
                    (str(row[2]), project_id),
                )
                requirement_rows = cursor.fetchall()
                _assert_plan_static(
                    cursor,
                    plan_id=str(row[2]),
                    project_id=project_id,
                    approval_grant_id=str(row[10]),
                )
        if row is None:
            raise ValueError("UI Execution Run does not exist in requested project")
        if str(row[11]) != "verified":
            raise ValueError("UI Execution Run Plan has an invalid Repository binding")
        scenario_refs = tuple(str(value) for value in cast(list[object], row[8]))
        scenario_evidence_requirements = tuple(
            (
                str(requirement_row[0]),
                tuple(str(value) for value in cast(list[object], requirement_row[1])),
            )
            for requirement_row in requirement_rows
        )
        requirement_ids = tuple(value[0] for value in scenario_evidence_requirements)
        if requirement_ids != scenario_refs or len(requirement_ids) != len(set(requirement_ids)):
            raise RuntimeError("UI Plan Scenario evidence requirements are not normalized")
        if any(
            not requirements
            or len(requirements) != len(set(requirements))
            or not set(requirements).issubset(UI_EVIDENCE_TYPES)
            for _, requirements in scenario_evidence_requirements
        ):
            raise RuntimeError("UI Plan Scenario has unsupported Evidence requirements")
        allowed_items = cast(list[object], row[9])
        test_files = {str(value) for value in cast(list[object], row[12])}
        normalized_items = [
            cast(dict[str, object], raw) for raw in allowed_items if isinstance(raw, dict)
        ]
        impact_item_ids = tuple(
            sorted(
                str(item["impact_item_id"])
                for item in normalized_items
                if isinstance(item.get("impact_item_id"), str)
                and isinstance(item.get("target_path"), str)
                and str(item["target_path"]) not in test_files
            )
        )
        all_item_ids = tuple(
            str(item["impact_item_id"])
            for item in normalized_items
            if isinstance(item.get("impact_item_id"), str)
            and isinstance(item.get("target_path"), str)
        )
        if len(all_item_ids) != len(allowed_items):
            raise RuntimeError("Edit Packet allowed_items are not normalized")
        if len(all_item_ids) != len(set(all_item_ids)):
            raise RuntimeError("Edit Packet allowed_items contain duplicate Impact IDs")
        return UiVerificationScope(
            project_id=project_id,
            analysis_case_id=str(row[0]),
            edit_packet_id=str(row[1]),
            approval_grant_id=str(row[10]),
            plan_id=str(row[2]),
            run_id=run_id,
            repository_revision=str(row[3]),
            deployment_revision=str(row[4]),
            environment_id=str(row[5]),
            plan_status=str(row[6]),
            run_status=str(row[7]),
            scenario_refs=scenario_refs,
            scenario_evidence_requirements=scenario_evidence_requirements,
            impact_item_ids=impact_item_ids,
        )

    def complete_run(
        self,
        *,
        scope: UiVerificationScope,
        write: UiVerificationCompletionWrite,
    ) -> UiVerificationResultRecord:
        case_status = _case_status(write.status)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT project_id, analysis_case_id, ui_execution_plan_id,
                       ui_execution_run_id, status, unresolved_impact_item_ids,
                       out_of_scope_files, failure_reasons
                FROM change_validations WHERE verification_result_id = %s
                """,
                (write.verification_result_id,),
            )
            existing = cursor.fetchone()
            if existing is not None:
                expected = (
                    scope.project_id,
                    scope.analysis_case_id,
                    scope.plan_id,
                    scope.run_id,
                    write.status,
                    list(write.unresolved_impact_item_ids),
                    list(write.out_of_scope_files),
                    list(write.failure_reasons),
                )
                if tuple(existing) != expected or not _completion_details_match(
                    cursor=cursor,
                    scope=scope,
                    write=write,
                ):
                    raise PersistenceConflictError(
                        "UI Verification Result has different normalized content: "
                        f"{write.verification_result_id}"
                    )
                return UiVerificationResultRecord(
                    False, write.verification_result_id, write.status, case_status
                )
            cursor.execute(
                """
                SELECT run.status, plan.status, plan.scenario_refs,
                       plan.repository_binding_status
                FROM ui_execution_runs AS run
                JOIN ui_execution_plans AS plan
                  ON plan.ui_execution_plan_id = run.ui_execution_plan_id
                 AND plan.project_id = run.project_id
                WHERE run.ui_execution_run_id = %s AND run.project_id = %s
                  AND plan.ui_execution_plan_id = %s
                FOR UPDATE OF run, plan
                """,
                (scope.run_id, scope.project_id, scope.plan_id),
            )
            current = cursor.fetchone()
            if current is None or tuple(current[:2]) != ("running", "ready"):
                raise ValueError("UI Verification completion requires a running ready Plan")
            if str(current[3]) != "verified":
                raise ValueError("UI Verification Plan has an invalid Repository binding")
            if tuple(str(value) for value in cast(list[object], current[2])) != scope.scenario_refs:
                raise ValueError("UI Execution Plan scenarios changed during Run")
            if write.status != "blocked":
                _assert_plan_current(
                    cursor,
                    plan_id=scope.plan_id,
                    project_id=scope.project_id,
                    approval_grant_id=scope.approval_grant_id,
                    lock_sources=True,
                )
            for evidence in write.evidence:
                cursor.execute(
                    """
                    INSERT INTO ui_execution_evidence (
                        evidence_id, ui_execution_run_id, project_id, scenario_id,
                        evidence_type, evidence_ref, content_digest, sanitized
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        evidence.evidence_id,
                        scope.run_id,
                        scope.project_id,
                        evidence.scenario_id,
                        evidence.evidence_type,
                        evidence.evidence_ref,
                        evidence.content_digest,
                        evidence.sanitized,
                    ),
                )
            for result in write.scenario_results:
                cursor.execute(
                    """
                    INSERT INTO ui_scenario_results (
                        ui_execution_run_id, project_id, scenario_id, status,
                        impact_item_refs, evidence_refs, failure_category, summary
                    ) VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
                    """,
                    (
                        scope.run_id,
                        scope.project_id,
                        result.scenario_id,
                        result.status,
                        _json(list(result.impact_item_refs)),
                        _json(list(result.evidence_refs)),
                        result.failure_category,
                        result.summary,
                    ),
                )
            cursor.execute(
                """
                INSERT INTO change_validations (
                    verification_result_id, project_id, analysis_case_id,
                    ui_execution_plan_id, ui_execution_run_id, status,
                    unresolved_impact_item_ids, out_of_scope_files, failure_reasons
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb
                )
                """,
                (
                    write.verification_result_id,
                    scope.project_id,
                    scope.analysis_case_id,
                    scope.plan_id,
                    scope.run_id,
                    write.status,
                    _json(list(write.unresolved_impact_item_ids)),
                    _json(list(write.out_of_scope_files)),
                    _json(list(write.failure_reasons)),
                ),
            )
            run_status = {
                "passed": "completed",
                "failed": "failed",
                "blocked": "blocked",
                "reanalysis_required": "failed",
            }[write.status]
            cursor.execute(
                """
                UPDATE ui_execution_runs
                SET status = %s, completed_at = clock_timestamp()
                WHERE ui_execution_run_id = %s AND project_id = %s
                """,
                (run_status, scope.run_id, scope.project_id),
            )
            if write.status != "blocked":
                cursor.execute(
                    """
                    UPDATE ui_execution_plans SET status = 'completed'
                    WHERE ui_execution_plan_id = %s AND project_id = %s
                    """,
                    (scope.plan_id, scope.project_id),
                )
            cursor.execute(
                """
                UPDATE analysis_cases SET status = %s, updated_at = now()
                WHERE analysis_case_id = %s AND project_id = %s
                """,
                (case_status, scope.analysis_case_id, scope.project_id),
            )
        return UiVerificationResultRecord(
            True, write.verification_result_id, write.status, case_status
        )

    @staticmethod
    def _load_plan(
        cursor: Cursor[Any], plan_id: str, *, lock: bool = False
    ) -> tuple[str, tuple[str, ...], tuple[str, ...]] | None:
        locking = "FOR UPDATE" if lock else ""
        cursor.execute(
            f"""
            SELECT status, scenario_refs, blocking_reasons FROM ui_execution_plans
            WHERE ui_execution_plan_id = %s
            {locking}
            """,
            (plan_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return (
            str(row[0]),
            tuple(str(value) for value in cast(list[object], row[1])),
            tuple(str(value) for value in cast(list[object], row[2])),
        )


def _assert_plan_current(
    cursor: Cursor[Any],
    *,
    plan_id: str,
    project_id: str,
    approval_grant_id: str | None = None,
    lock_sources: bool = False,
) -> None:
    locking = (
        "FOR SHARE OF result, packet, environment, deployment, analysis_case"
        if lock_sources
        else ""
    )
    cursor.execute(
        f"""
        SELECT plan.scenario_refs, packet.required_ui_scenario_refs,
               result.status, result.validation_mode, result.tests_passed,
               result.command_evidence_status, result.result_repository_revision,
               plan.repository_revision, deployment.repository_revision,
               deployment.status, environment.status, analysis_case.status,
               result.approval_grant_id, result.edit_packet_id,
               result.analysis_case_id, plan.edit_packet_id,
               plan.analysis_case_id, plan.repository_binding_status
        FROM ui_execution_plans AS plan
        JOIN edit_results AS result
          ON result.edit_result_id = plan.edit_result_id
         AND result.project_id = plan.project_id
        JOIN edit_packets AS packet
          ON packet.edit_packet_id = plan.edit_packet_id
         AND packet.project_id = plan.project_id
        JOIN ui_environments AS environment
          ON environment.environment_id = plan.environment_id
         AND environment.project_id = plan.project_id
        JOIN ui_deployments AS deployment
          ON deployment.environment_id = plan.environment_id
         AND deployment.project_id = plan.project_id
         AND deployment.deployment_revision = plan.deployment_revision
        JOIN analysis_cases AS analysis_case
          ON analysis_case.analysis_case_id = plan.analysis_case_id
         AND analysis_case.project_id = plan.project_id
        WHERE plan.ui_execution_plan_id = %s AND plan.project_id = %s
        {locking}
        """,
        (plan_id, project_id),
    )
    source = cursor.fetchone()
    if source is None:
        raise ValueError("UI Execution Plan source no longer exists")
    scenario_refs = tuple(str(value) for value in cast(list[object], source[0]))
    packet_refs = tuple(str(value) for value in cast(list[object], source[1]))
    identity_is_current = (
        scenario_refs == packet_refs
        and len(scenario_refs) == len(set(scenario_refs))
        and tuple(source[2:6]) == ("in_scope", "committed", True, "verified")
        and str(source[6]) == str(source[7]) == str(source[8])
        and tuple(source[9:12]) == ("ready", "active", "verifying_ui")
        and (str(source[13]), str(source[14])) == (str(source[15]), str(source[16]))
        and str(source[17]) == "verified"
        and (approval_grant_id is None or str(source[12]) == approval_grant_id)
        and _plan_scenarios_match(
            cursor,
            plan_id=plan_id,
            project_id=project_id,
            scenario_refs=scenario_refs,
            require_approved=True,
        )
    )
    if not identity_is_current:
        raise ValueError("UI Execution Plan source is no longer current")


def _assert_plan_static(
    cursor: Cursor[Any],
    *,
    plan_id: str,
    project_id: str,
    approval_grant_id: str,
) -> None:
    cursor.execute(
        """
        SELECT plan.scenario_refs, packet.required_ui_scenario_refs,
               plan.repository_revision, result.result_repository_revision,
               deployment.repository_revision, result.edit_packet_id,
               plan.edit_packet_id, result.analysis_case_id,
               plan.analysis_case_id, result.approval_grant_id,
               plan.repository_binding_status
        FROM ui_execution_plans AS plan
        JOIN edit_results AS result
          ON result.edit_result_id = plan.edit_result_id
         AND result.project_id = plan.project_id
        JOIN edit_packets AS packet
          ON packet.edit_packet_id = plan.edit_packet_id
         AND packet.project_id = plan.project_id
        JOIN ui_deployments AS deployment
          ON deployment.environment_id = plan.environment_id
         AND deployment.project_id = plan.project_id
         AND deployment.deployment_revision = plan.deployment_revision
        WHERE plan.ui_execution_plan_id = %s AND plan.project_id = %s
        """,
        (plan_id, project_id),
    )
    source = cursor.fetchone()
    if source is None:
        raise PersistenceConflictError(f"UI Execution Plan normalized source is missing: {plan_id}")
    scenario_refs = tuple(str(value) for value in cast(list[object], source[0]))
    packet_refs = tuple(str(value) for value in cast(list[object], source[1]))
    if not (
        scenario_refs == packet_refs
        and len(scenario_refs) == len(set(scenario_refs))
        and str(source[2]) == str(source[3]) == str(source[4])
        and (str(source[5]), str(source[7])) == (str(source[6]), str(source[8]))
        and str(source[9]) == approval_grant_id
        and str(source[10]) == "verified"
        and _plan_scenarios_match(
            cursor,
            plan_id=plan_id,
            project_id=project_id,
            scenario_refs=scenario_refs,
            require_approved=True,
        )
    ):
        raise PersistenceConflictError(f"UI Execution Plan normalized identity differs: {plan_id}")


def _plan_scenarios_match(
    cursor: Cursor[Any],
    *,
    plan_id: str,
    project_id: str,
    scenario_refs: tuple[str, ...],
    require_approved: bool,
) -> bool:
    cursor.execute(
        """
        SELECT planned.scenario_id, scenario.review_status
        FROM ui_execution_plan_scenarios AS planned
        JOIN verification_scenarios AS scenario
          ON scenario.scenario_version_id = planned.scenario_version_id
         AND scenario.project_id = planned.project_id
        WHERE planned.ui_execution_plan_id = %s AND planned.project_id = %s
        ORDER BY planned.execution_order
        """,
        (plan_id, project_id),
    )
    rows = cursor.fetchall()
    actual_refs = tuple(str(row[0]) for row in rows)
    return actual_refs == scenario_refs and (
        not require_approved or all(str(row[1]) == "approved" for row in rows)
    )


def _validate_scenario(write: VerificationScenarioWrite) -> None:
    required = (
        write.scenario_version_id,
        write.project_id,
        write.scenario_id,
        write.scenario_version,
        write.title,
        write.trigger_path,
    )
    if any(not value.strip() for value in required):
        raise ValueError("Verification Scenario fields must not be blank")
    collections = (write.steps, write.expected_visible_results, write.evidence_requirements)
    if any(not values or any(not value.strip() for value in values) for values in collections):
        raise ValueError("Verification Scenario steps/results/evidence must not be empty")
    if len(write.evidence_requirements) != len(set(write.evidence_requirements)) or not set(
        write.evidence_requirements
    ).issubset(UI_EVIDENCE_TYPES):
        raise ValueError("Verification Scenario Evidence requirements are unsupported or duplicate")
    if write.review_status not in {"draft", "approved", "rejected"}:
        raise ValueError("Invalid Verification Scenario review status")
    if write.activate and write.review_status != "approved":
        raise ValueError("Only approved Verification Scenario may become active")
    if len(write.test_case_refs) != len(set(write.test_case_refs)) or any(
        not value.strip() for value in write.test_case_refs
    ):
        raise ValueError("Verification Scenario Test case refs must be unique and non-blank")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _case_status(verification_status: str) -> str:
    if verification_status == "passed":
        return "passed"
    if verification_status == "blocked":
        return "verifying_ui"
    if verification_status == "reanalysis_required":
        return "reanalysis_required"
    if verification_status == "failed":
        return "failed"
    raise ValueError(f"Invalid UI Verification status: {verification_status}")


def _completion_details_match(
    *,
    cursor: Cursor[Any],
    scope: UiVerificationScope,
    write: UiVerificationCompletionWrite,
) -> bool:
    cursor.execute(
        """
        SELECT evidence_id, scenario_id, evidence_type, evidence_ref,
               content_digest, sanitized
        FROM ui_execution_evidence
        WHERE ui_execution_run_id = %s AND project_id = %s
        ORDER BY evidence_id
        """,
        (scope.run_id, scope.project_id),
    )
    actual_evidence = tuple(tuple(row) for row in cursor.fetchall())
    expected_evidence = tuple(
        sorted(
            (
                evidence.evidence_id,
                evidence.scenario_id,
                evidence.evidence_type,
                evidence.evidence_ref,
                evidence.content_digest,
                evidence.sanitized,
            )
            for evidence in write.evidence
        )
    )
    cursor.execute(
        """
        SELECT scenario_id, status, impact_item_refs, evidence_refs,
               failure_category, summary
        FROM ui_scenario_results
        WHERE ui_execution_run_id = %s AND project_id = %s
        ORDER BY scenario_id
        """,
        (scope.run_id, scope.project_id),
    )
    actual_results = tuple(tuple(row) for row in cursor.fetchall())
    expected_results = tuple(
        sorted(
            (
                result.scenario_id,
                result.status,
                list(result.impact_item_refs),
                list(result.evidence_refs),
                result.failure_category,
                result.summary,
            )
            for result in write.scenario_results
        )
    )
    return actual_evidence == expected_evidence and actual_results == expected_results
