"""Bounded read models for MCP Control Plane query tools."""

from __future__ import annotations

from typing import Any, cast

from psycopg import Connection

from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres.artifact_repository import ArtifactRepository


class ControlPlaneQueryRepository:
    """Read exact scoped records without exposing unbounded database collections."""

    def __init__(self, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._connection = connection
        self._artifacts = ArtifactRepository(connection, contracts)

    def list_ready_cases(
        self,
        *,
        workspace_roots: tuple[str, ...],
        remote_url: str,
        head_revision: str,
        limit: int,
    ) -> tuple[dict[str, object], ...]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT analysis_case.project_id, analysis_case.analysis_case_id,
                       analysis_case.status, repository.repository_id,
                       revision.repository_revision_id,
                       revision.commit_sha AS base_revision,
                       CASE
                           WHEN analysis_case.status = 'verifying_ui'
                           THEN committed_result.result_repository_revision
                           ELSE revision.commit_sha
                       END AS head_revision,
                       report.impact_report_id, report.status,
                       packet.edit_packet_id, packet.status,
                       grant_record.approval_grant_id,
                       CASE
                           WHEN grant_record.approval_grant_id IS NULL THEN NULL
                           WHEN EXISTS (
                               SELECT 1 FROM approval_grant_events AS event
                               WHERE event.approval_grant_id = grant_record.approval_grant_id
                                 AND event.event_type = 'revoked'
                           ) THEN 'revoked'
                           WHEN EXISTS (
                               SELECT 1 FROM approval_grant_events AS event
                               WHERE event.approval_grant_id = grant_record.approval_grant_id
                                 AND event.event_type = 'completed'
                           ) THEN 'completed'
                           WHEN grant_record.expires_at <= now() THEN 'expired'
                           WHEN EXISTS (
                               SELECT 1 FROM approval_grant_events AS event
                               WHERE event.approval_grant_id = grant_record.approval_grant_id
                                 AND event.event_type = 'edit_completed'
                           ) THEN 'ui_pending'
                           ELSE 'active_editing'
                       END AS grant_state
                FROM analysis_cases AS analysis_case
                JOIN repository_revisions AS revision
                  ON revision.repository_revision_id = analysis_case.repository_revision_id
                JOIN repositories AS repository
                  ON repository.repository_id = revision.repository_id
                 AND repository.project_id = analysis_case.project_id
                LEFT JOIN LATERAL (
                    SELECT impact_report_id, status
                    FROM impact_reports
                    WHERE project_id = analysis_case.project_id
                      AND analysis_case_id = analysis_case.analysis_case_id
                    ORDER BY created_at DESC, impact_report_id DESC
                    LIMIT 1
                ) AS report ON true
                LEFT JOIN LATERAL (
                    SELECT edit_packet_id, status
                    FROM edit_packets
                    WHERE project_id = analysis_case.project_id
                      AND analysis_case_id = analysis_case.analysis_case_id
                    ORDER BY (status = 'active') DESC, created_at DESC, edit_packet_id DESC
                    LIMIT 1
                ) AS packet ON true
                LEFT JOIN approval_grants AS grant_record
                  ON grant_record.project_id = analysis_case.project_id
                 AND grant_record.edit_packet_id = packet.edit_packet_id
                LEFT JOIN LATERAL (
                    SELECT result_repository_revision
                    FROM edit_results
                    WHERE project_id = analysis_case.project_id
                      AND analysis_case_id = analysis_case.analysis_case_id
                      AND validation_mode = 'committed'
                      AND status = 'in_scope'
                      AND tests_passed IS TRUE
                      AND command_evidence_status = 'verified'
                    ORDER BY recorded_at DESC, edit_result_id DESC
                    LIMIT 1
                ) AS committed_result ON true
                WHERE repository.workspace_root = ANY(%s)
                  AND repository.remote_url = %s
                  AND CASE
                          WHEN analysis_case.status = 'verifying_ui'
                          THEN committed_result.result_repository_revision
                          ELSE revision.commit_sha
                      END = %s
                  AND analysis_case.status IN (
                      'ready_for_impact', 'awaiting_confirmation',
                      'editing', 'verifying_ui'
                  )
                ORDER BY analysis_case.updated_at DESC, analysis_case.analysis_case_id
                LIMIT %s
                """,
                (list(workspace_roots), remote_url, head_revision, limit),
            )
            rows = cursor.fetchall()
        return tuple(
            {
                "project_id": str(row[0]),
                "analysis_case_id": str(row[1]),
                "case_status": str(row[2]),
                "repository_id": str(row[3]),
                "repository_revision_id": str(row[4]),
                "base_revision": str(row[5]),
                "head_revision": str(row[6]),
                "impact_report_id": str(row[7]) if row[7] is not None else None,
                "impact_report_status": str(row[8]) if row[8] is not None else None,
                "edit_packet_id": str(row[9]) if row[9] is not None else None,
                "edit_packet_status": str(row[10]) if row[10] is not None else None,
                "approval_grant_id": str(row[11]) if row[11] is not None else None,
                "approval_grant_state": str(row[12]) if row[12] is not None else None,
            }
            for row in rows
        )

    def get_impact_report(
        self, *, project_id: str, analysis_case_id: str, impact_report_id: str
    ) -> dict[str, object]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status, repository_revision, document_snapshot_id,
                       context_package_id, code_graph_snapshot_id,
                       analysis_policy_version, blocking_unknowns
                FROM impact_reports
                WHERE impact_report_id = %s AND project_id = %s
                  AND analysis_case_id = %s
                """,
                (impact_report_id, project_id, analysis_case_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise ValueError("Impact Report does not exist in requested Project/Case scope")
        artifact = self._artifacts.get(impact_report_id)
        if artifact is None or artifact.get("artifact_type") != "ImpactReport":
            raise RuntimeError("Normalized Impact Report has no immutable Artifact")
        return {
            "artifact": artifact,
            "current_status": str(row[0]),
            "repository_revision": str(row[1]),
            "document_snapshot_id": str(row[2]),
            "context_package_id": str(row[3]),
            "code_graph_snapshot_id": str(row[4]),
            "analysis_policy_version": str(row[5]),
            "blocking_unknowns": list(cast(list[object], row[6])),
        }

    def get_ui_plan(self, *, project_id: str, plan_id: str) -> dict[str, object]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT plan.analysis_case_id, plan.edit_packet_id, plan.edit_result_id,
                       plan.environment_id, plan.deployment_revision,
                       plan.repository_revision, plan.status, plan.scenario_refs,
                       plan.blocking_reasons, environment.base_url,
                       plan.repository_binding_status
                FROM ui_execution_plans AS plan
                JOIN ui_environments AS environment
                  ON environment.environment_id = plan.environment_id
                 AND environment.project_id = plan.project_id
                WHERE plan.ui_execution_plan_id = %s AND plan.project_id = %s
                """,
                (plan_id, project_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("UI Execution Plan does not exist in requested Project scope")
            cursor.execute(
                """
                SELECT scenario_id, scenario_version_id, execution_order
                FROM ui_execution_plan_scenarios
                WHERE ui_execution_plan_id = %s AND project_id = %s
                ORDER BY execution_order
                """,
                (plan_id, project_id),
            )
            scenarios = cursor.fetchall()
        return {
            "ui_execution_plan_id": plan_id,
            "project_id": project_id,
            "analysis_case_id": str(row[0]),
            "edit_packet_id": str(row[1]),
            "edit_result_id": str(row[2]),
            "environment_id": str(row[3]),
            "deployment_revision": str(row[4]),
            "repository_revision": str(row[5]),
            "status": str(row[6]),
            "scenario_refs": list(cast(list[object], row[7])),
            "blocking_reasons": list(cast(list[object], row[8])),
            "base_url": str(row[9]),
            "repository_binding_status": str(row[10]),
            "scenario_versions": [
                {
                    "scenario_id": str(scenario[0]),
                    "scenario_version_id": str(scenario[1]),
                    "execution_order": int(scenario[2]),
                }
                for scenario in scenarios
            ],
        }

    def get_validation_result(
        self, *, project_id: str, verification_result_id: str
    ) -> dict[str, object]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT analysis_case_id, ui_execution_plan_id, ui_execution_run_id,
                       status, unresolved_impact_item_ids, out_of_scope_files,
                       failure_reasons
                FROM change_validations
                WHERE verification_result_id = %s AND project_id = %s
                """,
                (verification_result_id, project_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise ValueError("Validation Result does not exist in requested Project scope")
        artifact = self._artifacts.get(verification_result_id)
        if artifact is None or artifact.get("artifact_type") != "UiVerificationResult":
            raise RuntimeError("Normalized Validation Result has no immutable Artifact")
        return {
            "artifact": artifact,
            "analysis_case_id": str(row[0]),
            "ui_execution_plan_id": str(row[1]),
            "ui_execution_run_id": str(row[2]) if row[2] is not None else None,
            "current_status": str(row[3]),
            "unresolved_impact_item_ids": list(cast(list[object], row[4])),
            "out_of_scope_files": list(cast(list[object], row[5])),
            "failure_reasons": list(cast(list[object], row[6])),
        }
