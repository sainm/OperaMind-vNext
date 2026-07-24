"""Claim, lease and resolve Profile Drift rebuild requests."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime
from typing import Any, cast

from psycopg import Connection, Cursor

from operamind.application.orchestration_task import validate_orchestration_result_evidence
from operamind.infrastructure.postgres.profile_rebuild_validation import (
    ProfileReplacementValidator,
)


class ProfileRebuildTaskQueue:
    """Adapt Profile rebuild requests to the reusable Orchestration Worker protocol."""

    def __init__(
        self,
        connection: Connection[Any],
        validator: ProfileReplacementValidator | None = None,
    ) -> None:
        self._connection = connection
        self._validator = validator or ProfileReplacementValidator()

    def list_ready(
        self,
        *,
        executor_kind: str,
        capabilities: tuple[str, ...],
        project_id: str | None = None,
    ) -> list[dict[str, object]]:
        _validate_executor(executor_kind, "executor")
        _validate_capabilities(capabilities)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            self._expire_leases_locked(cursor)
            cursor.execute(
                """
                SELECT request.profile_rebuild_request_id
                FROM profile_rebuild_requests AS request
                WHERE request.status = 'requested'
                  AND request.attempt_count < request.max_attempts
                  AND request.rebuild_action = ANY(%s::text[])
                  AND (%s::text IS NULL OR request.project_id = %s)
                  AND NOT EXISTS (
                      SELECT 1
                      FROM profile_rebuild_request_dependencies AS dependency
                      JOIN profile_rebuild_requests AS parent
                        ON parent.profile_rebuild_request_id = dependency.depends_on_request_id
                       AND parent.project_id = dependency.project_id
                      WHERE dependency.profile_rebuild_request_id =
                            request.profile_rebuild_request_id
                        AND dependency.project_id = request.project_id
                        AND parent.status <> 'completed'
                  )
                ORDER BY request.requested_at, request.profile_rebuild_batch_id,
                         request.phase_order, request.artifact_type, request.artifact_id
                """,
                (list(capabilities), project_id, project_id),
            )
            request_ids = [str(row[0]) for row in cursor.fetchall()]
        return [self.view(request_id) for request_id in request_ids]

    def claim(
        self,
        *,
        task_id: str,
        executor_kind: str,
        executor_id: str,
        capabilities: tuple[str, ...],
        worker_token: str,
        project_id: str | None = None,
    ) -> dict[str, object]:
        _validate_executor(executor_kind, executor_id)
        _validate_capabilities(capabilities)
        _validate_token(worker_token, "Worker")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            effective_project = self._require_worker_locked(
                cursor,
                executor_kind=executor_kind,
                executor_id=executor_id,
                capabilities=capabilities,
                worker_token=worker_token,
                project_id=project_id,
            )
            self._expire_leases_locked(cursor, request_id=task_id)
            cursor.execute(
                """
                SELECT request.project_id, request.lease_seconds,
                       request.rebuild_action
                FROM profile_rebuild_requests AS request
                WHERE request.profile_rebuild_request_id = %s
                  AND request.status = 'requested'
                  AND request.attempt_count < request.max_attempts
                  AND request.rebuild_action = ANY(%s::text[])
                  AND (%s::text IS NULL OR request.project_id = %s)
                  AND NOT EXISTS (
                      SELECT 1
                      FROM profile_rebuild_request_dependencies AS dependency
                      JOIN profile_rebuild_requests AS parent
                        ON parent.profile_rebuild_request_id = dependency.depends_on_request_id
                       AND parent.project_id = dependency.project_id
                      WHERE dependency.profile_rebuild_request_id =
                            request.profile_rebuild_request_id
                        AND dependency.project_id = request.project_id
                        AND parent.status <> 'completed'
                  )
                FOR UPDATE OF request
                """,
                (task_id, list(capabilities), effective_project, effective_project),
            )
            request = cursor.fetchone()
            if request is None:
                raise ValueError(
                    "Profile Rebuild Request is not ready or executor capabilities do not match"
                )
            request_project, lease_seconds = str(request[0]), int(request[1])
            claim_id = f"profile-rebuild-claim-{secrets.token_hex(16)}"
            lease_token = secrets.token_urlsafe(32)
            cursor.execute(
                """
                INSERT INTO profile_rebuild_claims (
                    profile_rebuild_claim_id, profile_rebuild_request_id, project_id,
                    executor_kind, executor_id, lease_token_digest, status,
                    lease_expires_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, 'active',
                    now() + make_interval(secs => %s)
                )
                """,
                (
                    claim_id,
                    task_id,
                    request_project,
                    executor_kind,
                    executor_id,
                    _digest(lease_token),
                    lease_seconds,
                ),
            )
            cursor.execute(
                """
                UPDATE profile_rebuild_requests
                SET status = 'in_progress', attempt_count = attempt_count + 1,
                    updated_at = now()
                WHERE profile_rebuild_request_id = %s
                """,
                (task_id,),
            )
            cursor.execute(
                """
                UPDATE profile_rebuild_batches SET status = 'in_progress'
                WHERE profile_rebuild_batch_id = (
                    SELECT profile_rebuild_batch_id FROM profile_rebuild_requests
                    WHERE profile_rebuild_request_id = %s
                )
                """,
                (task_id,),
            )
            self._append_event(
                cursor,
                request_id=task_id,
                project_id=request_project,
                event_type="claimed",
                actor=executor_id,
                payload={"claim_id": claim_id, "executor_kind": executor_kind},
            )
        result = self.view(task_id)
        result["lease_token"] = lease_token
        return result

    def heartbeat(self, *, task_id: str, executor_id: str, lease_token: str) -> dict[str, object]:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            claim_id, project_id = self._require_live_claim(
                cursor, task_id=task_id, executor_id=executor_id, lease_token=lease_token
            )
            cursor.execute(
                """
                UPDATE profile_rebuild_claims AS claim
                SET lease_expires_at = now() + make_interval(secs => request.lease_seconds)
                FROM profile_rebuild_requests AS request
                WHERE claim.profile_rebuild_claim_id = %s
                  AND request.profile_rebuild_request_id = claim.profile_rebuild_request_id
                """,
                (claim_id,),
            )
            self._append_event(
                cursor,
                request_id=task_id,
                project_id=project_id,
                event_type="heartbeat",
                actor=executor_id,
                payload={"claim_id": claim_id},
            )
        return self.view(task_id)

    def release(
        self, *, task_id: str, executor_id: str, lease_token: str, reason: str
    ) -> dict[str, object]:
        if not reason.strip() or len(reason) > 10_000:
            raise ValueError("release reason must be non-blank and bounded")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            claim_id, project_id = self._require_live_claim(
                cursor, task_id=task_id, executor_id=executor_id, lease_token=lease_token
            )
            cursor.execute(
                """
                UPDATE profile_rebuild_claims
                SET status = 'released', released_at = now(), release_reason = %s
                WHERE profile_rebuild_claim_id = %s
                """,
                (reason, claim_id),
            )
            cursor.execute(
                """
                UPDATE profile_rebuild_requests
                SET status = CASE
                        WHEN attempt_count >= max_attempts THEN 'failed' ELSE 'requested' END,
                    completed_at = CASE
                        WHEN attempt_count >= max_attempts THEN now() ELSE NULL END,
                    last_error = CASE
                        WHEN attempt_count >= max_attempts THEN %s ELSE NULL END,
                    updated_at = now()
                WHERE profile_rebuild_request_id = %s
                RETURNING status
                """,
                (_bounded(reason), task_id),
            )
            row = cursor.fetchone()
            status = str(row[0]) if row else "failed"
            self._append_event(
                cursor,
                request_id=task_id,
                project_id=project_id,
                event_type="released" if status == "requested" else "failed",
                actor=executor_id,
                payload={"claim_id": claim_id, "reason": _bounded(reason)},
            )
            if status == "failed":
                self._block_pending_batch_locked(cursor, task_id, reason)
            self._refresh_batch_locked(cursor, task_id)
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
            raise ValueError("Profile Rebuild outcome is invalid")
        if not summary.strip() or len(summary) > 10_000:
            raise ValueError("result summary must be non-blank and bounded")
        if len(artifact_refs) > 1 or any(
            not value.strip() or len(value) > 2_000 for value in artifact_refs
        ):
            raise ValueError("Profile Rebuild result accepts one bounded Artifact reference")
        validate_orchestration_result_evidence(evidence)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            claim_id, project_id = self._require_live_claim(
                cursor, task_id=task_id, executor_id=executor_id, lease_token=lease_token
            )
            final_outcome = outcome
            final_error = _bounded(summary)
            if outcome == "completed":
                try:
                    replacement_type = evidence.get("artifact_type")
                    if not isinstance(replacement_type, str) or not replacement_type.strip():
                        raise ValueError("completed result evidence requires artifact_type")
                    if len(artifact_refs) != 1:
                        raise ValueError("completed result requires exactly one Artifact reference")
                    validation = self._validator.validate(
                        cursor,
                        request_id=task_id,
                        project_id=project_id,
                        replacement_type=replacement_type,
                        replacement_id=artifact_refs[0],
                    )
                    self._accept_replacement_locked(
                        cursor,
                        request_id=task_id,
                        project_id=project_id,
                        executor_id=executor_id,
                        replacement_type=replacement_type,
                        replacement_id=artifact_refs[0],
                        validation=validation,
                    )
                    final_error = ""
                except ValueError as error:
                    final_outcome = "blocked"
                    final_error = _bounded(f"Canonical validation failed: {error}")
            cursor.execute(
                """
                UPDATE profile_rebuild_claims
                SET status = 'completed', released_at = now(), release_reason = %s
                WHERE profile_rebuild_claim_id = %s
                """,
                (f"result:{final_outcome}", claim_id),
            )
            if final_outcome != "completed":
                cursor.execute(
                    """
                    UPDATE profile_rebuild_requests
                    SET status = %s, completed_at = now(), last_error = %s, updated_at = now()
                    WHERE profile_rebuild_request_id = %s
                    """,
                    (final_outcome, final_error, task_id),
                )
                self._block_pending_batch_locked(cursor, task_id, final_error)
            self._append_event(
                cursor,
                request_id=task_id,
                project_id=project_id,
                event_type=final_outcome,
                actor=executor_id,
                payload={
                    "claim_id": claim_id,
                    "summary": _bounded(summary),
                    "artifact_refs": list(artifact_refs),
                    "canonical_validation_passed": final_outcome == "completed",
                },
            )
            self._refresh_batch_locked(cursor, task_id)
        return self.view(task_id)

    def requeue(
        self, *, task_id: str, project_id: str, actor: str, reason: str
    ) -> dict[str, object]:
        if not actor.strip() or not reason.strip():
            raise ValueError("requeue actor and reason must not be blank")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT profile_rebuild_batch_id, project_id, status
                FROM profile_rebuild_requests
                WHERE profile_rebuild_request_id = %s AND project_id = %s FOR UPDATE
                """,
                (task_id, project_id),
            )
            request = cursor.fetchone()
            if request is None:
                raise ValueError("Profile Rebuild Request does not exist")
            batch_id, request_project_id, status = map(str, request)
            if status not in {"failed", "blocked"}:
                raise ValueError("only failed or blocked Profile Rebuild Request can be requeued")
            cursor.execute(
                """
                SELECT 1 FROM profile_rebuild_batches AS other
                JOIN profile_rebuild_batches AS current
                  ON current.profile_drift_event_id = other.profile_drift_event_id
                 AND current.project_id = other.project_id
                WHERE current.profile_rebuild_batch_id = %s
                  AND other.profile_rebuild_batch_id <> current.profile_rebuild_batch_id
                  AND other.status IN ('requested', 'in_progress')
                """,
                (batch_id,),
            )
            if cursor.fetchone() is not None:
                raise ValueError("a newer active Profile Rebuild Batch already exists")
            cursor.execute(
                """
                SELECT profile_rebuild_request_id
                FROM profile_rebuild_requests
                WHERE profile_rebuild_batch_id = %s AND project_id = %s
                  AND status IN ('failed', 'blocked')
                  AND attempt_count < max_attempts
                  AND NOT EXISTS (
                      SELECT 1 FROM profile_artifact_replacements AS replacement
                      WHERE replacement.profile_rebuild_request_id =
                            profile_rebuild_requests.profile_rebuild_request_id
                  )
                FOR UPDATE
                """,
                (batch_id, request_project_id),
            )
            retry_ids = [str(row[0]) for row in cursor.fetchall()]
            if task_id not in retry_ids:
                raise ValueError("Profile Rebuild Request has exhausted its attempts")
            cursor.execute(
                """
                UPDATE profile_rebuild_requests
                SET status = 'requested', completed_at = NULL, last_error = NULL,
                    updated_at = now()
                WHERE profile_rebuild_request_id = ANY(%s::text[])
                """,
                (retry_ids,),
            )
            for retry_id in retry_ids:
                self._append_event(
                    cursor,
                    request_id=retry_id,
                    project_id=request_project_id,
                    event_type="requeued",
                    actor=actor,
                    payload={"reason": _bounded(reason), "requested_via": task_id},
                )
            cursor.execute(
                """
                UPDATE profile_rebuild_batches
                SET status = 'requested', completed_at = NULL
                WHERE profile_rebuild_batch_id = %s
                """,
                (batch_id,),
            )
        return self.view(task_id)

    def view(self, request_id: str) -> dict[str, object]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT request.profile_rebuild_request_id,
                       request.profile_rebuild_batch_id,
                       request.profile_drift_event_id, request.project_id,
                       request.artifact_type, request.artifact_id,
                       request.rebuild_action, request.phase_order, request.status,
                       request.attempt_count, request.max_attempts,
                       request.lease_seconds, request.last_error,
                       request.requested_by, request.requested_at,
                       request.completed_at, request.updated_at
                FROM profile_rebuild_requests AS request
                WHERE request.profile_rebuild_request_id = %s
                """,
                (request_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("Profile Rebuild Request does not exist")
            cursor.execute(
                """
                SELECT depends_on_request_id
                FROM profile_rebuild_request_dependencies
                WHERE profile_rebuild_request_id = %s
                ORDER BY depends_on_request_id
                """,
                (request_id,),
            )
            dependencies = [str(value[0]) for value in cursor.fetchall()]
            cursor.execute(
                """
                SELECT profile_rebuild_claim_id, executor_kind, executor_id,
                       status, claimed_at, lease_expires_at, released_at, release_reason
                FROM profile_rebuild_claims
                WHERE profile_rebuild_request_id = %s ORDER BY claimed_at
                """,
                (request_id,),
            )
            claims = [
                {
                    "claim_id": str(value[0]),
                    "executor_kind": str(value[1]),
                    "executor_id": str(value[2]),
                    "status": str(value[3]),
                    "claimed_at": _time(value[4]),
                    "lease_expires_at": _time(value[5]),
                    "released_at": _time(value[6]) if value[6] else None,
                    "release_reason": str(value[7]) if value[7] else None,
                }
                for value in cursor.fetchall()
            ]
            cursor.execute(
                """
                SELECT replacement_artifact_type, replacement_artifact_id,
                       validation_evidence, validation_digest, validated_by, validated_at
                FROM profile_artifact_replacements
                WHERE profile_rebuild_request_id = %s
                """,
                (request_id,),
            )
            replacement = cursor.fetchone()
        return {
            "orchestration_task_id": str(row[0]),
            "profile_rebuild_request_id": str(row[0]),
            "profile_rebuild_batch_id": str(row[1]),
            "profile_drift_event_id": str(row[2]),
            "project_id": str(row[3]),
            "artifact_type": str(row[4]),
            "artifact_id": str(row[5]),
            "action": str(row[6]),
            "rebuild_action": str(row[6]),
            "phase_order": int(row[7]),
            "status": str(row[8]),
            "attempt_count": int(row[9]),
            "max_attempts": int(row[10]),
            "lease_seconds": int(row[11]),
            "last_error": str(row[12]) if row[12] else None,
            "requested_by": str(row[13]),
            "requested_at": _time(row[14]),
            "completed_at": _time(row[15]) if row[15] else None,
            "updated_at": _time(row[16]),
            "dependencies": dependencies,
            "claims": claims,
            "replacement": (
                {
                    "artifact_type": str(replacement[0]),
                    "artifact_id": str(replacement[1]),
                    "validation_evidence": cast(dict[str, object], replacement[2]),
                    "validation_digest": str(replacement[3]),
                    "validated_by": str(replacement[4]),
                    "validated_at": _time(replacement[5]),
                }
                if replacement
                else None
            ),
        }

    def _accept_replacement_locked(
        self,
        cursor: Cursor[Any],
        *,
        request_id: str,
        project_id: str,
        executor_id: str,
        replacement_type: str,
        replacement_id: str,
        validation: dict[str, object],
    ) -> None:
        cursor.execute(
            """
            SELECT profile_drift_event_id, artifact_type, artifact_id
            FROM profile_rebuild_requests
            WHERE profile_rebuild_request_id = %s AND project_id = %s
            FOR UPDATE
            """,
            (request_id, project_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError("Profile Rebuild Request does not exist")
        event_id, old_type, old_id = map(str, row)
        encoded = _json(validation)
        cursor.execute(
            """
            INSERT INTO profile_artifact_replacements (
                profile_rebuild_request_id, profile_drift_event_id, project_id,
                replaced_artifact_type, replaced_artifact_id,
                replacement_artifact_type, replacement_artifact_id,
                validation_evidence, validation_digest, validated_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            """,
            (
                request_id,
                event_id,
                project_id,
                old_type,
                old_id,
                replacement_type,
                replacement_id,
                encoded,
                _digest(encoded),
                executor_id,
            ),
        )
        cursor.execute(
            """
            UPDATE profile_rebuild_requests
            SET status = 'completed', completed_at = now(), last_error = NULL,
                updated_at = now()
            WHERE profile_rebuild_request_id = %s
            """,
            (request_id,),
        )
        cursor.execute(
            """
            UPDATE profile_drift_impacts SET resolved_at = now()
            WHERE profile_drift_event_id = %s AND project_id = %s
              AND artifact_type = %s AND artifact_id = %s
              AND resolved_at IS NULL
            """,
            (event_id, project_id, old_type, old_id),
        )
        cursor.execute(
            """
            UPDATE profile_drift_events AS event
            SET status = 'resolved', resolved_at = now()
            WHERE event.profile_drift_event_id = %s AND event.project_id = %s
              AND NOT EXISTS (
                  SELECT 1 FROM profile_drift_impacts AS impact
                  WHERE impact.profile_drift_event_id = event.profile_drift_event_id
                    AND impact.project_id = event.project_id
                    AND impact.resolved_at IS NULL
              )
            """,
            (event_id, project_id),
        )

    def _require_worker_locked(
        self,
        cursor: Cursor[Any],
        *,
        executor_kind: str,
        executor_id: str,
        capabilities: tuple[str, ...],
        worker_token: str,
        project_id: str | None,
    ) -> str | None:
        cursor.execute(
            """
            SELECT capabilities, project_id, max_concurrent_tasks, status,
                   lease_expires_at > now(), credential_digest
            FROM orchestration_worker_registrations
            WHERE executor_kind = %s AND executor_id = %s FOR UPDATE
            """,
            (executor_kind, executor_id),
        )
        row = cursor.fetchone()
        if row is None or not secrets.compare_digest(str(row[5]), _digest(worker_token)):
            raise ValueError("Orchestration Worker credential is invalid")
        registered_capabilities = tuple(str(value) for value in row[0])
        registered_project = str(row[1]) if row[1] is not None else None
        if str(row[3]) != "online" or not bool(row[4]):
            raise ValueError("Orchestration Worker is not accepting new Tasks")
        if set(capabilities) != set(registered_capabilities):
            raise ValueError("executor capabilities differ from Worker registration")
        if registered_project is not None and project_id not in {None, registered_project}:
            raise ValueError("executor project differs from Worker registration")
        cursor.execute(
            """
            SELECT (
                SELECT count(*) FROM orchestration_task_claims
                WHERE executor_kind = %s AND executor_id = %s
                  AND status = 'active' AND lease_expires_at > now()
            ) + (
                SELECT count(*) FROM profile_rebuild_claims
                WHERE executor_kind = %s AND executor_id = %s
                  AND status = 'active' AND lease_expires_at > now()
            )
            """,
            (executor_kind, executor_id, executor_kind, executor_id),
        )
        active = cursor.fetchone()
        if active is not None and int(active[0]) >= int(row[2]):
            raise ValueError("Orchestration Worker concurrency limit is exhausted")
        return registered_project or project_id

    def _require_live_claim(
        self, cursor: Cursor[Any], *, task_id: str, executor_id: str, lease_token: str
    ) -> tuple[str, str]:
        _validate_token(lease_token, "lease")
        self._expire_leases_locked(cursor, request_id=task_id)
        cursor.execute(
            """
            SELECT profile_rebuild_claim_id, project_id, executor_id, lease_token_digest
            FROM profile_rebuild_claims
            WHERE profile_rebuild_request_id = %s AND status = 'active'
            FOR UPDATE
            """,
            (task_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError("Profile Rebuild Request has no active lease")
        if str(row[2]) != executor_id or not secrets.compare_digest(
            str(row[3]), _digest(lease_token)
        ):
            raise ValueError("Profile Rebuild Request lease does not belong to executor")
        return str(row[0]), str(row[1])

    def _expire_leases_locked(self, cursor: Cursor[Any], *, request_id: str | None = None) -> None:
        cursor.execute(
            """
            SELECT claim.profile_rebuild_claim_id,
                   claim.profile_rebuild_request_id, claim.project_id,
                   claim.executor_id, request.attempt_count, request.max_attempts
            FROM profile_rebuild_claims AS claim
            JOIN profile_rebuild_requests AS request
              ON request.profile_rebuild_request_id = claim.profile_rebuild_request_id
             AND request.project_id = claim.project_id
            WHERE claim.status = 'active' AND claim.lease_expires_at <= now()
              AND (%s::text IS NULL OR claim.profile_rebuild_request_id = %s)
            FOR UPDATE OF claim, request
            """,
            (request_id, request_id),
        )
        for claim_id, expired_id, project_id, executor_id, attempts, maximum in cursor.fetchall():
            terminal = int(attempts) >= int(maximum)
            status = "failed" if terminal else "requested"
            cursor.execute(
                """
                UPDATE profile_rebuild_claims
                SET status = 'expired', released_at = now(), release_reason = 'lease_expired'
                WHERE profile_rebuild_claim_id = %s
                """,
                (claim_id,),
            )
            cursor.execute(
                """
                UPDATE profile_rebuild_requests
                SET status = %s,
                    completed_at = CASE WHEN %s THEN now() ELSE NULL END,
                    last_error = CASE WHEN %s THEN 'Worker lease expired after final attempt'
                                      ELSE NULL END,
                    updated_at = now()
                WHERE profile_rebuild_request_id = %s
                """,
                (status, terminal, terminal, expired_id),
            )
            self._append_event(
                cursor,
                request_id=str(expired_id),
                project_id=str(project_id),
                event_type="lease_expired" if not terminal else "failed",
                actor=str(executor_id),
                payload={"claim_id": str(claim_id), "attempts_exhausted": terminal},
            )
            if terminal:
                self._block_pending_batch_locked(
                    cursor, str(expired_id), "A preceding rebuild exhausted its attempts"
                )
            self._refresh_batch_locked(cursor, str(expired_id))

    def _block_pending_batch_locked(
        self, cursor: Cursor[Any], request_id: str, reason: str
    ) -> None:
        cursor.execute(
            """
            SELECT profile_rebuild_batch_id, project_id
            FROM profile_rebuild_requests WHERE profile_rebuild_request_id = %s
            """,
            (request_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return
        batch_id, project_id = str(row[0]), str(row[1])
        cursor.execute(
            """
            UPDATE profile_rebuild_requests
            SET status = 'blocked', completed_at = now(),
                last_error = %s, updated_at = now()
            WHERE profile_rebuild_batch_id = %s AND project_id = %s
              AND status = 'requested'
            RETURNING profile_rebuild_request_id
            """,
            (_bounded(f"Blocked by preceding phase: {reason}"), batch_id, project_id),
        )
        for blocked in cursor.fetchall():
            self._append_event(
                cursor,
                request_id=str(blocked[0]),
                project_id=project_id,
                event_type="blocked",
                actor="profile-rebuild-queue",
                payload={"blocked_by": request_id, "reason": _bounded(reason)},
            )

    def _refresh_batch_locked(self, cursor: Cursor[Any], request_id: str) -> None:
        cursor.execute(
            """
            SELECT profile_rebuild_batch_id FROM profile_rebuild_requests
            WHERE profile_rebuild_request_id = %s
            """,
            (request_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return
        batch_id = str(row[0])
        cursor.execute(
            """
            SELECT
                bool_and(status = 'completed'),
                bool_or(status = 'failed'),
                bool_or(status = 'blocked'),
                bool_or(status = 'in_progress')
            FROM profile_rebuild_requests
            WHERE profile_rebuild_batch_id = %s
            """,
            (batch_id,),
        )
        aggregate = cursor.fetchone()
        if aggregate is None:
            return
        if bool(aggregate[0]):
            status = "completed"
        elif bool(aggregate[1]):
            status = "failed"
        elif bool(aggregate[2]):
            status = "blocked"
        elif bool(aggregate[3]):
            status = "in_progress"
        else:
            status = "requested"
        cursor.execute(
            """
            UPDATE profile_rebuild_batches
            SET status = %s,
                completed_at = CASE
                    WHEN %s IN ('completed', 'failed', 'blocked') THEN now() ELSE NULL END
            WHERE profile_rebuild_batch_id = %s
            """,
            (status, status, batch_id),
        )

    @staticmethod
    def _append_event(
        cursor: Cursor[Any],
        *,
        request_id: str,
        project_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, object],
    ) -> None:
        encoded = _json(payload)
        cursor.execute(
            """
            SELECT COALESCE(max(sequence), 0) + 1 FROM profile_rebuild_events
            WHERE profile_rebuild_request_id = %s
            """,
            (request_id,),
        )
        row = cursor.fetchone()
        sequence = int(row[0]) if row else 1
        event_id = f"profile-rebuild-event-{secrets.token_hex(16)}"
        cursor.execute(
            """
            INSERT INTO profile_rebuild_events (
                profile_rebuild_event_id, profile_rebuild_request_id, project_id,
                sequence, event_type, actor, payload, payload_digest
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                event_id,
                request_id,
                project_id,
                sequence,
                event_type,
                actor,
                encoded,
                _digest(encoded),
            ),
        )


def _validate_executor(kind: str, executor_id: str) -> None:
    if kind not in {"agent", "subagent"}:
        raise ValueError("executor kind is invalid")
    if not executor_id.strip() or len(executor_id) > 160:
        raise ValueError("executor ID must be non-blank and bounded")


def _validate_capabilities(capabilities: tuple[str, ...]) -> None:
    if not capabilities or any(not value.strip() or len(value) > 160 for value in capabilities):
        raise ValueError("executor capabilities must be non-blank and bounded")
    if len(set(capabilities)) != len(capabilities):
        raise ValueError("executor capabilities must be unique")


def _validate_token(value: str, label: str) -> None:
    if not value.strip() or len(value) > 500:
        raise ValueError(f"{label} token must be non-blank and bounded")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _bounded(value: str) -> str:
    text = value.strip()
    return text[:10_000] if text else "Profile rebuild failed"


def _time(value: object) -> str:
    return cast(datetime, value).isoformat()


__all__ = ["ProfileRebuildTaskQueue"]
