"""Canonical and externally observed inputs for repository readiness evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row

from operamind.infrastructure.postgres.errors import PersistenceConflictError


@dataclass(frozen=True, slots=True)
class ReadinessEvidenceInput:
    """A schema-valid evidence envelope before it is written to the repository."""

    evidence_id: str
    gate_id: str
    evidence_type: str
    observed_at: datetime
    review_status: str
    reviewed_by: tuple[str, ...]
    subject: dict[str, object]


class ReadinessEvidenceRepository:
    """Read fail-closed gate facts from Canonical PostgreSQL."""

    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def record_observation(
        self,
        *,
        observation_id: str,
        gate_id: str,
        evidence_type: str,
        project_id: str | None,
        analysis_case_id: str | None,
        observed_at: datetime,
        review_status: str,
        reviewed_by: tuple[str, ...],
        subject: dict[str, object],
    ) -> bool:
        """Append one immutable external observation; exact replay is a no-op."""

        normalized = json.dumps(subject, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(normalized.encode()).hexdigest()
        values = (
            observation_id,
            gate_id,
            evidence_type,
            project_id,
            analysis_case_id,
            observed_at,
            review_status,
            json.dumps(reviewed_by),
            normalized,
            digest,
        )
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO readiness_observations (
                    observation_id, gate_id, evidence_type, project_id,
                    analysis_case_id, observed_at, review_status, reviewed_by,
                    subject, subject_digest
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                ON CONFLICT DO NOTHING
                """,
                values,
            )
            created = cursor.rowcount == 1
            if not created:
                cursor.execute(
                    """
                    SELECT gate_id, evidence_type, project_id, analysis_case_id,
                           observed_at, review_status, reviewed_by, subject, subject_digest
                    FROM readiness_observations
                    WHERE observation_id = %s
                       OR (
                           gate_id = %s
                           AND project_id IS NOT DISTINCT FROM %s
                           AND analysis_case_id IS NOT DISTINCT FROM %s
                           AND subject_digest = %s
                       )
                    ORDER BY (observation_id = %s) DESC
                    LIMIT 1
                    """,
                    (
                        observation_id,
                        gate_id,
                        project_id,
                        analysis_case_id,
                        digest,
                        observation_id,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    raise PersistenceConflictError("Readiness observation disappeared")
                actual = tuple(row)
                expected = (
                    gate_id,
                    evidence_type,
                    project_id,
                    analysis_case_id,
                    observed_at,
                    review_status,
                    list(reviewed_by),
                    subject,
                    digest,
                )
                if actual != expected:
                    raise PersistenceConflictError(
                        "Readiness observation ID already has different immutable content"
                    )
            return created

    def embedding_provider(self, project_id: str) -> ReadinessEvidenceInput | None:
        """Return the newest probe bound to a current ready Canonical Search Index."""

        return self._one(
            """
            SELECT o.*
            FROM readiness_observations AS o
            JOIN search_index_builds AS build
              ON build.project_id = o.project_id
             AND build.is_current
             AND build.status = 'ready'
             AND build.embedding_profile_version_id = o.subject ->> 'profile_version_id'
             AND build.embedding_model = o.subject ->> 'model'
             AND build.dimensions = (o.subject ->> 'dimensions')::integer
            JOIN project_profile_bindings AS binding
              ON binding.project_id = build.project_id
             AND binding.active_profile_version_id = build.embedding_profile_version_id
            WHERE o.gate_id = 'embedding_provider_live'
              AND o.project_id = %s
            ORDER BY o.observed_at DESC, o.observation_id DESC
            LIMIT 1
            """,
            (project_id,),
        )

    def human_approval(
        self, project_id: str, analysis_case_id: str
    ) -> ReadinessEvidenceInput | None:
        """Derive a human approval chain entirely from Canonical tables."""

        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT report.impact_report_id, confirmation.confirmation_id,
                       confirmation.confirmed_by, confirmation.confirmed_at,
                       approval.approval_grant_id, approval.approved_by,
                       approval.created_at
                FROM impact_reports AS report
                JOIN impact_confirmations AS confirmation
                  ON confirmation.impact_report_id = report.impact_report_id
                 AND confirmation.project_id = report.project_id
                JOIN edit_packets AS packet
                  ON packet.impact_report_id = report.impact_report_id
                 AND packet.confirmation_id = confirmation.confirmation_id
                 AND packet.project_id = report.project_id
                JOIN approval_grants AS approval
                  ON approval.edit_packet_id = packet.edit_packet_id
                 AND approval.project_id = packet.project_id
                WHERE report.project_id = %s
                  AND report.analysis_case_id = %s
                  AND report.status = 'confirmed'
                  AND (
                      packet.status = 'active'
                      OR (
                          packet.status = 'superseded'
                          AND EXISTS (
                              SELECT 1 FROM edit_results AS result
                              WHERE result.edit_packet_id = packet.edit_packet_id
                                AND result.project_id = packet.project_id
                                AND result.approval_grant_id = approval.approval_grant_id
                                AND result.validation_mode = 'committed'
                                AND result.status = 'in_scope'
                                AND result.tests_passed
                                AND result.command_evidence_status = 'verified'
                          )
                      )
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM approval_grant_events AS event
                      WHERE event.approval_grant_id = approval.approval_grant_id
                        AND event.event_type = 'revoked'
                  )
                ORDER BY approval.created_at DESC
                LIMIT 1
                """,
                (project_id, analysis_case_id),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        reviewers = tuple(sorted({str(row["confirmed_by"]), str(row["approved_by"])}))
        observed_at = max(row["confirmed_at"], row["created_at"])
        evidence_id = f"canonical-human-{row['confirmation_id']}-{row['approval_grant_id']}"
        return ReadinessEvidenceInput(
            evidence_id=evidence_id,
            gate_id="human_approval_e2e",
            evidence_type="human_review",
            observed_at=observed_at,
            review_status="reviewed",
            reviewed_by=reviewers,
            subject={
                "project_id": project_id,
                "analysis_case_id": analysis_case_id,
                "impact_report_id": str(row["impact_report_id"]),
                "confirmation_id": str(row["confirmation_id"]),
                "approval_grant_id": str(row["approval_grant_id"]),
                "decision": "approved",
            },
        )

    def copilot_task_receipt_subject(self, coding_task_id: str) -> dict[str, object]:
        """Return the completed Canonical Task/Edit identity used by a live receipt."""

        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT task.project_id, task.analysis_case_id, task.coding_task_id,
                       task.edit_packet_id, task.approval_grant_id,
                       task.base_repository_revision,
                       result.result_repository_revision
                FROM copilot_coding_tasks AS task
                JOIN copilot_coding_task_edit_results AS task_result
                  ON task_result.coding_task_id = task.coding_task_id
                 AND task_result.project_id = task.project_id
                JOIN edit_results AS result
                  ON result.edit_result_id = task_result.edit_result_id
                 AND result.project_id = task_result.project_id
                WHERE task.coding_task_id = %s
                  AND task.state = 'completed'
                  AND result.validation_mode = 'committed'
                  AND result.status = 'in_scope'
                  AND result.tests_passed
                  AND result.command_evidence_status = 'verified'
                ORDER BY result.recorded_at DESC
                LIMIT 1
                """,
                (coding_task_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise ValueError(
                "Copilot Coding Task has no completed, command-verified committed result"
            )
        return {key: str(value) for key, value in row.items()}

    def copilot(self, project_id: str, analysis_case_id: str) -> ReadinessEvidenceInput | None:
        """Cross-check a reviewed Copilot receipt against its committed Edit Result."""

        return self._one(
            """
            SELECT o.*
            FROM readiness_observations AS o
            JOIN edit_results AS result
              ON result.project_id = o.project_id
             AND result.analysis_case_id = o.analysis_case_id
             AND result.edit_packet_id = o.subject ->> 'edit_packet_id'
             AND result.approval_grant_id = o.subject ->> 'approval_grant_id'
             AND result.base_repository_revision = o.subject ->> 'base_repository_revision'
             AND result.result_repository_revision = o.subject ->> 'result_repository_revision'
             AND result.validation_mode = 'committed'
             AND result.status = 'in_scope'
             AND result.tests_passed
             AND result.command_evidence_status = 'verified'
            JOIN copilot_coding_tasks AS task
              ON task.coding_task_id = o.subject ->> 'coding_task_id'
             AND task.project_id = o.project_id
             AND task.analysis_case_id = o.analysis_case_id
             AND task.edit_packet_id = result.edit_packet_id
             AND task.approval_grant_id = result.approval_grant_id
             AND task.base_repository_revision = result.base_repository_revision
             AND task.state = 'completed'
            JOIN copilot_coding_task_edit_results AS task_result
              ON task_result.coding_task_id = task.coding_task_id
             AND task_result.project_id = task.project_id
             AND task_result.edit_result_id = result.edit_result_id
            JOIN approval_grant_events AS event
              ON event.approval_grant_id = result.approval_grant_id
             AND event.project_id = result.project_id
             AND event.event_type = 'edit_completed'
            WHERE o.gate_id = 'github_copilot_live'
              AND o.project_id = %s
              AND o.analysis_case_id = %s
              AND o.subject ->> 'project_id' = o.project_id
              AND o.subject ->> 'analysis_case_id' = o.analysis_case_id
              AND o.review_status = 'reviewed'
              AND o.observed_at >= result.recorded_at
              AND o.observed_at >= event.created_at
              AND jsonb_array_length(result.test_result_refs) > 0
              AND (
                  SELECT count(*) FROM edit_result_command_executions AS relation
                  WHERE relation.edit_result_id = result.edit_result_id
                    AND relation.project_id = result.project_id
              ) = jsonb_array_length(result.test_result_refs)
              AND NOT EXISTS (
                  SELECT 1
                  FROM edit_result_command_executions AS relation
                  JOIN command_execution_results AS command_result
                    ON command_result.command_execution_id = relation.command_execution_id
                  WHERE relation.edit_result_id = result.edit_result_id
                    AND relation.project_id = result.project_id
                    AND command_result.status <> 'passed'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM approval_grant_events AS revoked
                  WHERE revoked.approval_grant_id = result.approval_grant_id
                    AND revoked.project_id = result.project_id
                    AND revoked.event_type = 'revoked'
              )
            ORDER BY o.observed_at DESC, o.observation_id DESC
            LIMIT 1
            """,
            (project_id, analysis_case_id),
        )

    def deployment(self, project_id: str, analysis_case_id: str) -> ReadinessEvidenceInput | None:
        """Derive a current revision-bound UI result from TestDataPlan execution."""

        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT validation.artifact_id AS verification_result_id,
                       validation.created_at,
                       orchestration.orchestration_id,
                       run.run_id AS test_data_run_id,
                       validation.payload ->> 'environment_id' AS environment_id,
                       validation.payload ->> 'deployment_revision' AS deployment_revision,
                       validation.payload ->> 'repository_revision' AS repository_revision,
                       array_agg(evidence.evidence_id ORDER BY evidence.evidence_id) AS evidence_ids
                FROM artifact_records AS validation
                JOIN change_orchestrations AS orchestration
                  ON orchestration.orchestration_id =
                     validation.payload ->> 'orchestration_id'
                 AND orchestration.project_id = validation.project_id
                 AND orchestration.analysis_case_id = validation.analysis_case_id
                JOIN test_data_execution_runs AS run
                  ON run.execution_result_id =
                     validation.payload ->> 'test_data_execution_result_id'
                 AND run.orchestration_id = orchestration.orchestration_id
                 AND run.project_id = validation.project_id
                JOIN test_data_execution_evidence AS evidence
                  ON evidence.run_id = run.run_id
                 AND evidence.project_id = run.project_id
                 AND evidence.sanitized
                 AND evidence.evidence_type = 'screenshot'
                WHERE validation.artifact_type = 'UiVerificationResult'
                  AND validation.schema_version = 'v2'
                  AND validation.project_id = %s
                  AND validation.analysis_case_id = %s
                  AND validation.payload ->> 'status' = 'passed'
                  AND validation.payload ->> 'deployment_revision' =
                      validation.payload ->> 'repository_revision'
                  AND jsonb_array_length(validation.payload -> 'scenario_results') > 0
                  AND NOT EXISTS (
                      SELECT 1
                      FROM jsonb_array_elements(
                          validation.payload -> 'scenario_results'
                      ) AS scenario
                      WHERE scenario ->> 'status' <> 'passed'
                  )
                  AND jsonb_array_length(
                      validation.payload -> 'unresolved_impact_item_ids'
                  ) = 0
                  AND jsonb_array_length(validation.payload -> 'out_of_scope_files') = 0
                  AND jsonb_array_length(validation.payload -> 'failure_reasons') = 0
                  AND run.status = 'passed'
                  AND run.result_artifact_id = run.execution_result_id
                  AND NOT EXISTS (
                      SELECT 1
                      FROM test_data_execution_runs AS newer_run
                      WHERE newer_run.project_id = run.project_id
                        AND newer_run.orchestration_id = run.orchestration_id
                        AND (newer_run.started_at, newer_run.run_id) >
                            (run.started_at, run.run_id)
                  )
                  AND EXISTS (
                      SELECT 1
                      FROM edit_results AS result
                      JOIN edit_packets AS packet
                        ON packet.edit_packet_id = result.edit_packet_id
                       AND packet.project_id = result.project_id
                      WHERE packet.impact_report_id = orchestration.impact_report_id
                        AND packet.project_id = orchestration.project_id
                        AND packet.analysis_case_id = orchestration.analysis_case_id
                        AND result.project_id = orchestration.project_id
                        AND result.analysis_case_id = orchestration.analysis_case_id
                        AND result.validation_mode = 'committed'
                        AND result.status = 'in_scope'
                        AND result.tests_passed
                        AND result.command_evidence_status = 'verified'
                        AND result.result_repository_revision =
                            validation.payload ->> 'repository_revision'
                  )
                GROUP BY validation.artifact_id, validation.created_at,
                         orchestration.orchestration_id, run.run_id,
                         validation.payload
                ORDER BY validation.created_at DESC
                LIMIT 1
                """,
                (project_id, analysis_case_id),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return ReadinessEvidenceInput(
            evidence_id=f"canonical-deployment-{row['verification_result_id']}",
            gate_id="target_deployment_e2e",
            evidence_type="deployment_run",
            observed_at=row["created_at"],
            review_status="verified",
            reviewed_by=("automation:operamind-readiness",),
            subject={
                "project_id": project_id,
                "analysis_case_id": analysis_case_id,
                "orchestration_id": str(row["orchestration_id"]),
                "test_data_run_id": str(row["test_data_run_id"]),
                "verification_result_id": str(row["verification_result_id"]),
                "environment_id": str(row["environment_id"]),
                "deployment_revision": str(row["deployment_revision"]),
                "repository_revision": str(row["repository_revision"]),
                "status": "passed",
                "evidence_ids": [str(value) for value in row["evidence_ids"]],
            },
        )

    def full_regression(self) -> ReadinessEvidenceInput | None:
        """Return the newest deterministically verified full-suite observation."""

        return self._one(
            """
            SELECT * FROM readiness_observations
            WHERE gate_id = 'full_local_regression'
              AND review_status = 'verified'
            ORDER BY observed_at DESC, observation_id DESC
            LIMIT 1
            """,
            (),
        )

    def database_version(self) -> str:
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT version()")
            row = cursor.fetchone()
        if row is None:
            raise RuntimeError("PostgreSQL did not return its version")
        return str(row[0])

    def _one(self, query: str, parameters: tuple[object, ...]) -> ReadinessEvidenceInput | None:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, parameters)
            row = cursor.fetchone()
        if row is None:
            return None
        return ReadinessEvidenceInput(
            evidence_id=str(row["observation_id"]),
            gate_id=str(row["gate_id"]),
            evidence_type=str(row["evidence_type"]),
            observed_at=row["observed_at"],
            review_status=str(row["review_status"]),
            reviewed_by=tuple(str(value) for value in row["reviewed_by"]),
            subject=dict(row["subject"]),
        )
