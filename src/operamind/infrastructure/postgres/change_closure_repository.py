"""Load Canonical closure evidence and append immutable Change Closure Results."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from psycopg import Connection

from operamind.contracts import ContractCatalog, project_change_closure_result
from operamind.infrastructure.postgres.artifact_repository import ArtifactRepository
from operamind.infrastructure.postgres.errors import PersistenceConflictError


@dataclass(frozen=True, slots=True)
class ChangeClosureEvidence:
    change_request: dict[str, Any]
    orchestration: dict[str, Any]
    test_plan: dict[str, Any]
    test_data_plan: dict[str, Any]
    coverage_report: dict[str, Any]
    edit_result: dict[str, Any] | None
    changed_line_coverage: dict[str, Any] | None
    test_data_result: dict[str, Any] | None
    ui_result: dict[str, Any] | None
    ui_test_case_refs: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True, slots=True)
class ChangeClosureRecord:
    closure_result_id: str
    status: str
    created_at: datetime
    created: bool


class ChangeClosureRepository:
    """Reconstruct normalized component evidence and persist one immutable snapshot."""

    def __init__(self, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._connection = connection
        self._contracts = contracts
        self._artifacts = ArtifactRepository(connection, contracts)

    def latest_changed_line_coverage(
        self,
        *,
        project_id: str,
        analysis_case_id: str,
        orchestration_id: str,
    ) -> dict[str, Any] | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT result.changed_line_coverage
                FROM edit_results AS result
                JOIN edit_packets AS packet
                  ON packet.edit_packet_id = result.edit_packet_id
                 AND packet.project_id = result.project_id
                JOIN change_orchestrations AS orchestration
                  ON orchestration.impact_report_id = packet.impact_report_id
                 AND orchestration.project_id = packet.project_id
                 AND orchestration.analysis_case_id = packet.analysis_case_id
                WHERE result.project_id = %s
                  AND result.analysis_case_id = %s
                  AND orchestration.orchestration_id = %s
                ORDER BY result.recorded_at DESC, result.edit_result_id DESC
                LIMIT 1
                """,
                (project_id, analysis_case_id, orchestration_id),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        value = cast(dict[str, Any], row[0])
        self._contracts.validate_artifact(value)
        return value

    def load_evidence(self, orchestration_id: str) -> ChangeClosureEvidence:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT change_request_id, project_id, analysis_case_id,
                       test_plan_id, test_data_plan_id, coverage_report_id,
                       created_at
                FROM change_orchestrations
                WHERE orchestration_id = %s
                """,
                (orchestration_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("Change Orchestration does not exist")
            request_id, project_id, case_id, test_plan_id, data_plan_id, coverage_id = (
                str(value) for value in row[:6]
            )
            orchestration_created_at = cast(datetime, row[6])
            cursor.execute(
                """
                SELECT result.edit_result_id, result.project_id,
                       result.analysis_case_id, result.validation_mode, result.status,
                       result.base_repository_revision,
                       result.result_repository_revision,
                       result.changed_paths, result.out_of_scope_files,
                       result.test_result_refs, result.tests_passed,
                       result.command_evidence_status, result.changed_line_coverage
                FROM edit_results AS result
                JOIN edit_packets AS packet
                  ON packet.edit_packet_id = result.edit_packet_id
                 AND packet.project_id = result.project_id
                JOIN change_orchestrations AS orchestration
                  ON orchestration.impact_report_id = packet.impact_report_id
                 AND orchestration.project_id = packet.project_id
                 AND orchestration.analysis_case_id = packet.analysis_case_id
                WHERE result.project_id = %s
                  AND result.analysis_case_id = %s
                  AND orchestration.orchestration_id = %s
                ORDER BY result.recorded_at DESC, result.edit_result_id DESC
                LIMIT 1
                """,
                (project_id, case_id, orchestration_id),
            )
            edit_row = cursor.fetchone()
            cursor.execute(
                """
                SELECT result_artifact_id
                FROM test_data_execution_runs
                WHERE orchestration_id = %s AND project_id = %s
                ORDER BY started_at DESC, run_id DESC
                LIMIT 1
                """,
                (orchestration_id, project_id),
            )
            data_row = cursor.fetchone()
            cursor.execute(
                """
                SELECT verification_result_id, ui_execution_plan_id
                FROM change_validations
                WHERE project_id = %s AND analysis_case_id = %s
                  AND created_at >= %s
                ORDER BY created_at DESC, verification_result_id DESC
                LIMIT 1
                """,
                (project_id, case_id, orchestration_created_at),
            )
            ui_row = cursor.fetchone()
            ui_artifact_id: str | None = None
            ui_test_case_refs: tuple[tuple[str, tuple[str, ...]], ...] = ()
            if ui_row is not None:
                cursor.execute(
                    """
                    SELECT planned.scenario_id, scenario.test_case_refs
                    FROM ui_execution_plan_scenarios AS planned
                    JOIN verification_scenarios AS scenario
                      ON scenario.scenario_version_id = planned.scenario_version_id
                     AND scenario.project_id = planned.project_id
                    WHERE planned.ui_execution_plan_id = %s
                      AND planned.project_id = %s
                    ORDER BY planned.execution_order
                    """,
                    (str(ui_row[1]), project_id),
                )
                ui_test_case_refs = tuple(
                    (
                        str(mapping[0]),
                        tuple(str(value) for value in cast(list[object], mapping[1])),
                    )
                    for mapping in cursor.fetchall()
                )
                ui_artifact_id = str(ui_row[0])
            else:
                cursor.execute(
                    """
                    SELECT artifact_id
                    FROM artifact_records
                    WHERE project_id = %s
                      AND analysis_case_id = %s
                      AND artifact_type = 'UiVerificationResult'
                      AND created_at >= %s
                    ORDER BY created_at DESC, artifact_id DESC
                    LIMIT 1
                    """,
                    (project_id, case_id, orchestration_created_at),
                )
                direct_ui_row = cursor.fetchone()
                if direct_ui_row is not None:
                    ui_artifact_id = str(direct_ui_row[0])
        edit_result = None
        if edit_row is not None:
            edit_result = {
                "edit_result_id": str(edit_row[0]),
                "project_id": str(edit_row[1]),
                "analysis_case_id": str(edit_row[2]),
                "validation_mode": str(edit_row[3]),
                "status": str(edit_row[4]),
                "base_repository_revision": str(edit_row[5]),
                "result_repository_revision": (
                    str(edit_row[6]) if edit_row[6] is not None else None
                ),
                "changed_paths": [str(value) for value in cast(list[object], edit_row[7])],
                "out_of_scope_files": [str(value) for value in cast(list[object], edit_row[8])],
                "test_result_refs": [str(value) for value in cast(list[object], edit_row[9])],
                "tests_passed": edit_row[10],
                "command_evidence_status": str(edit_row[11]),
            }
            changed_line_coverage = cast(dict[str, Any], edit_row[12])
            self._contracts.validate_artifact(changed_line_coverage)
        else:
            changed_line_coverage = None
        data_result = None
        if data_row is not None and data_row[0] is not None:
            data_result = self._required_artifact(str(data_row[0]), "TestDataExecutionResult")
        ui_result = None
        if ui_artifact_id is not None:
            ui_result = self._required_artifact(ui_artifact_id, "UiVerificationResult")
            if ui_row is None:
                ui_test_case_refs = tuple(
                    (
                        str(result["scenario_id"]),
                        (str(result["scenario_id"]),),
                    )
                    for result in cast(
                        list[dict[str, Any]], ui_result["scenario_results"]
                    )
                )
        return ChangeClosureEvidence(
            change_request=self._required_artifact(request_id, "ChangeRequest"),
            orchestration=self._required_artifact(orchestration_id, "ChangeOrchestrationPlan"),
            test_plan=self._required_artifact(test_plan_id, "TestPlan"),
            test_data_plan=self._required_artifact(data_plan_id, "TestDataPlan"),
            coverage_report=self._required_artifact(coverage_id, "BusinessCoverageReport"),
            edit_result=edit_result,
            changed_line_coverage=changed_line_coverage,
            test_data_result=data_result,
            ui_result=ui_result,
            ui_test_case_refs=ui_test_case_refs,
        )

    def persist(
        self,
        *,
        evidence: ChangeClosureEvidence,
        artifact: dict[str, Any],
        created_by: str,
    ) -> ChangeClosureRecord:
        if not created_by.strip():
            raise ValueError("Closure actor must not be blank")
        orchestration = evidence.orchestration
        component_digest = hashlib.sha256(
            json.dumps(
                artifact["artifact_refs"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        edit_result_id = (
            str(evidence.edit_result["edit_result_id"])
            if evidence.edit_result is not None
            else None
        )
        data_result_id = (
            str(evidence.test_data_result["execution_result_id"])
            if evidence.test_data_result is not None
            else None
        )
        ui_result_id = (
            str(evidence.ui_result["verification_result_id"])
            if evidence.ui_result is not None
            else None
        )
        identity = (
            str(orchestration["orchestration_id"]),
            str(orchestration["change_request_id"]),
            str(orchestration["project_id"]),
            str(orchestration["analysis_case_id"]),
            edit_result_id,
            data_result_id,
            ui_result_id,
            str(evidence.coverage_report["coverage_report_id"]),
            component_digest,
            str(artifact["status"]),
            list(cast(list[object], artifact["unresolved_items"])),
            created_by,
        )
        with self._connection.transaction(), self._connection.cursor() as cursor:
            self._artifacts.store(
                artifact_id=str(artifact["closure_result_id"]),
                project_id=str(orchestration["project_id"]),
                analysis_case_id=str(orchestration["analysis_case_id"]),
                artifact=artifact,
            )
            cursor.execute(
                """
                INSERT INTO change_closure_results (
                    closure_result_id, orchestration_id, change_request_id,
                    project_id, analysis_case_id, edit_result_id,
                    test_data_execution_result_id, ui_verification_result_id,
                    coverage_report_id, component_digest, status,
                    unresolved_items, created_by
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s
                ) ON CONFLICT DO NOTHING
                """,
                (
                    artifact["closure_result_id"],
                    *identity[:10],
                    json.dumps(identity[10], ensure_ascii=False),
                    identity[11],
                ),
            )
            created = cursor.rowcount == 1
            cursor.execute(
                """
                SELECT orchestration_id, change_request_id, project_id,
                       analysis_case_id, edit_result_id,
                       test_data_execution_result_id, ui_verification_result_id,
                       coverage_report_id, component_digest, status,
                       unresolved_items, created_by, created_at
                FROM change_closure_results
                WHERE closure_result_id = %s
                """,
                (artifact["closure_result_id"],),
            )
            row = cursor.fetchone()
        if row is None or tuple(row[:12]) != identity:
            raise PersistenceConflictError(
                "Change Closure Result identity has different immutable content"
            )
        return ChangeClosureRecord(
            closure_result_id=str(artifact["closure_result_id"]),
            status=str(artifact["status"]),
            created_at=row[12],
            created=created,
        )

    def latest(self, change_request_id: str) -> dict[str, Any] | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT closure_result_id
                FROM change_closure_results
                WHERE change_request_id = %s
                ORDER BY created_at DESC, closure_result_id DESC
                LIMIT 1
                """,
                (change_request_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return self._required_artifact(str(row[0]), "ChangeClosureResult")

    def latest_for_orchestration(self, orchestration_id: str) -> dict[str, Any] | None:
        """Return a Closure only for the selected Test Case/Orchestration version."""

        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT closure_result_id
                FROM change_closure_results
                WHERE orchestration_id = %s
                ORDER BY created_at DESC, closure_result_id DESC
                LIMIT 1
                """,
                (orchestration_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return self._required_artifact(str(row[0]), "ChangeClosureResult")

    def _required_artifact(self, artifact_id: str, artifact_type: str) -> dict[str, Any]:
        artifact = self._artifacts.get(artifact_id)
        if artifact is None or artifact.get("artifact_type") != artifact_type:
            raise PersistenceConflictError(
                f"Required {artifact_type} Artifact is missing: {artifact_id}"
            )
        if artifact_type != "ChangeClosureResult":
            return artifact
        projected = project_change_closure_result(artifact)
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT reason FROM profile_drift_impacts
                WHERE project_id = %s
                  AND artifact_type = 'ChangeClosureResult'
                  AND artifact_id = %s
                  AND resolved_at IS NULL
                ORDER BY profile_drift_event_id
                """,
                (artifact.get("project_id"), artifact_id),
            )
            reasons = [str(row[0]) for row in cursor.fetchall()]
        if not reasons:
            return projected
        unresolved = {
            str(value) for value in cast(list[object], projected.get("unresolved_items", []))
        }
        unresolved.update(f"Profile drift: {reason}" for reason in reasons)
        return {
            **projected,
            "compatibility_status": "stale",
            "status": "blocked",
            "unresolved_items": sorted(unresolved),
        }
