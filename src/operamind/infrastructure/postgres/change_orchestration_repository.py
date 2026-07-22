"""Canonical evidence loading and immutable Change Orchestration persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from psycopg import Connection

from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres.artifact_repository import ArtifactRepository
from operamind.infrastructure.postgres.errors import PersistenceConflictError

if TYPE_CHECKING:
    from operamind.application.change_orchestration import ChangeOrchestrationResult


@dataclass(frozen=True, slots=True)
class CanonicalOrchestrationEvidence:
    change_request: dict[str, Any]
    analysis_case_id: str
    structured_changes: tuple[dict[str, Any], ...]
    accepted_structured_change_refs: frozenset[str]
    impact_report: dict[str, Any]
    impact_report_state: str
    impact_confirmation: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ChangeOrchestrationRecord:
    orchestration_id: str
    status: str
    created_at: datetime
    created: bool


class ChangeOrchestrationRepository:
    """Read one confirmed basis and persist all generated Artifacts atomically."""

    def __init__(self, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._connection = connection
        self._artifacts = ArtifactRepository(connection, contracts)

    def load_evidence(self, change_request_id: str) -> CanonicalOrchestrationEvidence:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT project_id, analysis_case_id
                FROM change_requests
                WHERE change_request_id = %s
                """,
                (change_request_id,),
            )
            request_row = cursor.fetchone()
            if request_row is None:
                raise ValueError("Change Request does not exist")
            project_id = str(request_row[0])
            if request_row[1] is None:
                raise ValueError("Change Request is not bound to an Analysis Case")
            case_id = str(request_row[1])
            cursor.execute(
                """
                SELECT decision
                FROM change_request_review_events
                WHERE change_request_id = %s AND project_id = %s
                  AND review_step = 'document_diff'
                ORDER BY created_at DESC, review_event_id DESC
                LIMIT 1
                """,
                (change_request_id, project_id),
            )
            review = cursor.fetchone()
            if review is None or str(review[0]) != "confirmed":
                raise ValueError("Document diff must be confirmed before orchestration")
            cursor.execute(
                """
                SELECT impact_report_id
                FROM impact_reports
                WHERE project_id = %s AND analysis_case_id = %s
                  AND status = 'confirmed'
                ORDER BY confirmed_at DESC, impact_report_id DESC
                LIMIT 1
                """,
                (project_id, case_id),
            )
            report_row = cursor.fetchone()
            if report_row is None:
                raise ValueError("A current confirmed Impact Report is required")
            report_id = str(report_row[0])
            cursor.execute(
                """
                SELECT confirmation_id
                FROM impact_confirmations
                WHERE impact_report_id = %s AND project_id = %s
                """,
                (report_id, project_id),
            )
            confirmation_row = cursor.fetchone()
            if confirmation_row is None:
                raise ValueError("Confirmed Impact Report has no confirmation Artifact")

        request = self._required_artifact(change_request_id, "ChangeRequest")
        report = self._required_artifact(report_id, "ImpactReport")
        confirmation = self._required_artifact(
            str(confirmation_row[0]), "ImpactConfirmation"
        )
        change_refs = sorted(
            {
                str(reference)
                for item in cast(list[dict[str, Any]], report["items"])
                for reference in cast(list[object], item["structured_change_refs"])
            }
        )
        changes = tuple(
            self._required_artifact(reference, "StructuredChange")
            for reference in change_refs
        )
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT change.structured_change_id,
                       COALESCE(review.decision, change.review_status) AS effective_status
                FROM structured_changes AS change
                LEFT JOIN LATERAL (
                    SELECT decision
                    FROM structured_change_review_events
                    WHERE project_id = change.project_id
                      AND structured_change_id = change.structured_change_id
                    ORDER BY review_sequence DESC
                    LIMIT 1
                ) AS review ON true
                WHERE change.project_id = %s
                  AND change.structured_change_id = ANY(%s)
                """,
                (project_id, change_refs),
            )
            review_rows = cursor.fetchall()
        effective_reviews = {str(row[0]): str(row[1]) for row in review_rows}
        if set(effective_reviews) != set(change_refs):
            raise PersistenceConflictError("Structured Change normalized ledger is incomplete")
        return CanonicalOrchestrationEvidence(
            change_request=request,
            analysis_case_id=case_id,
            structured_changes=changes,
            accepted_structured_change_refs=frozenset(
                change_id
                for change_id, status in effective_reviews.items()
                if status == "accepted"
            ),
            impact_report=report,
            impact_report_state="confirmed",
            impact_confirmation=confirmation,
        )

    def persist(
        self,
        *,
        result: ChangeOrchestrationResult,
        created_by: str,
    ) -> ChangeOrchestrationRecord:
        if not created_by.strip():
            raise ValueError("Orchestration actor must not be blank")
        plan = result.orchestration
        refs = cast(dict[str, str], plan["artifact_refs"])
        with self._connection.transaction(), self._connection.cursor() as cursor:
            for artifact in result.artifacts:
                artifact_id = _artifact_id(artifact)
                self._artifacts.store(
                    artifact_id=artifact_id,
                    project_id=str(plan["project_id"]),
                    analysis_case_id=str(plan["analysis_case_id"]),
                    artifact=artifact,
                )
            cursor.execute(
                """
                INSERT INTO change_orchestrations (
                    orchestration_id, change_request_id, project_id,
                    analysis_case_id, impact_report_id, reviewed_case_id,
                    reviewed_case_digest, status, acceptance_criteria_id,
                    test_plan_id, test_data_plan_id, coverage_report_id, created_by
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT DO NOTHING
                """,
                (
                    plan["orchestration_id"],
                    plan["change_request_id"],
                    plan["project_id"],
                    plan["analysis_case_id"],
                    plan["impact_report_id"],
                    plan["reviewed_case_id"],
                    plan["reviewed_case_digest"],
                    plan["status"],
                    refs["acceptance_criteria_id"],
                    refs["test_plan_id"],
                    refs["test_data_plan_id"],
                    refs["coverage_report_id"],
                    created_by,
                ),
            )
            created = cursor.rowcount == 1
            cursor.execute(
                """
                SELECT change_request_id, project_id, analysis_case_id,
                       impact_report_id, reviewed_case_id, reviewed_case_digest,
                       status, acceptance_criteria_id, test_plan_id,
                       test_data_plan_id, coverage_report_id, created_by, created_at
                FROM change_orchestrations
                WHERE orchestration_id = %s
                """,
                (plan["orchestration_id"],),
            )
            row = cursor.fetchone()
        expected = (
            plan["change_request_id"],
            plan["project_id"],
            plan["analysis_case_id"],
            plan["impact_report_id"],
            plan["reviewed_case_id"],
            plan["reviewed_case_digest"],
            plan["status"],
            refs["acceptance_criteria_id"],
            refs["test_plan_id"],
            refs["test_data_plan_id"],
            refs["coverage_report_id"],
        )
        if row is None or tuple(row[:11]) != expected:
            raise PersistenceConflictError(
                "Change Orchestration identity has different immutable content"
            )
        return ChangeOrchestrationRecord(
            orchestration_id=str(plan["orchestration_id"]),
            status=str(plan["status"]),
            created_at=row[12],
            created=created,
        )

    def get(self, orchestration_id: str) -> dict[str, Any] | None:
        artifact = self._artifacts.get(orchestration_id)
        if artifact is None:
            return None
        if artifact.get("artifact_type") != "ChangeOrchestrationPlan":
            raise PersistenceConflictError("Orchestration ledger points to wrong Artifact type")
        return artifact

    def latest_bundle(self, change_request_id: str) -> dict[str, Any] | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT orchestration_id
                FROM change_orchestrations
                WHERE change_request_id = %s
                  AND status IN ('ready', 'blocked')
                ORDER BY created_at DESC, orchestration_id DESC
                LIMIT 1
                """,
                (change_request_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return self.bundle(str(row[0]))

    def bundle(self, orchestration_id: str) -> dict[str, Any]:
        """Load one immutable planning bundle by explicit version identity."""

        plan = self.get(orchestration_id)
        if plan is None:
            raise PersistenceConflictError("Orchestration ledger has no plan Artifact")
        refs = cast(dict[str, str], plan["artifact_refs"])
        return {
            "orchestration": plan,
            "acceptance_criteria": self._required_artifact(
                refs["acceptance_criteria_id"], "AcceptanceCriteria"
            ),
            "test_plan": self._required_artifact(refs["test_plan_id"], "TestPlan"),
            "test_data_plan": self._required_artifact(
                refs["test_data_plan_id"], "TestDataPlan"
            ),
            "coverage_report": self._required_artifact(
                refs["coverage_report_id"], "BusinessCoverageReport"
            ),
        }

    def _required_artifact(self, artifact_id: str, artifact_type: str) -> dict[str, Any]:
        artifact = self._artifacts.get(artifact_id)
        if artifact is None or artifact.get("artifact_type") != artifact_type:
            raise PersistenceConflictError(
                f"Required {artifact_type} Artifact is missing: {artifact_id}"
            )
        return artifact


def _artifact_id(artifact: dict[str, Any]) -> str:
    keys = {
        "AcceptanceCriteria": "acceptance_criteria_id",
        "TestPlan": "test_plan_id",
        "TestDataPlan": "test_data_plan_id",
        "BusinessCoverageReport": "coverage_report_id",
        "ChangeOrchestrationPlan": "orchestration_id",
    }
    return str(artifact[keys[str(artifact["artifact_type"])]])
