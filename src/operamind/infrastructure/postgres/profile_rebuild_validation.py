"""Authoritative Canonical validation for Profile Drift replacement artifacts."""

from __future__ import annotations

from typing import Any

from psycopg import Cursor

_CANONICAL_CHECKS: dict[str, str] = {
    "DocumentSnapshot": """
        SELECT status = 'committed' FROM document_snapshots
        WHERE document_snapshot_id = %s AND project_id = %s
    """,
    "SearchIndexBuild": """
        SELECT status = 'ready' AND is_current FROM search_index_builds
        WHERE search_index_build_id = %s AND project_id = %s
    """,
    "CodeGraphSnapshot": """
        SELECT status = 'complete' AND is_current FROM code_graph_snapshots
        WHERE code_graph_snapshot_id = %s AND project_id = %s
    """,
    "UiKnowledgeSnapshot": """
        SELECT review_status = 'approved' AND is_active FROM ui_knowledge_snapshots
        WHERE ui_knowledge_snapshot_id = %s AND project_id = %s
    """,
    "ImpactReport": """
        SELECT status = 'confirmed' FROM impact_reports
        WHERE impact_report_id = %s AND project_id = %s
    """,
    "TestPlan": """
        SELECT record.artifact_type = 'TestPlan' AND orchestration.status = 'ready'
        FROM artifact_records AS record
        JOIN change_orchestrations AS orchestration
          ON orchestration.test_plan_id = record.artifact_id
         AND orchestration.project_id = record.project_id
        WHERE record.artifact_id = %s AND record.project_id = %s
    """,
    "UiExecutionPlan": """
        SELECT status IN ('ready', 'completed') FROM ui_execution_plans
        WHERE ui_execution_plan_id = %s AND project_id = %s
    """,
    "EditResult": """
        SELECT validation_mode = 'committed' AND status = 'in_scope' AND tests_passed
        FROM edit_results WHERE edit_result_id = %s AND project_id = %s
    """,
    "TestDataExecutionResult": """
        SELECT status = 'passed' FROM test_data_execution_runs
        WHERE execution_result_id = %s AND project_id = %s
    """,
    "UiVerificationResult": """
        SELECT status = 'passed'
           AND jsonb_array_length(unresolved_impact_item_ids) = 0
           AND jsonb_array_length(out_of_scope_files) = 0
           AND jsonb_array_length(failure_reasons) = 0
        FROM change_validations
        WHERE verification_result_id = %s AND project_id = %s
    """,
    "CommandExecutionResult": """
        SELECT status = 'passed' FROM command_execution_results
        WHERE command_execution_id = %s AND project_id = %s
    """,
    "ChangeClosureResult": """
        SELECT status = 'passed' AND jsonb_array_length(unresolved_items) = 0
        FROM change_closure_results
        WHERE closure_result_id = %s AND project_id = %s
    """,
}


