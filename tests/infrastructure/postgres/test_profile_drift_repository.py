from __future__ import annotations

from typing import Any, cast

from psycopg import Cursor

from operamind.infrastructure.postgres.profile_drift_repository import (
    ProfileDriftRepository,
)


class DependencyCursor:
    def __init__(self) -> None:
        self.query = ""

    def execute(self, query: object, params: object = None) -> None:
        del params
        self.query = " ".join(str(query).split())

    def fetchall(self) -> list[tuple[object, ...]]:
        if "FROM snapshot_memberships" in self.query:
            return [("snapshot-1",)]
        if "FROM impact_reports" in self.query:
            return [("impact-1", "snapshot-1", "graph-other")]
        if "FROM change_orchestrations" in self.query:
            return [("orchestration-1", "impact-1", "test-plan-1")]
        if "FROM edit_results AS result" in self.query:
            return [("edit-result-1", "impact-1", None)]
        if "FROM test_data_execution_runs" in self.query:
            return [("test-data-result-1", "orchestration-1", "grant-other", "run-1")]
        if "artifact_type = 'UiVerificationResult'" in self.query:
            return [("ui-result-1", "orchestration-1", "test-data-result-1")]
        if "FROM test_data_execution_evidence" in self.query:
            return [("evidence-test-1", "run-1")]
        if "FROM change_closure_results" in self.query:
            return [
                (
                    "closure-1",
                    "orchestration-1",
                    "edit-result-1",
                    "test-data-result-1",
                    "ui-result-1",
                )
            ]
        raise AssertionError(f"Unexpected dependency query: {self.query}")


def test_document_profile_drift_propagates_through_every_artifact_layer() -> None:
    repository = cast(ProfileDriftRepository, object.__new__(ProfileDriftRepository))

    impacts = repository._discover_impacts(
        cast(Cursor[Any], DependencyCursor()),
        project_id="project-1",
        profile_type="DocumentConventionProfile",
        profile_version_id="document-profile-v1",
    )

    assert {
        (impact.layer, impact.artifact_type, impact.artifact_id, impact.status)
        for impact in impacts.values()
    } == {
        ("snapshot", "DocumentSnapshot", "snapshot-1", "stale"),
        ("impact", "ImpactReport", "impact-1", "blocked"),
        ("test_plan", "TestPlan", "test-plan-1", "blocked"),
        ("evidence", "EditResult", "edit-result-1", "stale"),
        ("evidence", "TestDataExecutionResult", "test-data-result-1", "stale"),
        ("evidence", "UiVerificationResult", "ui-result-1", "stale"),
        ("evidence", "Evidence", "evidence-test-1", "stale"),
        ("closure", "ChangeClosureResult", "closure-1", "blocked"),
    }
