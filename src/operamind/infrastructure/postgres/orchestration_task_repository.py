"""PostgreSQL claim/lease/result ledger for agent-neutral orchestration tasks."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from typing import Any

from psycopg import Connection, Cursor
from psycopg.rows import dict_row

from operamind.application.orchestration_task import (
    OrchestrationSchedulingPolicy,
    OrchestrationTaskDefinition,
    validate_orchestration_result_evidence,
)
from operamind.infrastructure.postgres.errors import PersistenceConflictError

TERMINAL_STATES = {"completed", "failed", "blocked", "cancelled", "superseded"}
TASK_STATES = {
    "ready",
    "claimed",
    "running",
    "submitted",
    "completed",
    "failed",
    "blocked",
    "cancelled",
    "superseded",
}


class OrchestrationTaskRepository:
    """Keep task definitions executor-neutral and assignments short-lived."""

    def __init__(
        self,
        connection: Connection[Any],
        scheduling_policy: OrchestrationSchedulingPolicy | None = None,
    ) -> None:
        self._connection = connection
        self._scheduling_policy = scheduling_policy or OrchestrationSchedulingPolicy()

    def register_worker(
        self,
        *,
        executor_kind: str,
        executor_id: str,
        capabilities: tuple[str, ...],
        project_id: str | None,
        max_concurrent_tasks: int = 1,
        lease_seconds: int = 30,
    ) -> dict[str, object]:
        """Register the deploy-time worker identity used by automatic claims."""
        _validate_executor(executor_kind, executor_id)
        if executor_kind == "human":
            raise ValueError("human executors are not registered as Workers")
        _validate_capabilities(capabilities)
        if project_id is not None and (not project_id.strip() or len(project_id) > 160):
            raise ValueError("worker project_id must be non-blank and bounded")
        if not 1 <= max_concurrent_tasks <= 100:
            raise ValueError("max_concurrent_tasks must be between 1 and 100")
        if not 10 <= lease_seconds <= 86400:
            raise ValueError("worker lease_seconds must be between 10 and 86400")
        worker_token = secrets.token_urlsafe(32)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO orchestration_worker_registrations (
                    executor_kind, executor_id, capabilities, project_id,
                    max_concurrent_tasks, status, lease_expires_at, credential_digest
                ) VALUES (
                    %s, %s, %s::jsonb, %s, %s, 'online',
                    now() + make_interval(secs => %s), %s
                )
                ON CONFLICT (executor_kind, executor_id) DO UPDATE SET
                    capabilities = EXCLUDED.capabilities,
                    project_id = EXCLUDED.project_id,
                    max_concurrent_tasks = EXCLUDED.max_concurrent_tasks,
                    status = 'online',
                    credential_digest = EXCLUDED.credential_digest,
                    last_seen_at = now(),
                    lease_expires_at = now() + make_interval(secs => %s),
                    updated_at = now()
                """,
                (
                    executor_kind,
                    executor_id,
                    _json(capabilities),
                    project_id,
                    max_concurrent_tasks,
                    lease_seconds,
                    _digest(worker_token),
                    lease_seconds,
                ),
            )
            self._append_worker_event(
                cursor,
                executor_kind=executor_kind,
                executor_id=executor_id,
                event_type="registered",
                actor=executor_id,
                payload={
                    "capabilities": list(capabilities),
                    "project_id": project_id,
                    "max_concurrent_tasks": max_concurrent_tasks,
                },
            )
        result = self.worker_registration(executor_kind=executor_kind, executor_id=executor_id)
        result["worker_token"] = worker_token
        return result

    def heartbeat_worker(
        self,
        *,
        executor_kind: str,
        executor_id: str,
        worker_token: str,
        lease_seconds: int = 30,
    ) -> dict[str, object]:
        """Refresh presence without overriding an operator-managed status."""
        _validate_executor(executor_kind, executor_id)
        _validate_worker_token(worker_token)
        if not 10 <= lease_seconds <= 86400:
            raise ValueError("worker lease_seconds must be between 10 and 86400")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE orchestration_worker_registrations
                SET last_seen_at = now(),
                    lease_expires_at = now() + make_interval(secs => %s),
                    updated_at = now()
                WHERE executor_kind = %s AND executor_id = %s
                  AND credential_digest = %s
                """,
                (lease_seconds, executor_kind, executor_id, _digest(worker_token)),
            )
            if cursor.rowcount != 1:
                raise ValueError("Orchestration Worker credential is invalid")
        return self.worker_registration(executor_kind=executor_kind, executor_id=executor_id)

    def unregister_worker(
        self, *, executor_kind: str, executor_id: str, worker_token: str
    ) -> dict[str, object]:
        """Mark a Worker offline while retaining its auditable registration."""
        _validate_executor(executor_kind, executor_id)
        _validate_worker_token(worker_token)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE orchestration_worker_registrations
                SET status = 'offline', last_seen_at = now(),
                    lease_expires_at = now() + interval '1 microsecond', updated_at = now()
                WHERE executor_kind = %s AND executor_id = %s
                  AND credential_digest = %s
                """,
                (executor_kind, executor_id, _digest(worker_token)),
            )
            if cursor.rowcount != 1:
                raise ValueError("Orchestration Worker credential is invalid")
            self._append_worker_event(
                cursor,
                executor_kind=executor_kind,
                executor_id=executor_id,
                event_type="disabled",
                actor=executor_id,
                payload={"reason": "worker_process_stopped"},
            )
        return self.worker_registration(executor_kind=executor_kind, executor_id=executor_id)

    def set_worker_status(
        self,
        *,
        executor_kind: str,
        executor_id: str,
        status: str,
        actor: str,
    ) -> dict[str, object]:
        """Apply an operator command without pretending to control the process itself."""
        _validate_executor(executor_kind, executor_id)
        _validate_actor(actor)
        if status not in {"online", "draining", "offline"}:
            raise ValueError("Worker status must be online, draining, or offline")
        event_type = {
            "online": "enabled",
            "draining": "drain_requested",
            "offline": "disabled",
        }[status]
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE orchestration_worker_registrations
                SET status = %s, updated_at = now()
                WHERE executor_kind = %s AND executor_id = %s
                RETURNING status
                """,
                (status, executor_kind, executor_id),
            )
            if cursor.fetchone() is None:
                raise ValueError("Orchestration Worker registration does not exist")
            self._append_worker_event(
                cursor,
                executor_kind=executor_kind,
                executor_id=executor_id,
                event_type=event_type,
                actor=actor,
                payload={"status": status},
            )
        return self.worker_registration(executor_kind=executor_kind, executor_id=executor_id)

    def update_worker_configuration(
        self,
        *,
        executor_kind: str,
        executor_id: str,
        capabilities: tuple[str, ...],
        max_concurrent_tasks: int,
        actor: str,
    ) -> dict[str, object]:
        """Change scheduling inputs without mutating active Claim snapshots."""
        _validate_executor(executor_kind, executor_id)
        _validate_capabilities(capabilities)
        _validate_actor(actor)
        if not 1 <= max_concurrent_tasks <= 100:
            raise ValueError("max_concurrent_tasks must be between 1 and 100")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE orchestration_worker_registrations
                SET capabilities = %s::jsonb, max_concurrent_tasks = %s,
                    updated_at = now()
                WHERE executor_kind = %s AND executor_id = %s
                """,
                (
                    _json(capabilities),
                    max_concurrent_tasks,
                    executor_kind,
                    executor_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Orchestration Worker registration does not exist")
            self._append_worker_event(
                cursor,
                executor_kind=executor_kind,
                executor_id=executor_id,
                event_type="configuration_updated",
                actor=actor,
                payload={
                    "capabilities": list(capabilities),
                    "max_concurrent_tasks": max_concurrent_tasks,
                },
            )
        return self.worker_registration(executor_kind=executor_kind, executor_id=executor_id)

    def worker_registration(self, *, executor_kind: str, executor_id: str) -> dict[str, object]:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT registration.*,
                       (registration.lease_expires_at > now()) AS present,
                       (registration.status = 'online'
                        AND registration.lease_expires_at > now()) AS live,
                       (
                           SELECT count(*)
                           FROM orchestration_task_claims AS claim
                           WHERE claim.executor_kind = registration.executor_kind
                             AND claim.executor_id = registration.executor_id
                             AND claim.status = 'active'
                             AND claim.lease_expires_at > now()
                       ) AS active_task_count
                FROM orchestration_worker_registrations AS registration
                WHERE executor_kind = %s AND executor_id = %s
                """,
                (executor_kind, executor_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise ValueError("Orchestration Worker registration does not exist")
        result = _worker_view(row)
        result["events"] = self.worker_events(executor_kind=executor_kind, executor_id=executor_id)
        return result

    def list_worker_registrations(
        self, *, project_id: str | None = None
    ) -> list[dict[str, object]]:
        if project_id is not None and (not project_id.strip() or len(project_id) > 160):
            raise ValueError("project_id must be non-blank and bounded")
        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT registration.*,
                       (registration.lease_expires_at > now()) AS present,
                       (registration.status = 'online'
                        AND registration.lease_expires_at > now()) AS live,
                       (
                           SELECT count(*)
                           FROM orchestration_task_claims AS claim
                           WHERE claim.executor_kind = registration.executor_kind
                             AND claim.executor_id = registration.executor_id
                             AND claim.status = 'active'
                             AND claim.lease_expires_at > now()
                       ) AS active_task_count
                FROM orchestration_worker_registrations AS registration
                WHERE (%s::text IS NULL OR registration.project_id IS NULL
                       OR registration.project_id = %s)
                ORDER BY live DESC, registration.executor_kind, registration.executor_id
                """,
                (project_id, project_id),
            )
            rows = cursor.fetchall()
        workers = [_worker_view(row) for row in rows]
        for worker in workers:
            worker["events"] = self.worker_events(
                executor_kind=str(worker["executor_kind"]),
                executor_id=str(worker["executor_id"]),
            )
        return workers

    def worker_events(
        self, *, executor_kind: str, executor_id: str, limit: int = 20
    ) -> list[dict[str, object]]:
        if not 1 <= limit <= 100:
            raise ValueError("Worker event limit must be between 1 and 100")
        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT sequence, event_type, actor, payload, created_at
                FROM orchestration_worker_events
                WHERE executor_kind = %s AND executor_id = %s
                ORDER BY sequence DESC
                LIMIT %s
                """,
                (executor_kind, executor_id, limit),
            )
            rows = cursor.fetchall()
        return [
            {
                "sequence": int(row["sequence"]),
                "event_type": str(row["event_type"]),
                "actor": str(row["actor"]),
                "payload": dict(row["payload"]),
                "created_at": _time(row["created_at"]),
            }
            for row in rows
        ]

    def runtime_monitoring(
        self,
        *,
        project_id: str | None = None,
        window_hours: int = 24,
        backlog_alert_threshold: int = 20,
        queue_wait_alert_seconds: int = 300,
    ) -> dict[str, object]:
        """Aggregate operational Task metrics for the Japanese monitoring panel."""
        if project_id is not None and (not project_id.strip() or len(project_id) > 160):
            raise ValueError("project_id must be non-blank and bounded")
        if not 1 <= window_hours <= 2160:
            raise ValueError("window_hours must be between 1 and 2160")
        if not 1 <= backlog_alert_threshold <= 100000:
            raise ValueError("backlog_alert_threshold must be between 1 and 100000")
        if not 30 <= queue_wait_alert_seconds <= 86400:
            raise ValueError("queue_wait_alert_seconds must be between 30 and 86400")
        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                WITH scoped_tasks AS (
                    SELECT * FROM orchestration_tasks
                    WHERE (%s::text IS NULL OR project_id = %s)
                ),
                task_stats AS (
                    SELECT count(*) AS task_count,
                           count(*) FILTER (
                               WHERE state = 'ready'
                                  OR (state IN ('claimed', 'running') AND EXISTS (
                                      SELECT 1 FROM orchestration_task_claims AS overdue
                                      WHERE overdue.orchestration_task_id =
                                            scoped_tasks.orchestration_task_id
                                        AND overdue.status = 'active'
                                        AND overdue.lease_expires_at <= now()
                                  ))
                           ) AS ready_count,
                           COALESCE(
                               max(EXTRACT(epoch FROM (now() - created_at))) FILTER (
                                   WHERE state = 'ready'
                                      OR (state IN ('claimed', 'running') AND EXISTS (
                                          SELECT 1 FROM orchestration_task_claims AS overdue
                                          WHERE overdue.orchestration_task_id =
                                                scoped_tasks.orchestration_task_id
                                            AND overdue.status = 'active'
                                            AND overdue.lease_expires_at <= now()
                                      ))
                               ),
                               0
                           ) AS oldest_ready_wait_seconds,
                           count(*) FILTER (
                               WHERE state IN ('claimed', 'running') AND EXISTS (
                                   SELECT 1 FROM orchestration_task_claims AS live
                                   WHERE live.orchestration_task_id =
                                         scoped_tasks.orchestration_task_id
                                     AND live.status = 'active'
                                     AND live.lease_expires_at > now()
                               )
                           ) AS active_task_count,
                           COALESCE(sum(GREATEST(attempt_count - 1, 0)), 0)
                               AS retry_count,
                           count(*) FILTER (WHERE attempt_count > 1) AS retried_task_count
                    FROM scoped_tasks
                ),
                claim_stats AS (
                    SELECT count(*) AS claim_count,
                           avg(EXTRACT(epoch FROM (claim.claimed_at - task.created_at)))
                               AS average_queue_wait_seconds,
                           percentile_cont(0.95) WITHIN GROUP (
                               ORDER BY EXTRACT(epoch FROM (
                                   claim.claimed_at - task.created_at
                               ))
                           ) AS p95_queue_wait_seconds,
                           avg(EXTRACT(epoch FROM (
                               COALESCE(claim.released_at, now()) - claim.claimed_at
                           ))) AS average_execution_seconds,
                           count(*) FILTER (
                               WHERE claim.status = 'expired'
                                  OR (claim.status = 'active'
                                      AND claim.lease_expires_at <= now())
                           ) AS lease_expiry_count
                    FROM orchestration_task_claims AS claim
                    JOIN scoped_tasks AS task
                      ON task.orchestration_task_id = claim.orchestration_task_id
                    WHERE claim.claimed_at >= now() - make_interval(hours => %s)
                ),
                result_stats AS (
                    SELECT count(*) AS result_count,
                           count(*) FILTER (WHERE result.outcome = 'completed')
                               AS success_count
                    FROM orchestration_task_results AS result
                    JOIN scoped_tasks AS task
                      ON task.orchestration_task_id = result.orchestration_task_id
                    WHERE result.submitted_at >= now() - make_interval(hours => %s)
                )
                SELECT task_stats.*, claim_stats.*, result_stats.*
                FROM task_stats CROSS JOIN claim_stats CROSS JOIN result_stats
                """,
                (project_id, project_id, window_hours, window_hours),
            )
            metrics = cursor.fetchone()
            cursor.execute(
                """
                SELECT COALESCE(NULLIF(result.evidence->>'blocking_reason', ''),
                                result.summary) AS reason,
                       count(*) AS occurrence_count,
                       max(result.submitted_at) AS latest_at
                FROM orchestration_task_results AS result
                JOIN orchestration_tasks AS task
                  ON task.orchestration_task_id = result.orchestration_task_id
                WHERE result.outcome IN ('blocked', 'failed')
                  AND result.submitted_at >= now() - make_interval(hours => %s)
                  AND (%s::text IS NULL OR task.project_id = %s)
                GROUP BY reason
                ORDER BY occurrence_count DESC, latest_at DESC, reason
                LIMIT 10
                """,
                (window_hours, project_id, project_id),
            )
            blocker_rows = cursor.fetchall()
        if metrics is None:
            raise RuntimeError("Orchestration Task monitoring query returned no row")
        result_count = int(metrics["result_count"])
        success_count = int(metrics["success_count"])
        ready_count = int(metrics["ready_count"])
        lease_expiry_count = int(metrics["lease_expiry_count"])
        oldest_ready_wait_seconds = _number(metrics["oldest_ready_wait_seconds"])
        p95_queue_wait_seconds = _number(metrics["p95_queue_wait_seconds"])
        alerts: list[dict[str, object]] = []
        if ready_count >= backlog_alert_threshold:
            alerts.append(
                {
                    "alert_type": "queue_backlog",
                    "severity": "warning",
                    "threshold": backlog_alert_threshold,
                    "value": ready_count,
                }
            )
        if lease_expiry_count:
            alerts.append(
                {
                    "alert_type": "task_timeout",
                    "severity": "critical",
                    "threshold": 0,
                    "value": lease_expiry_count,
                }
            )
        if (
            p95_queue_wait_seconds is not None
            and p95_queue_wait_seconds >= queue_wait_alert_seconds
        ):
            alerts.append(
                {
                    "alert_type": "queue_wait",
                    "severity": "warning",
                    "threshold": queue_wait_alert_seconds,
                    "value": p95_queue_wait_seconds,
                }
            )
        return {
            "window_hours": window_hours,
            "project_id": project_id,
            "task_count": int(metrics["task_count"]),
            "ready_count": ready_count,
            "oldest_ready_wait_seconds": oldest_ready_wait_seconds,
            "active_task_count": int(metrics["active_task_count"]),
            "claim_count": int(metrics["claim_count"]),
            "average_queue_wait_seconds": _number(metrics["average_queue_wait_seconds"]),
            "p95_queue_wait_seconds": p95_queue_wait_seconds,
            "average_execution_seconds": _number(metrics["average_execution_seconds"]),
            "success_count": success_count,
            "result_count": result_count,
            "success_rate": round(success_count / result_count, 4) if result_count else None,
            "retry_count": int(metrics["retry_count"]),
            "retried_task_count": int(metrics["retried_task_count"]),
            "lease_expiry_count": lease_expiry_count,
            "alerts": alerts,
            "blocker_reasons": [
                {
                    "reason": str(row["reason"]),
                    "occurrence_count": int(row["occurrence_count"]),
                    "latest_at": _time(row["latest_at"]),
                }
                for row in blocker_rows
            ],
            "workers": self.list_worker_registrations(project_id=project_id),
        }

    def ensure_current(
        self, *, definition: OrchestrationTaskDefinition, actor: str
    ) -> dict[str, object]:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT automation_run_id FROM change_automation_runs
                WHERE automation_run_id = %s FOR UPDATE
                """,
                (definition.automation_run_id,),
            )
            if cursor.fetchone() is None:
                raise ValueError("Change Automation Run does not exist")
            cursor.execute(
                """
                SELECT orchestration_task_id, step_key, state
                FROM orchestration_tasks
                WHERE automation_run_id = %s
                  AND orchestration_task_id <> %s
                ORDER BY sequence
                FOR UPDATE
                """,
                (definition.automation_run_id, definition.orchestration_task_id),
            )
            previous = cursor.fetchall()
            for task_id_value, step_key_value, state_value in previous:
                if str(state_value) not in {"ready", "claimed", "running", "submitted"}:
                    continue
                task_id = str(task_id_value)
                next_state = (
                    "completed" if str(step_key_value) != definition.step_key else "superseded"
                )
                if next_state == "completed":
                    self._complete_active_claim_from_canonical(
                        cursor,
                        task_id=task_id,
                        project_id=definition.project_id,
                        actor=actor,
                    )
                else:
                    self._close_active_claim(cursor, task_id, "canonical_state_superseded")
                cursor.execute(
                    """
                    UPDATE orchestration_tasks
                    SET state = %s, updated_at = now()
                    WHERE orchestration_task_id = %s
                    """,
                    (next_state, task_id),
                )
                self._append_event(
                    cursor,
                    task_id=task_id,
                    project_id=definition.project_id,
                    event_type=next_state,
                    actor=actor,
                    payload={"reason": "canonical_state_advanced"},
                )

            cursor.execute(
                """
                SELECT COALESCE(max(sequence), 0) + 1
                FROM orchestration_tasks WHERE automation_run_id = %s
                """,
                (definition.automation_run_id,),
            )
            sequence_row = cursor.fetchone()
            if sequence_row is None:
                raise RuntimeError("Orchestration Task sequence query returned no row")
            sequence = int(sequence_row[0])
            cursor.execute(
                """
                INSERT INTO orchestration_tasks (
                    orchestration_task_id, protocol_version,
                    automation_run_id, change_request_id,
                    project_id, sequence, step_key, action, title, instruction, task_kind,
                    required_capabilities, eligible_executor_kinds,
                    input_artifact_refs, expected_output_types, acceptance_criteria,
                    lease_seconds, max_attempts, definition_digest, priority, created_by
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                    %s, %s, %s, %s, %s
                )
                ON CONFLICT DO NOTHING
                """,
                (
                    definition.orchestration_task_id,
                    definition.protocol_version,
                    definition.automation_run_id,
                    definition.change_request_id,
                    definition.project_id,
                    sequence,
                    definition.step_key,
                    definition.action,
                    definition.title,
                    definition.instruction,
                    definition.task_kind,
                    _json(definition.required_capabilities),
                    _json(definition.eligible_executor_kinds),
                    _json(definition.input_artifact_refs),
                    _json(definition.expected_output_types),
                    _json(definition.acceptance_criteria),
                    definition.lease_seconds,
                    definition.max_attempts,
                    definition.definition_digest,
                    definition.priority,
                    actor,
                ),
            )
            created = cursor.rowcount == 1
            cursor.execute(
                """
                SELECT definition_digest, protocol_version, automation_run_id,
                       change_request_id, project_id
                FROM orchestration_tasks WHERE orchestration_task_id = %s
                """,
                (definition.orchestration_task_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("Orchestration Task disappeared during creation")
            if tuple(str(value) for value in row) != (
                definition.definition_digest,
                definition.protocol_version,
                definition.automation_run_id,
                definition.change_request_id,
                definition.project_id,
            ):
                raise PersistenceConflictError(
                    "Orchestration Task identity has different content: "
                    f"{definition.orchestration_task_id}"
                )
            if created:
                self._append_event(
                    cursor,
                    task_id=definition.orchestration_task_id,
                    project_id=definition.project_id,
                    event_type="created",
                    actor=actor,
                    payload={"state": "ready", "action": definition.action},
                )
                completed_previous = [
                    str(task_id)
                    for task_id, step_key, state in previous
                    if str(step_key) != definition.step_key
                    and str(state)
                    in {
                        "ready",
                        "claimed",
                        "running",
                        "submitted",
                        "completed",
                    }
                ]
                if completed_previous:
                    cursor.execute(
                        """
                        INSERT INTO orchestration_task_dependencies (
                            orchestration_task_id, depends_on_task_id, project_id
                        ) VALUES (%s, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            definition.orchestration_task_id,
                            completed_previous[-1],
                            definition.project_id,
                        ),
                    )
        view = self.view(definition.orchestration_task_id)
        view["created"] = created
        return view

    def list_for_run(self, automation_run_id: str) -> list[dict[str, object]]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT orchestration_task_id
                FROM orchestration_tasks
                WHERE automation_run_id = %s
                ORDER BY sequence
                """,
                (automation_run_id,),
            )
            task_ids = [str(row[0]) for row in cursor.fetchall()]
        return [self.view(task_id) for task_id in task_ids]

    def list_management(
        self,
        *,
        project_id: str | None = None,
        states: tuple[str, ...] = (),
        capability: str | None = None,
        blocking_reason: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, object]]:
        """Return a bounded, cross-run task queue for the human management UI."""
        if project_id is not None and not project_id.strip():
            raise ValueError("project_id must not be blank")
        if len(states) != len(set(states)) or any(state not in TASK_STATES for state in states):
            raise ValueError("Orchestration Task state filter is invalid")
        if capability is not None and (not capability.strip() or len(capability) > 200):
            raise ValueError("capability filter must be non-blank and bounded")
        if blocking_reason is not None and (
            not blocking_reason.strip() or len(blocking_reason) > 500
        ):
            raise ValueError("blocking_reason filter must be non-blank and bounded")
        if not 1 <= limit <= 500:
            raise ValueError("management limit must be between 1 and 500")
        reason_pattern = f"%{blocking_reason.strip()}%" if blocking_reason is not None else None
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                WITH task_projection AS (
                    SELECT task.*,
                           EXISTS (
                               SELECT 1 FROM orchestration_task_claims AS claim
                               WHERE claim.orchestration_task_id = task.orchestration_task_id
                                 AND claim.status = 'active'
                                 AND claim.lease_expires_at <= now()
                           ) AS lease_expired_projection
                    FROM orchestration_tasks AS task
                )
                SELECT task.orchestration_task_id
                FROM task_projection AS task
                WHERE (%s::text IS NULL OR task.project_id = %s)
                  AND (
                      %s::text[] IS NULL
                      OR CASE
                          WHEN task.lease_expired_projection
                               AND task.state IN ('claimed', 'running')
                          THEN CASE
                              WHEN task.attempt_count >= task.max_attempts THEN 'failed'
                              ELSE 'ready'
                          END
                          ELSE task.state
                      END = ANY(%s::text[])
                  )
                  AND (%s::text IS NULL OR task.required_capabilities ? %s)
                  AND (
                      %s::text IS NULL
                      OR EXISTS (
                          SELECT 1 FROM orchestration_task_results AS result
                          WHERE result.orchestration_task_id = task.orchestration_task_id
                            AND result.outcome IN ('blocked', 'failed')
                            AND (
                                result.summary ILIKE %s
                                OR COALESCE(result.evidence ->> 'blocking_reason', '') ILIKE %s
                            )
                      )
                      OR EXISTS (
                          SELECT 1 FROM orchestration_task_events AS event
                          WHERE event.orchestration_task_id = task.orchestration_task_id
                            AND COALESCE(event.payload ->> 'reason', '') ILIKE %s
                      )
                  )
                ORDER BY
                    CASE task.state
                        WHEN 'ready' THEN 0
                        WHEN 'claimed' THEN 1
                        WHEN 'running' THEN 2
                        WHEN 'submitted' THEN 3
                        WHEN 'blocked' THEN 4
                        WHEN 'failed' THEN 5
                        ELSE 6
                    END,
                    task.updated_at DESC,
                    task.automation_run_id,
                    task.sequence
                LIMIT %s
                """,
                (
                    project_id,
                    project_id,
                    list(states) if states else None,
                    list(states) if states else None,
                    capability,
                    capability,
                    reason_pattern,
                    reason_pattern,
                    reason_pattern,
                    reason_pattern,
                    limit,
                ),
            )
            task_ids = [str(row[0]) for row in cursor.fetchall()]
        return [self.view(task_id) for task_id in task_ids]

    def dependency_graph(
        self,
        *,
        project_id: str | None = None,
        automation_run_id: str | None = None,
        limit: int = 500,
    ) -> dict[str, object]:
        """Return a bounded graph projection without loading Claim/Result/Event histories."""
        if project_id is not None and not project_id.strip():
            raise ValueError("project_id must not be blank")
        if automation_run_id is not None and not automation_run_id.strip():
            raise ValueError("automation_run_id must not be blank")
        if not 1 <= limit <= 1000:
            raise ValueError("dependency graph limit must be between 1 and 1000")
        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                WITH eligible AS (
                    SELECT task.orchestration_task_id, task.automation_run_id,
                           task.change_request_id, task.project_id, task.sequence,
                           task.title, task.state, task.attempt_count,
                           task.max_attempts, task.updated_at,
                           EXISTS (
                               SELECT 1 FROM orchestration_task_claims AS claim
                               WHERE claim.orchestration_task_id = task.orchestration_task_id
                                 AND claim.status = 'active'
                                 AND claim.lease_expires_at <= now()
                           ) AS lease_expired,
                           count(*) OVER () AS total_count,
                           (
                               SELECT COALESCE(
                                   result.evidence ->> 'blocking_reason', result.summary
                               )
                               FROM orchestration_task_results AS result
                               WHERE result.orchestration_task_id = task.orchestration_task_id
                                 AND result.outcome IN ('blocked', 'failed')
                               ORDER BY result.submitted_at DESC, result.result_id DESC
                               LIMIT 1
                           ) AS blocking_reason
                    FROM orchestration_tasks AS task
                    WHERE (%s::text IS NULL OR task.project_id = %s)
                      AND (%s::text IS NULL OR task.automation_run_id = %s)
                    ORDER BY task.updated_at DESC, task.automation_run_id, task.sequence
                    LIMIT %s
                )
                SELECT task.orchestration_task_id, task.automation_run_id,
                       task.change_request_id, task.project_id, task.sequence,
                       task.title, task.state, task.lease_expired, task.total_count,
                       task.attempt_count, task.max_attempts, task.blocking_reason,
                       COALESCE(
                           jsonb_agg(dependency.depends_on_task_id
                                     ORDER BY dependency.depends_on_task_id)
                               FILTER (WHERE dependency.depends_on_task_id IS NOT NULL),
                           '[]'::jsonb
                       ) AS dependencies
                FROM eligible AS task
                LEFT JOIN orchestration_task_dependencies AS dependency
                  ON dependency.orchestration_task_id = task.orchestration_task_id
                GROUP BY task.orchestration_task_id, task.automation_run_id,
                         task.change_request_id, task.project_id, task.sequence,
                         task.title, task.state, task.attempt_count, task.max_attempts,
                         task.lease_expired, task.total_count,
                         task.blocking_reason, task.updated_at
                ORDER BY task.automation_run_id, task.sequence, task.orchestration_task_id
                """,
                (
                    project_id,
                    project_id,
                    automation_run_id,
                    automation_run_id,
                    limit,
                ),
            )
            rows = cursor.fetchall()
        tasks = [
            {
                "orchestration_task_id": str(row["orchestration_task_id"]),
                "automation_run_id": str(row["automation_run_id"]),
                "change_request_id": str(row["change_request_id"]),
                "project_id": str(row["project_id"]),
                "sequence": int(row["sequence"]),
                "title": str(row["title"]),
                "state": str(row["state"]),
                "effective_state": (
                    (
                        "failed"
                        if int(row["attempt_count"]) >= int(row["max_attempts"])
                        else "ready"
                    )
                    if bool(row["lease_expired"]) and str(row["state"]) in {"claimed", "running"}
                    else str(row["state"])
                ),
                "lease_expired": bool(row["lease_expired"]),
                "blocking_reason": (
                    str(row["blocking_reason"]) if row["blocking_reason"] is not None else None
                ),
                "dependencies": [str(value) for value in row["dependencies"]],
            }
            for row in rows
        ]
        total_count = int(rows[0]["total_count"]) if rows else 0
        return {
            "tasks": tasks,
            "count": len(tasks),
            "total_count": total_count,
            "truncated": total_count > len(tasks),
        }

    def complete_open_for_run(self, *, automation_run_id: str, actor: str) -> None:
        """Close the last open task when Canonical workflow state is completed."""
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT orchestration_task_id, project_id
                FROM orchestration_tasks
                WHERE automation_run_id = %s
                  AND state IN ('ready', 'claimed', 'running', 'submitted')
                FOR UPDATE
                """,
                (automation_run_id,),
            )
            for task_id_value, project_id_value in cursor.fetchall():
                task_id = str(task_id_value)
                self._complete_active_claim_from_canonical(
                    cursor,
                    task_id=task_id,
                    project_id=str(project_id_value),
                    actor=actor,
                )
                cursor.execute(
                    """
                    UPDATE orchestration_tasks SET state = 'completed', updated_at = now()
                    WHERE orchestration_task_id = %s
                    """,
                    (task_id,),
                )
                self._append_event(
                    cursor,
                    task_id=task_id,
                    project_id=str(project_id_value),
                    event_type="completed",
                    actor=actor,
                    payload={"reason": "canonical_workflow_completed"},
                )

    def supersede_open_for_run(self, *, automation_run_id: str, actor: str) -> None:
        """Release and retire tasks owned by a superseded automation run."""
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT orchestration_task_id, project_id
                FROM orchestration_tasks
                WHERE automation_run_id = %s
                  AND state IN ('ready', 'claimed', 'running', 'submitted')
                FOR UPDATE
                """,
                (automation_run_id,),
            )
            for task_id_value, project_id_value in cursor.fetchall():
                task_id = str(task_id_value)
                self._close_active_claim(cursor, task_id, "automation_run_superseded")
                cursor.execute(
                    """
                    UPDATE orchestration_tasks SET state = 'superseded', updated_at = now()
                    WHERE orchestration_task_id = %s
                    """,
                    (task_id,),
                )
                self._append_event(
                    cursor,
                    task_id=task_id,
                    project_id=str(project_id_value),
                    event_type="superseded",
                    actor=actor,
                    payload={"reason": "automation_run_superseded"},
                )

    def update_priority(self, *, task_id: str, priority: int, actor: str) -> dict[str, object]:
        """Update queue priority while preserving the immutable task definition."""
        _validate_actor(actor)
        if not 1 <= priority <= 1000:
            raise ValueError("Orchestration Task priority must be between 1 and 1000")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT project_id, state, priority
                FROM orchestration_tasks
                WHERE orchestration_task_id = %s
                FOR UPDATE
                """,
                (task_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("Orchestration Task does not exist")
            if str(row[1]) not in {"ready", "blocked", "failed"}:
                raise ValueError("Only queued or retryable Tasks can change priority")
            previous = int(row[2])
            if previous == priority:
                return self.view(task_id)
            cursor.execute(
                """
                UPDATE orchestration_tasks
                SET priority = %s, updated_at = now()
                WHERE orchestration_task_id = %s
                """,
                (priority, task_id),
            )
            self._append_event(
                cursor,
                task_id=task_id,
                project_id=str(row[0]),
                event_type="priority_updated",
                actor=actor,
                payload={"previous_priority": previous, "priority": priority},
            )
        return self.view(task_id)

    def list_ready(
        self,
        *,
        executor_kind: str,
        capabilities: tuple[str, ...],
        project_id: str | None = None,
    ) -> list[dict[str, object]]:
        _validate_executor(executor_kind, "executor")
        _validate_capabilities(capabilities)
        query = """
            SELECT task.orchestration_task_id
            FROM orchestration_tasks AS task
            WHERE (
                  task.state = 'ready'
                  OR (
                      task.state IN ('claimed', 'running')
                      AND EXISTS (
                          SELECT 1 FROM orchestration_task_claims AS expired_claim
                          WHERE expired_claim.orchestration_task_id = task.orchestration_task_id
                            AND expired_claim.status = 'active'
                            AND expired_claim.lease_expires_at <= now()
                      )
                  )
              )
              AND task.attempt_count < task.max_attempts
              AND task.eligible_executor_kinds @> %s::jsonb
              AND task.required_capabilities <@ %s::jsonb
              AND (%s::text IS NULL OR task.project_id = %s)
              AND NOT EXISTS (
                  SELECT 1 FROM orchestration_task_dependencies AS dependency
                  JOIN orchestration_tasks AS parent
                    ON parent.orchestration_task_id = dependency.depends_on_task_id
                  WHERE dependency.orchestration_task_id = task.orchestration_task_id
                    AND parent.state <> 'completed'
              )
              AND (
                  SELECT count(*) FROM orchestration_tasks AS active
                  WHERE active.automation_run_id = task.automation_run_id
                    AND (
                        active.state = 'submitted'
                        OR (
                            active.state IN ('claimed', 'running')
                            AND EXISTS (
                                SELECT 1 FROM orchestration_task_claims AS live_claim
                                WHERE live_claim.orchestration_task_id =
                                      active.orchestration_task_id
                                  AND live_claim.status = 'active'
                                  AND live_claim.lease_expires_at > now()
                            )
                        )
                    )
              ) < %s
            ORDER BY task.priority DESC,
                     (
                         SELECT count(*)
                         FROM orchestration_task_claims AS history
                         JOIN orchestration_tasks AS history_task
                           ON history_task.orchestration_task_id = history.orchestration_task_id
                         WHERE history_task.automation_run_id = task.automation_run_id
                           AND history.claimed_at >= now() - interval '1 hour'
                     ),
                     task.created_at, task.automation_run_id, task.sequence
        """
        with self._connection.cursor() as cursor:
            cursor.execute(
                query,
                (
                    _json((executor_kind,)),
                    _json(capabilities),
                    project_id,
                    project_id,
                    self._scheduling_policy.max_active_tasks_per_run,
                ),
            )
            task_ids = [str(row[0]) for row in cursor.fetchall()]
        return [self.view(task_id) for task_id in task_ids]

    def claim_next(
        self,
        *,
        executor_kind: str,
        executor_id: str,
        capabilities: tuple[str, ...],
        project_id: str | None = None,
        worker_token: str | None = None,
    ) -> dict[str, object] | None:
        _validate_executor(executor_kind, executor_id)
        _validate_capabilities(capabilities)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            capabilities, project_id = self._registered_claim_policy_locked(
                cursor,
                executor_kind=executor_kind,
                executor_id=executor_id,
                supplied_capabilities=capabilities,
                project_id=project_id,
                worker_token=worker_token,
            )
            self._expire_leases_locked(cursor)
            cursor.execute(
                """
                SELECT task.orchestration_task_id, task.project_id, task.lease_seconds
                FROM orchestration_tasks AS task
                JOIN change_automation_runs AS run
                  ON run.automation_run_id = task.automation_run_id
                 AND run.project_id = task.project_id
                WHERE task.state = 'ready'
                  AND task.attempt_count < task.max_attempts
                  AND task.eligible_executor_kinds @> %s::jsonb
                  AND task.required_capabilities <@ %s::jsonb
                  AND (%s::text IS NULL OR task.project_id = %s)
                  AND NOT EXISTS (
                      SELECT 1 FROM orchestration_task_dependencies AS dependency
                      JOIN orchestration_tasks AS parent
                        ON parent.orchestration_task_id = dependency.depends_on_task_id
                      WHERE dependency.orchestration_task_id = task.orchestration_task_id
                        AND parent.state <> 'completed'
                  )
                  AND (
                      SELECT count(*) FROM orchestration_tasks AS active
                      WHERE active.automation_run_id = task.automation_run_id
                        AND active.state IN ('claimed', 'running', 'submitted')
                  ) < %s
                ORDER BY task.priority DESC,
                         (
                             SELECT count(*)
                             FROM orchestration_task_claims AS history
                             JOIN orchestration_tasks AS history_task
                               ON history_task.orchestration_task_id = history.orchestration_task_id
                             WHERE history_task.automation_run_id = task.automation_run_id
                               AND history.claimed_at >= now() - interval '1 hour'
                         ),
                         task.created_at, task.automation_run_id, task.sequence
                LIMIT 1
                FOR UPDATE OF task, run SKIP LOCKED
                """,
                (
                    _json((executor_kind,)),
                    _json(capabilities),
                    project_id,
                    project_id,
                    self._scheduling_policy.max_active_tasks_per_run,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            task_id, lease_token = self._create_claim_locked(
                cursor,
                row=row,
                executor_kind=executor_kind,
                executor_id=executor_id,
                capabilities=capabilities,
            )
        result = self.view(task_id)
        result["lease_token"] = lease_token
        return result

    def claim(
        self,
        *,
        task_id: str,
        executor_kind: str,
        executor_id: str,
        capabilities: tuple[str, ...],
        project_id: str | None = None,
        worker_token: str | None = None,
    ) -> dict[str, object]:
        """Claim the selected ready task instead of taking an implicit queue head."""
        _validate_executor(executor_kind, executor_id)
        _validate_capabilities(capabilities)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            capabilities, project_id = self._registered_claim_policy_locked(
                cursor,
                executor_kind=executor_kind,
                executor_id=executor_id,
                supplied_capabilities=capabilities,
                project_id=project_id,
                worker_token=worker_token,
            )
            self._expire_leases_locked(cursor, task_id=task_id)
            cursor.execute(
                """
                SELECT task.orchestration_task_id, task.project_id, task.lease_seconds
                FROM orchestration_tasks AS task
                JOIN change_automation_runs AS run
                  ON run.automation_run_id = task.automation_run_id
                 AND run.project_id = task.project_id
                WHERE task.orchestration_task_id = %s
                  AND task.state = 'ready'
                  AND task.attempt_count < task.max_attempts
                  AND task.eligible_executor_kinds @> %s::jsonb
                  AND task.required_capabilities <@ %s::jsonb
                  AND (%s::text IS NULL OR task.project_id = %s)
                  AND NOT EXISTS (
                      SELECT 1 FROM orchestration_task_dependencies AS dependency
                      JOIN orchestration_tasks AS parent
                        ON parent.orchestration_task_id = dependency.depends_on_task_id
                      WHERE dependency.orchestration_task_id = task.orchestration_task_id
                        AND parent.state <> 'completed'
                  )
                  AND (
                      SELECT count(*) FROM orchestration_tasks AS active
                      WHERE active.automation_run_id = task.automation_run_id
                        AND active.state IN ('claimed', 'running', 'submitted')
                  ) < %s
                FOR UPDATE OF task, run
                """,
                (
                    task_id,
                    _json((executor_kind,)),
                    _json(capabilities),
                    project_id,
                    project_id,
                    self._scheduling_policy.max_active_tasks_per_run,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError(
                    "Orchestration Task is not ready or executor capabilities do not match"
                )
            claimed_task_id, lease_token = self._create_claim_locked(
                cursor,
                row=row,
                executor_kind=executor_kind,
                executor_id=executor_id,
                capabilities=capabilities,
            )
        result = self.view(claimed_task_id)
        result["lease_token"] = lease_token
        return result

    def heartbeat(self, *, task_id: str, executor_id: str, lease_token: str) -> dict[str, object]:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            claim_id = self._require_live_claim(cursor, task_id, executor_id, lease_token)
            cursor.execute(
                """
                SELECT project_id, lease_seconds, state
                FROM orchestration_tasks WHERE orchestration_task_id = %s FOR UPDATE
                """,
                (task_id,),
            )
            task = cursor.fetchone()
            if task is None:
                raise ValueError("Orchestration Task does not exist")
            event_type = "lease_renewed"
            if str(task[2]) == "claimed":
                cursor.execute(
                    """
                    UPDATE orchestration_tasks SET state = 'running', updated_at = now()
                    WHERE orchestration_task_id = %s
                    """,
                    (task_id,),
                )
                event_type = "started"
            cursor.execute(
                """
                UPDATE orchestration_task_claims
                SET lease_expires_at = now() + make_interval(secs => %s)
                WHERE claim_id = %s
                """,
                (int(task[1]), claim_id),
            )
            cursor.execute(
                """
                UPDATE orchestration_worker_registrations
                SET lease_expires_at = now() + GREATEST(
                        lease_expires_at - last_seen_at, interval '10 seconds'
                    ),
                    last_seen_at = now(), updated_at = now()
                WHERE executor_id = %s AND status = 'online'
                  AND executor_kind = (
                      SELECT executor_kind FROM orchestration_task_claims
                      WHERE claim_id = %s
                  )
                """,
                (executor_id, claim_id),
            )
            self._append_event(
                cursor,
                task_id=task_id,
                project_id=str(task[0]),
                event_type=event_type,
                actor=executor_id,
                payload={"claim_id": claim_id},
            )
        return self.view(task_id)

    def release(
        self, *, task_id: str, executor_id: str, lease_token: str, reason: str
    ) -> dict[str, object]:
        if not reason.strip():
            raise ValueError("release reason must not be blank")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            claim_id = self._require_live_claim(cursor, task_id, executor_id, lease_token)
            cursor.execute(
                """
                UPDATE orchestration_task_claims
                SET status = 'released', released_at = now(), release_reason = %s
                WHERE claim_id = %s
                """,
                (reason, claim_id),
            )
            cursor.execute(
                """
                UPDATE orchestration_tasks
                SET state = CASE WHEN attempt_count >= max_attempts THEN 'failed' ELSE 'ready' END,
                    updated_at = now()
                WHERE orchestration_task_id = %s
                RETURNING project_id, state
                """,
                (task_id,),
            )
            task = cursor.fetchone()
            if task is None:
                raise ValueError("Orchestration Task does not exist")
            self._append_event(
                cursor,
                task_id=task_id,
                project_id=str(task[0]),
                event_type="released" if str(task[1]) == "ready" else "failed",
                actor=executor_id,
                payload={"claim_id": claim_id, "reason": reason},
            )
        return self.view(task_id)

    def record_result(
        self,
        *,
        task_id: str,
        executor_id: str,
        lease_token: str,
        outcome: str,
        summary: str,
        artifact_refs: tuple[str, ...],
        evidence: dict[str, object],
    ) -> dict[str, object]:
        if outcome not in {"completed", "failed", "blocked"}:
            raise ValueError("Orchestration Task outcome is invalid")
        if not summary.strip():
            raise ValueError("result summary must not be blank")
        if len(summary) > 10_000:
            raise ValueError("result summary is too long")
        if any(not value.strip() or len(value) > 2_000 for value in artifact_refs):
            raise ValueError("artifact references must be non-blank and bounded")
        if len(artifact_refs) > 500 or len(set(artifact_refs)) != len(artifact_refs):
            raise ValueError("artifact references must be unique and contain at most 500 values")
        validate_orchestration_result_evidence(evidence)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            self._expire_leases_locked(cursor, task_id=task_id)
            cursor.execute(
                """
                SELECT claim_id, status
                FROM orchestration_task_claims
                WHERE orchestration_task_id = %s
                  AND executor_id = %s
                  AND lease_token_digest = %s
                FOR UPDATE
                """,
                (task_id, executor_id, _digest(lease_token)),
            )
            claim = cursor.fetchone()
            if claim is None:
                raise ValueError("Orchestration Task lease does not belong to executor")
            claim_id, claim_status = str(claim[0]), str(claim[1])
            if claim_status not in {"active", "completed"}:
                raise ValueError("Orchestration Task lease is no longer active")
            cursor.execute(
                """
                SELECT project_id, expected_output_types
                FROM orchestration_tasks WHERE orchestration_task_id = %s FOR UPDATE
                """,
                (task_id,),
            )
            task = cursor.fetchone()
            if task is None:
                raise ValueError("Orchestration Task does not exist")
            expected_outputs = list(task[1])
            if outcome == "completed" and expected_outputs and not artifact_refs:
                raise ValueError("completed task requires output artifact references")
            if outcome == "completed" and not evidence:
                raise ValueError("completed task requires acceptance evidence")
            payload = {
                "task_id": task_id,
                "claim_id": claim_id,
                "outcome": outcome,
                "summary": summary,
                "artifact_refs": list(artifact_refs),
                "evidence": evidence,
            }
            result_id = f"result-{hashlib.sha256(_json(payload).encode()).hexdigest()[:32]}"
            payload_digest = _digest(_json(payload))
            if claim_status == "completed":
                cursor.execute(
                    """
                    SELECT payload_digest FROM orchestration_task_results
                    WHERE claim_id = %s
                    """,
                    (claim_id,),
                )
                stored = cursor.fetchone()
                if stored is None or str(stored[0]) != payload_digest:
                    raise PersistenceConflictError(
                        "Orchestration Task Result replay has different content"
                    )
            else:
                cursor.execute(
                    """
                    INSERT INTO orchestration_task_results (
                        result_id, orchestration_task_id, project_id, claim_id, outcome,
                        summary, artifact_refs, evidence, payload_digest, submitted_by
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
                    """,
                    (
                        result_id,
                        task_id,
                        str(task[0]),
                        claim_id,
                        outcome,
                        summary,
                        _json(artifact_refs),
                        _json(evidence),
                        payload_digest,
                        executor_id,
                    ),
                )
                cursor.execute(
                    """
                    UPDATE orchestration_task_claims
                    SET status = 'completed', released_at = now(), release_reason = %s
                    WHERE claim_id = %s
                    """,
                    (f"result:{outcome}", claim_id),
                )
                task_state = "submitted" if outcome == "completed" else outcome
                cursor.execute(
                    """
                    UPDATE orchestration_tasks SET state = %s, updated_at = now()
                    WHERE orchestration_task_id = %s
                    """,
                    (task_state, task_id),
                )
                self._append_event(
                    cursor,
                    task_id=task_id,
                    project_id=str(task[0]),
                    event_type="result_submitted" if outcome == "completed" else outcome,
                    actor=executor_id,
                    payload={"claim_id": claim_id, "result_id": result_id},
                )
        return self.view(task_id)

    def requeue(self, *, task_id: str, actor: str, reason: str) -> dict[str, object]:
        """Explicitly retry a failed or blocked task without rewriting its history."""
        if not reason.strip():
            raise ValueError("requeue reason must not be blank")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT project_id, state, attempt_count, max_attempts
                FROM orchestration_tasks
                WHERE orchestration_task_id = %s FOR UPDATE
                """,
                (task_id,),
            )
            task = cursor.fetchone()
            if task is None:
                raise ValueError("Orchestration Task does not exist")
            if str(task[1]) not in {"failed", "blocked"}:
                raise ValueError("only failed or blocked Orchestration Task can be requeued")
            if int(task[2]) >= int(task[3]):
                raise ValueError("Orchestration Task has exhausted its attempts")
            cursor.execute(
                """
                UPDATE orchestration_tasks SET state = 'ready', updated_at = now()
                WHERE orchestration_task_id = %s
                """,
                (task_id,),
            )
            self._append_event(
                cursor,
                task_id=task_id,
                project_id=str(task[0]),
                event_type="requeued",
                actor=actor,
                payload={"reason": reason},
            )
        return self.view(task_id)

    def view(self, task_id: str) -> dict[str, object]:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT * FROM orchestration_tasks WHERE orchestration_task_id = %s",
                (task_id,),
            )
            task = cursor.fetchone()
            if task is None:
                raise ValueError("Orchestration Task does not exist")
            cursor.execute(
                """
                SELECT claim_id, executor_kind, executor_id, capabilities, status,
                       claimed_at, lease_expires_at, released_at, release_reason
                FROM orchestration_task_claims
                WHERE orchestration_task_id = %s ORDER BY claimed_at, claim_id
                """,
                (task_id,),
            )
            claims = cursor.fetchall()
            cursor.execute(
                """
                SELECT result_id, claim_id, outcome, summary, artifact_refs, evidence,
                       submitted_by, submitted_at
                FROM orchestration_task_results
                WHERE orchestration_task_id = %s ORDER BY submitted_at, result_id
                """,
                (task_id,),
            )
            results = cursor.fetchall()
            cursor.execute(
                """
                SELECT sequence, event_type, actor, payload, created_at
                FROM orchestration_task_events
                WHERE orchestration_task_id = %s ORDER BY sequence
                """,
                (task_id,),
            )
            events = cursor.fetchall()
            cursor.execute(
                """
                SELECT depends_on_task_id FROM orchestration_task_dependencies
                WHERE orchestration_task_id = %s ORDER BY depends_on_task_id
                """,
                (task_id,),
            )
            dependencies = [str(row["depends_on_task_id"]) for row in cursor.fetchall()]
        state = str(task["state"])
        lease_expired = any(
            str(value["status"]) == "active" and value["lease_expires_at"] <= datetime.now(UTC)
            for value in claims
        )
        if lease_expired and state in {"claimed", "running"}:
            effective_state = (
                "failed" if int(task["attempt_count"]) >= int(task["max_attempts"]) else "ready"
            )
        else:
            effective_state = state
        claim_views = [_claim_view(value) for value in claims]
        result_views = [_result_view(value) for value in results]
        event_views = [_event_view(value) for value in events]
        return {
            "orchestration_task_id": str(task["orchestration_task_id"]),
            "protocol_version": str(task["protocol_version"]),
            "automation_run_id": str(task["automation_run_id"]),
            "change_request_id": str(task["change_request_id"]),
            "project_id": str(task["project_id"]),
            "sequence": int(task["sequence"]),
            "step_key": str(task["step_key"]),
            "action": str(task["action"]),
            "title": str(task["title"]),
            "instruction": str(task["instruction"]),
            "task_kind": str(task["task_kind"]),
            "state": state,
            "effective_state": effective_state,
            "lease_expired": lease_expired,
            "required_capabilities": list(task["required_capabilities"]),
            "eligible_executor_kinds": list(task["eligible_executor_kinds"]),
            "input_artifact_refs": list(task["input_artifact_refs"]),
            "expected_output_types": list(task["expected_output_types"]),
            "acceptance_criteria": list(task["acceptance_criteria"]),
            "lease_seconds": int(task["lease_seconds"]),
            "max_attempts": int(task["max_attempts"]),
            "priority": int(task["priority"]),
            "attempt_count": int(task["attempt_count"]),
            "definition_digest": str(task["definition_digest"]),
            "dependencies": dependencies,
            "created_by": str(task["created_by"]),
            "created_at": _time(task["created_at"]),
            "updated_at": _time(task["updated_at"]),
            "blocking_reason": _blocking_reason(state, result_views, event_views),
            "claims": claim_views,
            "results": result_views,
            "events": event_views,
        }

    def _registered_claim_policy_locked(
        self,
        cursor: Cursor[Any],
        *,
        executor_kind: str,
        executor_id: str,
        supplied_capabilities: tuple[str, ...],
        project_id: str | None,
        worker_token: str | None,
    ) -> tuple[tuple[str, ...], str | None]:
        """Use persisted Worker policy when the executor has registered itself."""
        if executor_kind == "human":
            return supplied_capabilities, project_id
        if worker_token is None:
            raise ValueError("registered Worker credential is required")
        _validate_worker_token(worker_token)
        cursor.execute(
            """
            SELECT capabilities, project_id, max_concurrent_tasks, status,
                   lease_expires_at > now() AS present, credential_digest
            FROM orchestration_worker_registrations
            WHERE executor_kind = %s AND executor_id = %s
            FOR UPDATE
            """,
            (executor_kind, executor_id),
        )
        registration = cursor.fetchone()
        if registration is None:
            raise ValueError("registered Worker credential is required")
        registered_capabilities = tuple(str(value) for value in registration[0])
        registered_project = str(registration[1]) if registration[1] is not None else None
        if not secrets.compare_digest(str(registration[5]), _digest(worker_token)):
            raise ValueError("Orchestration Worker credential is invalid")
        if str(registration[3]) != "online":
            raise ValueError("Orchestration Worker is not accepting new Tasks")
        if not bool(registration[4]):
            raise ValueError("Orchestration Worker registration is not live")
        if set(supplied_capabilities) != set(registered_capabilities):
            raise ValueError("executor capabilities differ from Worker registration")
        if registered_project is not None and project_id not in {None, registered_project}:
            raise ValueError("executor project differs from Worker registration")
        cursor.execute(
            """
            SELECT count(*)
            FROM orchestration_task_claims
            WHERE executor_kind = %s AND executor_id = %s
              AND status = 'active' AND lease_expires_at > now()
            """,
            (executor_kind, executor_id),
        )
        active_row = cursor.fetchone()
        active_count = int(active_row[0]) if active_row is not None else 0
        if active_count >= int(registration[2]):
            raise ValueError("Orchestration Worker concurrency limit is exhausted")
        return registered_capabilities, registered_project or project_id

    def _create_claim_locked(
        self,
        cursor: Cursor[Any],
        *,
        row: tuple[Any, ...],
        executor_kind: str,
        executor_id: str,
        capabilities: tuple[str, ...],
    ) -> tuple[str, str]:
        task_id, task_project_id, lease_seconds = str(row[0]), str(row[1]), int(row[2])
        claim_id = f"claim-{secrets.token_hex(16)}"
        lease_token = secrets.token_urlsafe(32)
        cursor.execute(
            """
            INSERT INTO orchestration_task_claims (
                claim_id, orchestration_task_id, project_id, executor_kind,
                executor_id, capabilities, lease_token_digest, status,
                lease_expires_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s::jsonb, %s, 'active',
                now() + make_interval(secs => %s)
            )
            """,
            (
                claim_id,
                task_id,
                task_project_id,
                executor_kind,
                executor_id,
                _json(capabilities),
                _digest(lease_token),
                lease_seconds,
            ),
        )
        cursor.execute(
            """
            UPDATE orchestration_tasks
            SET state = 'claimed', attempt_count = attempt_count + 1, updated_at = now()
            WHERE orchestration_task_id = %s
            """,
            (task_id,),
        )
        self._append_event(
            cursor,
            task_id=task_id,
            project_id=task_project_id,
            event_type="claimed",
            actor=executor_id,
            payload={
                "claim_id": claim_id,
                "executor_kind": executor_kind,
                "capabilities": list(capabilities),
            },
        )
        cursor.execute(
            """
            UPDATE orchestration_worker_registrations
            SET last_seen_at = now(), updated_at = now()
            WHERE executor_kind = %s AND executor_id = %s
            """,
            (executor_kind, executor_id),
        )
        return task_id, lease_token

    def _require_live_claim(
        self, cursor: Cursor[Any], task_id: str, executor_id: str, lease_token: str
    ) -> str:
        self._expire_leases_locked(cursor, task_id=task_id)
        cursor.execute(
            """
            SELECT claim_id, executor_id, lease_token_digest
            FROM orchestration_task_claims
            WHERE orchestration_task_id = %s AND status = 'active'
            FOR UPDATE
            """,
            (task_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError("Orchestration Task has no active lease")
        if str(row[1]) != executor_id or not secrets.compare_digest(
            str(row[2]), _digest(lease_token)
        ):
            raise ValueError("Orchestration Task lease does not belong to executor")
        return str(row[0])

    def _expire_leases_locked(self, cursor: Cursor[Any], *, task_id: str | None = None) -> None:
        cursor.execute(
            """
            SELECT claim.claim_id, claim.orchestration_task_id, claim.project_id,
                   claim.executor_id, task.attempt_count, task.max_attempts
            FROM orchestration_task_claims AS claim
            JOIN orchestration_tasks AS task
              ON task.orchestration_task_id = claim.orchestration_task_id
            WHERE claim.status = 'active'
              AND claim.lease_expires_at <= now()
              AND (%s::text IS NULL OR claim.orchestration_task_id = %s)
            FOR UPDATE OF claim, task
            """,
            (task_id, task_id),
        )
        expired_claims = cursor.fetchall()
        for claim_id, expired_task_id, project_id, executor_id, attempts, maximum in expired_claims:
            state = "failed" if int(attempts) >= int(maximum) else "ready"
            cursor.execute(
                """
                UPDATE orchestration_task_claims
                SET status = 'expired', released_at = now(), release_reason = 'lease_expired'
                WHERE claim_id = %s
                """,
                (claim_id,),
            )
            cursor.execute(
                """
                UPDATE orchestration_tasks SET state = %s, updated_at = now()
                WHERE orchestration_task_id = %s
                """,
                (state, expired_task_id),
            )
            self._append_event(
                cursor,
                task_id=str(expired_task_id),
                project_id=str(project_id),
                event_type="lease_expired" if state == "ready" else "failed",
                actor=str(executor_id),
                payload={"claim_id": str(claim_id)},
            )

    def _close_active_claim(self, cursor: Cursor[Any], task_id: str, reason: str) -> None:
        cursor.execute(
            """
            UPDATE orchestration_task_claims
            SET status = 'released', released_at = now(), release_reason = %s
            WHERE orchestration_task_id = %s AND status = 'active'
            """,
            (reason, task_id),
        )

    def _complete_active_claim_from_canonical(
        self,
        cursor: Cursor[Any],
        *,
        task_id: str,
        project_id: str,
        actor: str,
    ) -> None:
        """Reconcile a claimed task when its Canonical business state already advanced."""
        cursor.execute(
            """
            SELECT claim_id, executor_id
            FROM orchestration_task_claims
            WHERE orchestration_task_id = %s AND status = 'active'
            FOR UPDATE
            """,
            (task_id,),
        )
        claim = cursor.fetchone()
        if claim is None:
            return
        claim_id, executor_id = str(claim[0]), str(claim[1])
        payload = {
            "task_id": task_id,
            "claim_id": claim_id,
            "outcome": "completed",
            "summary": "Canonical 状態の進行を確認しました。",
            "artifact_refs": [],
            "evidence": {"canonical_state_advanced": True, "reconciled_by": actor},
        }
        result_id = f"result-{hashlib.sha256(_json(payload).encode()).hexdigest()[:32]}"
        cursor.execute(
            """
            INSERT INTO orchestration_task_results (
                result_id, orchestration_task_id, project_id, claim_id, outcome,
                summary, artifact_refs, evidence, payload_digest, submitted_by
            ) VALUES (
                %s, %s, %s, %s, 'completed', %s, '[]'::jsonb,
                %s::jsonb, %s, %s
            )
            ON CONFLICT (claim_id) DO NOTHING
            """,
            (
                result_id,
                task_id,
                project_id,
                claim_id,
                str(payload["summary"]),
                _json(payload["evidence"]),
                _digest(_json(payload)),
                executor_id,
            ),
        )
        cursor.execute(
            """
            UPDATE orchestration_task_claims
            SET status = 'completed', released_at = now(),
                release_reason = 'canonical_state_advanced'
            WHERE claim_id = %s
            """,
            (claim_id,),
        )

    def _append_event(
        self,
        cursor: Cursor[Any],
        *,
        task_id: str,
        project_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, object],
    ) -> None:
        cursor.execute(
            """
            SELECT COALESCE(max(sequence), 0) + 1
            FROM orchestration_task_events WHERE orchestration_task_id = %s
            """,
            (task_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Orchestration Task event sequence query returned no row")
        sequence = int(row[0])
        payload_text = _json(payload)
        cursor.execute(
            """
            INSERT INTO orchestration_task_events (
                event_id, orchestration_task_id, project_id, sequence,
                event_type, actor, payload, payload_digest
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                f"{task_id}-event-{sequence:04d}",
                task_id,
                project_id,
                sequence,
                event_type,
                actor,
                payload_text,
                _digest(payload_text),
            ),
        )

    def _append_worker_event(
        self,
        cursor: Cursor[Any],
        *,
        executor_kind: str,
        executor_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, object],
    ) -> None:
        cursor.execute(
            """
            SELECT COALESCE(max(sequence), 0) + 1
            FROM orchestration_worker_events
            WHERE executor_kind = %s AND executor_id = %s
            """,
            (executor_kind, executor_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Orchestration Worker event sequence query returned no row")
        payload_text = _json(payload)
        cursor.execute(
            """
            INSERT INTO orchestration_worker_events (
                executor_kind, executor_id, sequence, event_type,
                actor, payload, payload_digest
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                executor_kind,
                executor_id,
                int(row[0]),
                event_type,
                actor,
                payload_text,
                _digest(payload_text),
            ),
        )


def _validate_executor(executor_kind: str, executor_id: str) -> None:
    if executor_kind not in {"agent", "subagent", "human"}:
        raise ValueError("executor_kind must be agent, subagent, or human")
    if not executor_id.strip() or len(executor_id) > 200:
        raise ValueError("executor_id must be non-blank and at most 200 characters")


def _validate_capabilities(capabilities: tuple[str, ...]) -> None:
    if not capabilities or len(capabilities) > 100:
        raise ValueError("capabilities must contain between 1 and 100 values")
    if len(set(capabilities)) != len(capabilities):
        raise ValueError("capabilities must be unique")
    if any(not value.strip() or len(value) > 160 for value in capabilities):
        raise ValueError("capabilities must be non-blank and at most 160 characters")


def _validate_actor(actor: str) -> None:
    if not actor.strip() or len(actor) > 200:
        raise ValueError("actor must be non-blank and at most 200 characters")


def _validate_worker_token(worker_token: str) -> None:
    if not worker_token.strip() or len(worker_token) > 500:
        raise ValueError("Worker token must be non-blank and bounded")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _time(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _claim_view(value: dict[str, Any]) -> dict[str, object]:
    return {
        "claim_id": str(value["claim_id"]),
        "executor_kind": str(value["executor_kind"]),
        "executor_id": str(value["executor_id"]),
        "capabilities": list(value["capabilities"]),
        "status": str(value["status"]),
        "claimed_at": _time(value["claimed_at"]),
        "lease_expires_at": _time(value["lease_expires_at"]),
        "released_at": _time(value["released_at"]),
        "release_reason": value["release_reason"],
    }


def _result_view(value: dict[str, Any]) -> dict[str, object]:
    return {
        "result_id": str(value["result_id"]),
        "claim_id": str(value["claim_id"]),
        "outcome": str(value["outcome"]),
        "summary": str(value["summary"]),
        "artifact_refs": list(value["artifact_refs"]),
        "evidence": dict(value["evidence"]),
        "submitted_by": str(value["submitted_by"]),
        "submitted_at": _time(value["submitted_at"]),
    }


def _event_view(value: dict[str, Any]) -> dict[str, object]:
    return {
        "sequence": int(value["sequence"]),
        "event_type": str(value["event_type"]),
        "actor": str(value["actor"]),
        "payload": dict(value["payload"]),
        "created_at": _time(value["created_at"]),
    }


def _worker_view(value: dict[str, Any]) -> dict[str, object]:
    return {
        "executor_kind": str(value["executor_kind"]),
        "executor_id": str(value["executor_id"]),
        "capabilities": list(value["capabilities"]),
        "project_id": value["project_id"],
        "max_concurrent_tasks": int(value["max_concurrent_tasks"]),
        "active_task_count": int(value["active_task_count"]),
        "status": str(value["status"]),
        "present": bool(value["present"]),
        "live": bool(value["live"]),
        "registered_at": _time(value["registered_at"]),
        "last_seen_at": _time(value["last_seen_at"]),
        "lease_expires_at": _time(value["lease_expires_at"]),
    }


def _number(value: Any) -> float | None:
    return round(float(value), 3) if value is not None else None


def _blocking_reason(
    state: str,
    results: list[dict[str, object]],
    events: list[dict[str, object]],
) -> str | None:
    if state not in {"blocked", "failed"}:
        return None
    for result in reversed(results):
        if result["outcome"] not in {"blocked", "failed"}:
            continue
        evidence = result["evidence"]
        if isinstance(evidence, dict):
            reason = evidence.get("blocking_reason")
            if isinstance(reason, str) and reason.strip():
                return reason
        summary = result["summary"]
        if isinstance(summary, str) and summary.strip():
            return summary
    for event in reversed(events):
        payload = event["payload"]
        if isinstance(payload, dict):
            reason = payload.get("reason")
            if isinstance(reason, str) and reason.strip():
                return reason
    return "阻断理由が記録されていません。"