class ProfileReplacementValidator:
    """Validate a replacement from Canonical tables, never from Worker assertions."""

    def validate(
        self,
        cursor: Cursor[Any],
        *,
        request_id: str,
        project_id: str,
        replacement_type: str,
        replacement_id: str,
    ) -> dict[str, object]:
        cursor.execute(
            """
            SELECT request.artifact_type, request.artifact_id,
                   request.profile_drift_event_id, version.profile_type,
                   event.activated_profile_version_id, version.payload
            FROM profile_rebuild_requests AS request
            JOIN profile_drift_events AS event
              ON event.profile_drift_event_id = request.profile_drift_event_id
             AND event.project_id = request.project_id
            JOIN profile_versions AS version
              ON version.profile_version_id = event.activated_profile_version_id
            WHERE request.profile_rebuild_request_id = %s
              AND request.project_id = %s
            FOR SHARE OF request, event, version
            """,
            (request_id, project_id),
        )
        request = cursor.fetchone()
        if request is None:
            raise ValueError("Profile Rebuild Request does not exist")
        artifact_type, artifact_id, drift_event_id = map(str, request[:3])
        profile_type, profile_version_id = str(request[3]), str(request[4])
        profile_payload = request[5]
        if replacement_type != artifact_type:
            raise ValueError("replacement Artifact type must match the stale Artifact type")
        if replacement_id == artifact_id:
            raise ValueError("replacement Artifact must have a new identity")
        if not replacement_id.strip() or len(replacement_id) > 2_000:
            raise ValueError("replacement Artifact ID must be non-blank and bounded")
        self._require_canonical(
            cursor,
            artifact_type=artifact_type,
            artifact_id=replacement_id,
            project_id=project_id,
        )
        cursor.execute(
            """
            SELECT 1
            FROM profile_drift_impacts AS impact
            JOIN profile_drift_events AS event
              ON event.profile_drift_event_id = impact.profile_drift_event_id
            WHERE impact.project_id = %s AND impact.artifact_type = %s
              AND impact.artifact_id = %s AND impact.resolved_at IS NULL
              AND event.status = 'open'
            LIMIT 1
            """,
            (project_id, artifact_type, replacement_id),
        )
        if cursor.fetchone() is not None:
            raise ValueError("replacement Artifact is already affected by an open Profile Drift")
        self._require_active_profile_binding(
            cursor,
            project_id=project_id,
            artifact_type=artifact_type,
            artifact_id=replacement_id,
            profile_type=profile_type,
            profile_version_id=profile_version_id,
            profile_payload=profile_payload if isinstance(profile_payload, dict) else {},
        )
        cursor.execute(
            """
            SELECT parent.artifact_type, replacement.replacement_artifact_id
            FROM profile_rebuild_request_dependencies AS dependency
            JOIN profile_rebuild_requests AS parent
              ON parent.profile_rebuild_request_id = dependency.depends_on_request_id
             AND parent.project_id = dependency.project_id
            JOIN profile_artifact_replacements AS replacement
              ON replacement.profile_rebuild_request_id = parent.profile_rebuild_request_id
             AND replacement.project_id = parent.project_id
            WHERE dependency.profile_rebuild_request_id = %s
              AND dependency.project_id = %s
            ORDER BY parent.phase_order, parent.artifact_type, parent.artifact_id
            """,
            (request_id, project_id),
        )
        dependency_rows = [(str(row[0]), str(row[1])) for row in cursor.fetchall()]
        dependency_replacements: dict[str, set[str]] = {}
        for dependency_type, dependency_id in dependency_rows:
            dependency_replacements.setdefault(dependency_type, set()).add(dependency_id)
        self._require_dependency_bindings(
            cursor,
            artifact_type=artifact_type,
            artifact_id=replacement_id,
            project_id=project_id,
            dependencies=dependency_replacements,
        )
        dependencies = [f"{kind}:{identifier}" for kind, identifier in dependency_rows]
        return {
            "validator": "canonical-profile-replacement-v1",
            "project_id": project_id,
            "drift_event_id": drift_event_id,
            "profile_type": profile_type,
            "activated_profile_version_id": profile_version_id,
            "replaced_artifact": f"{artifact_type}:{artifact_id}",
            "replacement_artifact": f"{artifact_type}:{replacement_id}",
            "validated_dependency_count": len(dependencies),
            "validated_dependencies": dependencies,
        }

    @staticmethod
    def _require_dependency_bindings(
        cursor: Cursor[Any],
        *,
        artifact_type: str,
        artifact_id: str,
        project_id: str,
        dependencies: dict[str, set[str]],
    ) -> None:
        if artifact_type == "ImpactReport":
            cursor.execute(
                """
                SELECT document_snapshot_id, code_graph_snapshot_id
                FROM impact_reports
                WHERE impact_report_id = %s AND project_id = %s
                """,
                (artifact_id, project_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("replacement Impact Report does not exist")
            _require_reference(
                "Impact Report Document Snapshot", str(row[0]), dependencies.get("DocumentSnapshot")
            )
            _require_reference(
                "Impact Report Code Graph", str(row[1]), dependencies.get("CodeGraphSnapshot")
            )
            return
        if artifact_type == "TestPlan":
            cursor.execute(
                """
                SELECT impact_report_id FROM change_orchestrations
                WHERE test_plan_id = %s AND project_id = %s AND status = 'ready'
                """,
                (artifact_id, project_id),
            )
            rows = cursor.fetchall()
            _require_any_reference(
                "Test Plan Impact Report",
                {str(row[0]) for row in rows},
                dependencies.get("ImpactReport"),
            )
            return
        if artifact_type == "UiExecutionPlan":
            cursor.execute(
                """
                SELECT ui_knowledge_snapshot_id FROM ui_browser_manifests
                WHERE ui_execution_plan_id = %s AND project_id = %s
                """,
                (artifact_id, project_id),
            )
            _require_any_reference(
                "UI Execution Plan Knowledge Snapshot",
                {str(row[0]) for row in cursor.fetchall()},
                dependencies.get("UiKnowledgeSnapshot"),
            )
            return
        if artifact_type == "EditResult":
            cursor.execute(
                """
                SELECT packet.impact_report_id
                FROM edit_results AS result
                JOIN edit_packets AS packet
                  ON packet.edit_packet_id = result.edit_packet_id
                 AND packet.project_id = result.project_id
                WHERE result.edit_result_id = %s AND result.project_id = %s
                """,
                (artifact_id, project_id),
            )
            _require_any_reference(
                "Edit Result Impact Report",
                {str(row[0]) for row in cursor.fetchall()},
                dependencies.get("ImpactReport"),
            )
            return
        if artifact_type == "TestDataExecutionResult":
            cursor.execute(
                """
                SELECT orchestration.test_plan_id
                FROM test_data_execution_runs AS run
                JOIN change_orchestrations AS orchestration
                  ON orchestration.orchestration_id = run.orchestration_id
                 AND orchestration.project_id = run.project_id
                WHERE run.execution_result_id = %s AND run.project_id = %s
                """,
                (artifact_id, project_id),
            )
            _require_any_reference(
                "Test Data Execution Test Plan",
                {str(row[0]) for row in cursor.fetchall()},
                dependencies.get("TestPlan"),
            )
            return
        if artifact_type == "UiVerificationResult":
            cursor.execute(
                """
                SELECT ui_execution_plan_id FROM change_validations
                WHERE verification_result_id = %s AND project_id = %s
                """,
                (artifact_id, project_id),
            )
            _require_any_reference(
                "UI Verification Execution Plan",
                {str(row[0]) for row in cursor.fetchall()},
                dependencies.get("UiExecutionPlan"),
            )
            return
        if artifact_type == "ChangeClosureResult":
            cursor.execute(
                """
                SELECT closure.edit_result_id,
                       closure.test_data_execution_result_id,
                       closure.ui_verification_result_id,
                       orchestration.test_plan_id
                FROM change_closure_results AS closure
                JOIN change_orchestrations AS orchestration
                  ON orchestration.orchestration_id = closure.orchestration_id
                 AND orchestration.project_id = closure.project_id
                WHERE closure.closure_result_id = %s AND closure.project_id = %s
                """,
                (artifact_id, project_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("replacement Change Closure Result does not exist")
            for label, value, dependency_type in (
                ("Closure Edit Result", row[0], "EditResult"),
                ("Closure Test Data Result", row[1], "TestDataExecutionResult"),
                ("Closure UI Verification", row[2], "UiVerificationResult"),
                ("Closure Test Plan", row[3], "TestPlan"),
            ):
                if value is not None:
                    _require_reference(label, str(value), dependencies.get(dependency_type))

    @staticmethod
    def _require_canonical(
        cursor: Cursor[Any], *, artifact_type: str, artifact_id: str, project_id: str
    ) -> None:
        if artifact_type == "Evidence":
            cursor.execute(
                """
                SELECT true
                FROM test_data_execution_evidence AS evidence
                JOIN test_data_execution_runs AS run
                  ON run.run_id = evidence.run_id AND run.project_id = evidence.project_id
                WHERE evidence.evidence_id = %s AND evidence.project_id = %s
                  AND run.status = 'passed'
                UNION ALL
                SELECT true
                FROM ui_execution_evidence AS evidence
                JOIN change_validations AS validation
                  ON validation.ui_execution_run_id = evidence.ui_execution_run_id
                 AND validation.project_id = evidence.project_id
                WHERE evidence.evidence_id = %s AND evidence.project_id = %s
                  AND validation.status = 'passed'
                LIMIT 1
                """,
                (artifact_id, project_id, artifact_id, project_id),
            )
        else:
            query = _CANONICAL_CHECKS.get(artifact_type)
            if query is None:
                raise ValueError(f"unsupported replacement Artifact type: {artifact_type}")
            cursor.execute(query, (artifact_id, project_id))
        row = cursor.fetchone()
        if row is None:
            raise ValueError("replacement Artifact is absent from Canonical Data")
        if not bool(row[0]):
            raise ValueError("replacement Artifact has not passed its Canonical acceptance gate")

    @staticmethod
    def _require_active_profile_binding(
        cursor: Cursor[Any],
        *,
        project_id: str,
        artifact_type: str,
        artifact_id: str,
        profile_type: str,
        profile_version_id: str,
        profile_payload: dict[str, object],
    ) -> None:
        query: str | None = None
        values: tuple[object, ...] = ()
        if artifact_type == "DocumentSnapshot" and profile_type == "DocumentConventionProfile":
            query = """
                SELECT bool_and(profile_version_id = %s) AND count(*) > 0
                FROM snapshot_memberships
                WHERE document_snapshot_id = %s AND project_id = %s
            """
            values = (profile_version_id, artifact_id, project_id)
        elif artifact_type == "DocumentSnapshot" and profile_type == "DocumentRelationProfile":
            query = """
                SELECT count(*) > 0 FROM document_relation_builds
                WHERE relation_profile_version_id = %s
                  AND document_snapshot_id = %s AND project_id = %s
            """
            values = (profile_version_id, artifact_id, project_id)
        elif artifact_type == "SearchIndexBuild" and profile_type == "EmbeddingProfile":
            query = """
                SELECT embedding_profile_version_id = %s FROM search_index_builds
                WHERE search_index_build_id = %s AND project_id = %s
            """
            values = (profile_version_id, artifact_id, project_id)
        elif artifact_type == "CodeGraphSnapshot" and profile_type == "CodeFrameworkProfile":
            query = """
                SELECT count(*) > 0 FROM code_graph_snapshot_profiles
                WHERE profile_version_id = %s
                  AND code_graph_snapshot_id = %s AND project_id = %s
            """
            values = (profile_version_id, artifact_id, project_id)
        elif artifact_type == "UiKnowledgeSnapshot" and profile_type == "UiLocatorProfile":
            if profile_payload.get("ui_knowledge_snapshot_id") != artifact_id:
                raise ValueError(
                    "replacement UI Knowledge Snapshot is not bound by the active Profile"
                )
            return
        elif (
            artifact_type == "CommandExecutionResult" and profile_type == "CommandExecutionProfile"
        ):
            query = """
                SELECT request.command_profile_version_id = %s
                FROM command_execution_requests AS request
                WHERE request.command_execution_id = %s AND request.project_id = %s
            """
            values = (profile_version_id, artifact_id, project_id)
        if query is None:
            return
        cursor.execute(query, values)
        row = cursor.fetchone()
        if row is None or not bool(row[0]):
            raise ValueError("replacement Artifact is not bound to the activated Profile version")


__all__ = ["ProfileReplacementValidator"]


def _require_reference(label: str, actual: str, expected: set[str] | None) -> None:
    if expected and actual not in expected:
        raise ValueError(f"{label} does not reference a validated replacement")


def _require_any_reference(label: str, actual: set[str], expected: set[str] | None) -> None:
    if expected and not actual.intersection(expected):
        raise ValueError(f"{label} does not reference a validated replacement")
