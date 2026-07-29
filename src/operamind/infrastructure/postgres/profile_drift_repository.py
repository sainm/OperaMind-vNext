"""Canonical Profile registry views and fail-closed drift propagation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from psycopg import Connection, Cursor

from operamind.infrastructure.postgres.errors import PersistenceConflictError


@dataclass(frozen=True, slots=True)
class ProfileDriftDetectionResult:
    drift_event_id: str | None
    created: bool
    impact_count: int


@dataclass(frozen=True, slots=True)
class ProfileRebuildScheduleResult:
    created: bool
    batch_id: str
    requested_request_id: str
    request_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Impact:
    layer: str
    artifact_type: str
    artifact_id: str
    status: str
    action: str


_PROFILE_LABELS = {
    "DocumentConventionProfile": "文書ルール",
    "DocumentRelationProfile": "文書関係ルール",
    "EmbeddingProfile": "Embedding",
    "CodeFrameworkProfile": "コードフレームワーク",
    "CommandExecutionProfile": "コマンド",
}


class ProfileDriftRepository:
    """Detect dependencies from Canonical ledgers and persist effective drift state."""

    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def detect_activation(
        self,
        *,
        activation_event_id: str,
        cursor: Cursor[Any] | None = None,
    ) -> ProfileDriftDetectionResult:
        """Detect one activation's complete downstream impact in the caller transaction."""

        if not activation_event_id.strip():
            raise ValueError("activation_event_id must not be blank")
        if cursor is not None:
            return self._detect(cursor, activation_event_id)
        with self._connection.transaction(), self._connection.cursor() as owned:
            return self._detect(owned, activation_event_id)

    def _detect(self, cursor: Cursor[Any], activation_event_id: str) -> ProfileDriftDetectionResult:
        cursor.execute(
            """
            SELECT event.project_id, event.binding_key,
                   event.previous_profile_version_id,
                   event.activated_profile_version_id,
                   previous.profile_type
            FROM profile_activation_events AS event
            LEFT JOIN profile_versions AS previous
              ON previous.profile_version_id = event.previous_profile_version_id
            WHERE event.activation_event_id = %s
            FOR SHARE OF event
            """,
            (activation_event_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError("Profile activation event does not exist")
        project_id, binding_key = str(row[0]), str(row[1])
        previous_version_id = str(row[2]) if row[2] is not None else None
        activated_version_id = str(row[3])
        if previous_version_id is None or previous_version_id == activated_version_id:
            return ProfileDriftDetectionResult(None, False, 0)
        profile_type = str(row[4])
        drift_event_id = _stable_id("profile-drift", activation_event_id)
        expected = (
            activation_event_id,
            project_id,
            binding_key,
            previous_version_id,
            activated_version_id,
        )
        cursor.execute(
            """
            SELECT activation_event_id, project_id, binding_key,
                   previous_profile_version_id, activated_profile_version_id
            FROM profile_drift_events
            WHERE profile_drift_event_id = %s
            """,
            (drift_event_id,),
        )
        existing = cursor.fetchone()
        if existing is not None:
            if tuple(existing) != expected:
                raise PersistenceConflictError(
                    f"Profile Drift Event identity has different content: {drift_event_id}"
                )
            cursor.execute(
                """
                SELECT count(*) FROM profile_drift_impacts
                WHERE profile_drift_event_id = %s
                """,
                (drift_event_id,),
            )
            count_row = cursor.fetchone()
            return ProfileDriftDetectionResult(
                drift_event_id, False, int(count_row[0]) if count_row else 0
            )

        impacts = self._discover_impacts(
            cursor,
            project_id=project_id,
            profile_type=profile_type,
            profile_version_id=previous_version_id,
        )
        cursor.execute(
            """
            INSERT INTO profile_drift_events (
                profile_drift_event_id, activation_event_id, project_id,
                binding_key, previous_profile_version_id,
                activated_profile_version_id, status, resolved_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                drift_event_id,
                *expected,
                "open" if impacts else "resolved",
                None if impacts else datetime.now().astimezone(),
            ),
        )
        reason = (
            f"{_PROFILE_LABELS.get(profile_type, profile_type)} Profile "
            f"{previous_version_id} → {activated_version_id}"
        )
        for impact in sorted(
            impacts.values(), key=lambda item: (item.layer, item.artifact_type, item.artifact_id)
        ):
            cursor.execute(
                """
                INSERT INTO profile_drift_impacts (
                    profile_drift_event_id, project_id, affected_layer,
                    artifact_type, artifact_id, effective_status, reason,
                    rebuild_action
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    drift_event_id,
                    project_id,
                    impact.layer,
                    impact.artifact_type,
                    impact.artifact_id,
                    impact.status,
                    reason,
                    impact.action,
                ),
            )
        return ProfileDriftDetectionResult(drift_event_id, True, len(impacts))

    def _discover_impacts(
        self,
        cursor: Cursor[Any],
        *,
        project_id: str,
        profile_type: str,
        profile_version_id: str,
    ) -> dict[tuple[str, str], _Impact]:
        impacts: dict[tuple[str, str], _Impact] = {}
        document_snapshot_ids: set[str] = set()
        graph_ids: set[str] = set()
        impact_ids: set[str] = set()
        orchestration_ids: set[str] = set()
        grant_ids: set[str] = set()
        edit_result_ids: set[str] = set()
        test_data_result_ids: set[str] = set()
        ui_result_ids: set[str] = set()

        if profile_type == "DocumentConventionProfile":
            cursor.execute(
                """
                SELECT DISTINCT document_snapshot_id FROM snapshot_memberships
                WHERE project_id = %s AND profile_version_id = %s
                """,
                (project_id, profile_version_id),
            )
            document_snapshot_ids.update(str(row[0]) for row in cursor.fetchall())
        elif profile_type == "DocumentRelationProfile":
            cursor.execute(
                """
                SELECT DISTINCT document_snapshot_id FROM document_relation_builds
                WHERE project_id = %s AND relation_profile_version_id = %s
                """,
                (project_id, profile_version_id),
            )
            document_snapshot_ids.update(str(row[0]) for row in cursor.fetchall())
        elif profile_type == "EmbeddingProfile":
            cursor.execute(
                """
                SELECT search_index_build_id, document_snapshot_id
                FROM search_index_builds
                WHERE project_id = %s AND embedding_profile_version_id = %s
                """,
                (project_id, profile_version_id),
            )
            for build_id, snapshot_id in cursor.fetchall():
                _put(
                    impacts,
                    _Impact(
                        "snapshot",
                        "SearchIndexBuild",
                        str(build_id),
                        "stale",
                        "rebuild_search_index",
                    ),
                )
                document_snapshot_ids.add(str(snapshot_id))
        elif profile_type == "CodeFrameworkProfile":
            cursor.execute(
                """
                SELECT DISTINCT code_graph_snapshot_id
                FROM code_graph_snapshot_profiles
                WHERE project_id = %s AND profile_version_id = %s
                """,
                (project_id, profile_version_id),
            )
            graph_ids.update(str(row[0]) for row in cursor.fetchall())
        elif profile_type == "CommandExecutionProfile":
            cursor.execute(
                """
                SELECT approval_grant_id FROM approval_grants
                WHERE project_id = %s AND command_profile_version_id = %s
                """,
                (project_id, profile_version_id),
            )
            grant_ids.update(str(row[0]) for row in cursor.fetchall())
        for snapshot_id in document_snapshot_ids:
            _put(
                impacts,
                _Impact(
                    "snapshot",
                    "DocumentSnapshot",
                    snapshot_id,
                    "stale",
                    "rebuild_document_snapshot",
                ),
            )
        for graph_id in graph_ids:
            _put(
                impacts,
                _Impact("snapshot", "CodeGraphSnapshot", graph_id, "stale", "rebuild_code_graph"),
            )

        cursor.execute(
            """
            SELECT impact_report_id, document_snapshot_id, code_graph_snapshot_id
            FROM impact_reports WHERE project_id = %s
            """,
            (project_id,),
        )
        for report_id, snapshot_id, graph_id in cursor.fetchall():
            if str(snapshot_id) in document_snapshot_ids or str(graph_id) in graph_ids:
                impact_ids.add(str(report_id))
        for report_id in impact_ids:
            _put(
                impacts,
                _Impact("impact", "ImpactReport", report_id, "blocked", "rerun_impact_analysis"),
            )

        cursor.execute(
            """
            SELECT orchestration_id, impact_report_id, test_plan_id
            FROM change_orchestrations WHERE project_id = %s
            """,
            (project_id,),
        )
        for orchestration_id, report_id, test_plan_id in cursor.fetchall():
            if str(report_id) in impact_ids:
                orchestration_ids.add(str(orchestration_id))
                _put(
                    impacts,
                    _Impact(
                        "test_plan",
                        "TestPlan",
                        str(test_plan_id),
                        "blocked",
                        "regenerate_test_plan",
                    ),
                )
        cursor.execute(
            """
            SELECT result.edit_result_id, packet.impact_report_id,
                   result.approval_grant_id
            FROM edit_results AS result
            JOIN edit_packets AS packet
              ON packet.edit_packet_id = result.edit_packet_id
             AND packet.project_id = result.project_id
            WHERE result.project_id = %s
            """,
            (project_id,),
        )
        for result_id, report_id, grant_id in cursor.fetchall():
            if str(report_id) in impact_ids or (
                grant_id is not None and str(grant_id) in grant_ids
            ):
                edit_result_ids.add(str(result_id))
                _put(
                    impacts,
                    _Impact(
                        "evidence", "EditResult", str(result_id), "stale", "rerun_code_verification"
                    ),
                )

        cursor.execute(
            """
            SELECT execution_result_id, orchestration_id, approval_grant_id, run_id
            FROM test_data_execution_runs WHERE project_id = %s
            """,
            (project_id,),
        )
        test_run_ids: set[str] = set()
        for result_id, orchestration_id, grant_id, run_id in cursor.fetchall():
            if str(orchestration_id) in orchestration_ids or str(grant_id) in grant_ids:
                test_data_result_ids.add(str(result_id))
                test_run_ids.add(str(run_id))
                _put(
                    impacts,
                    _Impact(
                        "evidence",
                        "TestDataExecutionResult",
                        str(result_id),
                        "stale",
                        "rerun_test_data",
                    ),
                )

        cursor.execute(
            """
            SELECT artifact_id,
                   payload ->> 'orchestration_id',
                   payload ->> 'test_data_execution_result_id'
            FROM artifact_records
            WHERE project_id = %s
              AND artifact_type = 'UiVerificationResult'
              AND schema_version = 'v2'
            """,
            (project_id,),
        )
        for result_id, orchestration_id, data_result_id in cursor.fetchall():
            if (
                str(orchestration_id) in orchestration_ids
                or str(data_result_id) in test_data_result_ids
            ):
                ui_result_ids.add(str(result_id))
                _put(
                    impacts,
                    _Impact(
                        "evidence",
                        "UiVerificationResult",
                        str(result_id),
                        "stale",
                        "rerun_ui_verification",
                    ),
                )

        self._add_evidence_rows(cursor, project_id, test_run_ids, impacts)
        self._add_command_evidence(cursor, project_id, grant_ids, impacts)

        cursor.execute(
            """
            SELECT closure_result_id, orchestration_id, edit_result_id,
                   test_data_execution_result_id, ui_verification_result_id
            FROM change_closure_results WHERE project_id = %s
            """,
            (project_id,),
        )
        for closure_id, orchestration_id, edit_id, data_id, ui_id in cursor.fetchall():
            if (
                str(orchestration_id) in orchestration_ids
                or (edit_id is not None and str(edit_id) in edit_result_ids)
                or (data_id is not None and str(data_id) in test_data_result_ids)
                or (ui_id is not None and str(ui_id) in ui_result_ids)
            ):
                _put(
                    impacts,
                    _Impact(
                        "closure",
                        "ChangeClosureResult",
                        str(closure_id),
                        "blocked",
                        "regenerate_change_closure",
                    ),
                )
        return impacts

    @staticmethod
    def _add_evidence_rows(
        cursor: Cursor[Any],
        project_id: str,
        test_run_ids: set[str],
        impacts: dict[tuple[str, str], _Impact],
    ) -> None:
        if test_run_ids:
            cursor.execute(
                """
                SELECT evidence_id, run_id
                FROM test_data_execution_evidence
                WHERE project_id = %s
                """,
                (project_id,),
            )
            for evidence_id, run_id in cursor.fetchall():
                if str(run_id) in test_run_ids:
                    _put(
                        impacts,
                        _Impact(
                            "evidence", "Evidence", str(evidence_id), "stale", "rerun_test_data"
                        ),
                    )

    @staticmethod
    def _add_command_evidence(
        cursor: Cursor[Any],
        project_id: str,
        grant_ids: set[str],
        impacts: dict[tuple[str, str], _Impact],
    ) -> None:
        if not grant_ids:
            return
        cursor.execute(
            """
            SELECT result.command_execution_id, request.approval_grant_id
            FROM command_execution_results AS result
            JOIN command_execution_requests AS request
              ON request.command_execution_id = result.command_execution_id
             AND request.project_id = result.project_id
            WHERE result.project_id = %s
            """,
            (project_id,),
        )
        for execution_id, grant_id in cursor.fetchall():
            if str(grant_id) in grant_ids:
                _put(
                    impacts,
                    _Impact(
                        "evidence",
                        "CommandExecutionResult",
                        str(execution_id),
                        "stale",
                        "rerun_command",
                    ),
                )

    def management_view(self, *, project_id: str) -> dict[str, object]:
        if not project_id.strip():
            raise ValueError("project_id must not be blank")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT version.profile_version_id, version.profile_type,
                       version.profile_id, version.semantic_version,
                       version.created_at,
                       EXISTS (
                           SELECT 1 FROM project_profile_bindings AS binding
                           WHERE binding.project_id = %s
                             AND binding.active_profile_version_id = version.profile_version_id
                       )
                FROM profile_versions AS version
                ORDER BY version.profile_type, version.profile_id,
                         version.semantic_version, version.profile_version_id
                """,
                (project_id,),
            )
            versions = [
                {
                    "profile_version_id": str(row[0]),
                    "profile_type": str(row[1]),
                    "profile_id": str(row[2]),
                    "semantic_version": str(row[3]),
                    "created_at": cast(datetime, row[4]).isoformat(),
                    "active": bool(row[5]),
                }
                for row in cursor.fetchall()
            ]
            cursor.execute(
                """
                SELECT binding.binding_key, binding.active_profile_version_id,
                       version.profile_type, version.profile_id,
                       version.semantic_version, binding.activated_by,
                       binding.activated_at
                FROM project_profile_bindings AS binding
                JOIN profile_versions AS version
                  ON version.profile_version_id = binding.active_profile_version_id
                WHERE binding.project_id = %s
                ORDER BY version.profile_type, binding.binding_key
                """,
                (project_id,),
            )
            bindings = [
                {
                    "binding_key": str(row[0]),
                    "profile_version_id": str(row[1]),
                    "profile_type": str(row[2]),
                    "profile_id": str(row[3]),
                    "semantic_version": str(row[4]),
                    "activated_by": str(row[5]),
                    "activated_at": cast(datetime, row[6]).isoformat(),
                }
                for row in cursor.fetchall()
            ]
            cursor.execute(
                """
                SELECT event.profile_drift_event_id, event.binding_key,
                       event.previous_profile_version_id,
                       event.activated_profile_version_id, event.status,
                       event.detected_at, impact.affected_layer,
                       impact.artifact_type, impact.artifact_id,
                       impact.effective_status, impact.reason,
                       impact.rebuild_action, impact.resolved_at
                FROM profile_drift_events AS event
                LEFT JOIN profile_drift_impacts AS impact
                  ON impact.profile_drift_event_id = event.profile_drift_event_id
                WHERE event.project_id = %s
                ORDER BY event.detected_at DESC, event.profile_drift_event_id,
                         impact.affected_layer, impact.artifact_type, impact.artifact_id
                """,
                (project_id,),
            )
            events: dict[str, dict[str, object]] = {}
            for row in cursor.fetchall():
                event_id = str(row[0])
                event = events.setdefault(
                    event_id,
                    {
                        "drift_event_id": event_id,
                        "binding_key": str(row[1]),
                        "previous_profile_version_id": str(row[2]),
                        "activated_profile_version_id": str(row[3]),
                        "status": str(row[4]),
                        "detected_at": cast(datetime, row[5]).isoformat(),
                        "impacts": [],
                    },
                )
                if row[6] is not None:
                    cast(list[object], event["impacts"]).append(
                        {
                            "affected_layer": str(row[6]),
                            "artifact_type": str(row[7]),
                            "artifact_id": str(row[8]),
                            "effective_status": str(row[9]),
                            "reason": str(row[10]),
                            "rebuild_action": str(row[11]),
                            "resolved": row[12] is not None,
                        }
                    )
            cursor.execute(
                """
                SELECT profile_rebuild_batch_id, profile_drift_event_id,
                       status, requested_by, requested_at, completed_at,
                       (SELECT count(*) FROM profile_rebuild_requests AS request
                        WHERE request.profile_rebuild_batch_id = batch.profile_rebuild_batch_id),
                       (SELECT count(*) FROM profile_rebuild_requests AS request
                        WHERE request.profile_rebuild_batch_id = batch.profile_rebuild_batch_id
                          AND request.status = 'completed')
                FROM profile_rebuild_batches AS batch
                WHERE project_id = %s
                ORDER BY requested_at DESC, profile_rebuild_batch_id
                """,
                (project_id,),
            )
            batches = [
                {
                    "rebuild_batch_id": str(row[0]),
                    "drift_event_id": str(row[1]),
                    "status": str(row[2]),
                    "requested_by": str(row[3]),
                    "requested_at": cast(datetime, row[4]).isoformat(),
                    "completed_at": cast(datetime, row[5]).isoformat() if row[5] else None,
                    "request_count": int(row[6]),
                    "completed_count": int(row[7]),
                }
                for row in cursor.fetchall()
            ]
            cursor.execute(
                """
                SELECT request.profile_rebuild_request_id,
                       request.profile_rebuild_batch_id,
                       request.profile_drift_event_id, request.artifact_type,
                       request.artifact_id, request.rebuild_action,
                       request.phase_order, request.status, request.attempt_count,
                       request.max_attempts, request.last_error,
                       request.requested_by, request.requested_at,
                       request.completed_at,
                       (SELECT count(*)
                        FROM profile_rebuild_request_dependencies AS dependency
                        WHERE dependency.profile_rebuild_request_id =
                              request.profile_rebuild_request_id),
                       replacement.replacement_artifact_type,
                       replacement.replacement_artifact_id,
                       replacement.validation_evidence,
                       replacement.validated_by,
                       replacement.validated_at
                FROM profile_rebuild_requests AS request
                LEFT JOIN profile_artifact_replacements AS replacement
                  ON replacement.profile_rebuild_request_id =
                     request.profile_rebuild_request_id
                 AND replacement.project_id = request.project_id
                WHERE request.project_id = %s
                ORDER BY request.requested_at DESC, request.profile_rebuild_batch_id,
                         request.phase_order, request.artifact_type, request.artifact_id
                """,
                (project_id,),
            )
            requests = [
                {
                    "rebuild_request_id": str(row[0]),
                    "rebuild_batch_id": str(row[1]),
                    "drift_event_id": str(row[2]),
                    "artifact_type": str(row[3]),
                    "artifact_id": str(row[4]),
                    "rebuild_action": str(row[5]),
                    "phase_order": int(row[6]),
                    "status": str(row[7]),
                    "attempt_count": int(row[8]),
                    "max_attempts": int(row[9]),
                    "last_error": str(row[10]) if row[10] else None,
                    "requested_by": str(row[11]),
                    "requested_at": cast(datetime, row[12]).isoformat(),
                    "completed_at": cast(datetime, row[13]).isoformat() if row[13] else None,
                    "dependency_count": int(row[14]),
                    "replacement": (
                        {
                            "artifact_type": str(row[15]),
                            "artifact_id": str(row[16]),
                            "validation_evidence": cast(dict[str, object], row[17]),
                            "validated_by": str(row[18]),
                            "validated_at": cast(datetime, row[19]).isoformat(),
                        }
                        if row[15] is not None
                        else None
                    ),
                }
                for row in cursor.fetchall()
            ]
        drift_events = list(events.values())
        open_impact_count = sum(
            not bool(cast(dict[str, object], impact)["resolved"])
            for event in drift_events
            for impact in cast(list[object], event["impacts"])
        )
        return {
            "project_id": project_id,
            "profile_versions": versions,
            "bindings": bindings,
            "drift_events": drift_events,
            "rebuild_batches": batches,
            "rebuild_requests": requests,
            "open_drift_count": sum(event["status"] == "open" for event in drift_events),
            "open_impact_count": open_impact_count,
        }

    def request_rebuild(
        self,
        *,
        rebuild_request_id: str,
        project_id: str,
        drift_event_id: str,
        artifact_type: str,
        artifact_id: str,
        requested_by: str,
    ) -> ProfileRebuildScheduleResult:
        values = (
            rebuild_request_id,
            project_id,
            drift_event_id,
            artifact_type,
            artifact_id,
            requested_by,
        )
        if any(not value.strip() for value in values):
            raise ValueError("Profile rebuild request fields must not be blank")
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT rebuild_action FROM profile_drift_impacts
                WHERE profile_drift_event_id = %s AND project_id = %s
                  AND artifact_type = %s AND artifact_id = %s
                  AND resolved_at IS NULL
                FOR SHARE
                """,
                (drift_event_id, project_id, artifact_type, artifact_id),
            )
            impact = cursor.fetchone()
            if impact is None:
                raise ValueError("Open Profile drift impact does not exist")
            cursor.execute(
                """
                SELECT profile_rebuild_batch_id
                FROM profile_rebuild_batches
                WHERE profile_drift_event_id = %s AND project_id = %s
                  AND status IN ('requested', 'in_progress')
                FOR UPDATE
                """,
                (drift_event_id, project_id),
            )
            active = cursor.fetchone()
            if active is not None:
                batch_id = str(active[0])
                cursor.execute(
                    """
                    SELECT profile_rebuild_request_id, artifact_type, artifact_id
                    FROM profile_rebuild_requests
                    WHERE profile_rebuild_batch_id = %s AND project_id = %s
                    ORDER BY phase_order, artifact_type, artifact_id
                    """,
                    (batch_id, project_id),
                )
                rows = cursor.fetchall()
                requested_id = next(
                    (
                        str(row[0])
                        for row in rows
                        if str(row[1]) == artifact_type and str(row[2]) == artifact_id
                    ),
                    rebuild_request_id,
                )
                return ProfileRebuildScheduleResult(
                    False, batch_id, requested_id, tuple(str(row[0]) for row in rows)
                )

            batch_id = _stable_id("profile-rebuild-batch", rebuild_request_id)
            cursor.execute(
                """
                INSERT INTO profile_rebuild_batches (
                    profile_rebuild_batch_id, profile_drift_event_id, project_id,
                    status, requested_by
                ) VALUES (%s, %s, %s, 'requested', %s)
                """,
                (batch_id, drift_event_id, project_id, requested_by),
            )
            cursor.execute(
                """
                SELECT affected_layer, artifact_type, artifact_id, rebuild_action
                FROM profile_drift_impacts
                WHERE profile_drift_event_id = %s AND project_id = %s
                  AND resolved_at IS NULL
                ORDER BY CASE affected_layer
                    WHEN 'snapshot' THEN 10 WHEN 'impact' THEN 20
                    WHEN 'test_plan' THEN 30 WHEN 'evidence' THEN 40
                    WHEN 'closure' THEN 50 END,
                    artifact_type, artifact_id
                """,
                (drift_event_id, project_id),
            )
            impacts = cursor.fetchall()
            if not impacts:
                raise ValueError("Open Profile drift impact does not exist")
            request_rows: list[tuple[str, int]] = []
            requested_request_id = rebuild_request_id
            for layer, row_type, row_id, action in impacts:
                current_type, current_id = str(row_type), str(row_id)
                request_id = (
                    rebuild_request_id
                    if current_type == artifact_type and current_id == artifact_id
                    else _stable_id("profile-rebuild", batch_id, current_type, current_id)
                )
                phase_order = _phase_order(str(layer))
                cursor.execute(
                    """
                    INSERT INTO profile_rebuild_requests (
                        profile_rebuild_request_id, profile_rebuild_batch_id,
                        profile_drift_event_id, project_id, artifact_type,
                        artifact_id, rebuild_action, phase_order, requested_by
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        request_id,
                        batch_id,
                        drift_event_id,
                        project_id,
                        current_type,
                        current_id,
                        str(action),
                        phase_order,
                        requested_by,
                    ),
                )
                request_rows.append((request_id, phase_order))
                self._append_rebuild_event(
                    cursor,
                    request_id=request_id,
                    project_id=project_id,
                    event_type="scheduled",
                    actor=requested_by,
                    payload={"batch_id": batch_id, "phase_order": phase_order},
                )
            for request_id, phase_order in request_rows:
                for parent_id, parent_phase in request_rows:
                    if parent_phase >= phase_order:
                        continue
                    cursor.execute(
                        """
                        INSERT INTO profile_rebuild_request_dependencies (
                            profile_rebuild_request_id, depends_on_request_id, project_id
                        ) VALUES (%s, %s, %s)
                        """,
                        (request_id, parent_id, project_id),
                    )
        return ProfileRebuildScheduleResult(
            True,
            batch_id,
            requested_request_id,
            tuple(request_id for request_id, _phase in request_rows),
        )

    @staticmethod
    def _append_rebuild_event(
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
            SELECT COALESCE(max(sequence), 0) + 1
            FROM profile_rebuild_events
            WHERE profile_rebuild_request_id = %s
            """,
            (request_id,),
        )
        row = cursor.fetchone()
        sequence = int(row[0]) if row else 1
        event_id = _stable_id("profile-rebuild-event", request_id, str(sequence), encoded)
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
                hashlib.sha256(encoded.encode()).hexdigest(),
            ),
        )


def _put(values: dict[tuple[str, str], _Impact], impact: _Impact) -> None:
    key = (impact.artifact_type, impact.artifact_id)
    existing = values.get(key)
    if existing is None or (existing.status == "stale" and impact.status == "blocked"):
        values[key] = impact


def _stable_id(prefix: str, *values: str) -> str:
    digest = hashlib.sha256("\0".join(values).encode()).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _phase_order(layer: str) -> int:
    try:
        return {"snapshot": 10, "impact": 20, "test_plan": 30, "evidence": 40, "closure": 50}[layer]
    except KeyError as error:
        raise ValueError(f"Unsupported Profile rebuild layer: {layer}") from error


def _json(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


__all__ = [
    "ProfileDriftDetectionResult",
    "ProfileDriftRepository",
    "ProfileRebuildScheduleResult",
]
