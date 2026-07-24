"""Approval Grant binding for revised Test Case execution scopes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from psycopg import Connection
from psycopg.types.json import Jsonb

from operamind.contracts import ContractCatalog
from operamind.domain.test_case_execution_scope import (
    TestCaseExecutionScopeComparison,
    compare_test_case_execution_scope,
)
from operamind.infrastructure.postgres.artifact_repository import ArtifactRepository
from operamind.infrastructure.postgres.change_orchestration_repository import (
    ChangeOrchestrationRepository,
)
from operamind.infrastructure.postgres.errors import PersistenceConflictError


@dataclass(frozen=True, slots=True)
class TestCaseExecutionAuthorizationRecord:
    authorization_id: str
    revision_id: str
    target_orchestration_id: str
    approval_grant_id: str
    decision: str
    confirmed_by: str
    created_at: datetime
    created: bool


class TestCaseExecutionAuthorizationRepository:
    """Compare immutable Case versions and bind a Grant to the target scope."""

    def __init__(self, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._connection = connection
        self._artifacts = ArtifactRepository(connection, contracts)
        self._orchestrations = ChangeOrchestrationRepository(connection, contracts)

    def state(self, *, target_orchestration_id: str, at: datetime) -> dict[str, Any]:
        if at.utcoffset() is None:
            raise ValueError("Test Case execution authorization time must include a timezone")
        revision = self._revision(target_orchestration_id)
        target_bundle = self._orchestrations.bundle(target_orchestration_id)
        target = cast(dict[str, Any], target_bundle["orchestration"])
        common_scope = {
            "project_id": target["project_id"],
            "analysis_case_id": target["analysis_case_id"],
        }
        grants = self._eligible_grants(
            project_id=str(target["project_id"]),
            analysis_case_id=str(target["analysis_case_id"]),
            ui_scenario_ids=_ui_scenario_ids(target_bundle),
            at=at,
        )
        if revision is None:
            return {
                **common_scope,
                **_state(
                    authorized=bool(grants),
                    status="original" if grants else "grant_required",
                    grant_id=grants[0] if grants else None,
                    blocking_reason=(
                        None
                        if grants
                        else "No active Approval Grant permits TestDataPlan execution"
                    ),
                ),
            }
        comparison = self._comparison(revision, target_bundle)
        existing = self._latest_valid_authorization(
            revision=revision,
            comparison=comparison,
            eligible_grants=frozenset(grants),
        )
        common = {
            **common_scope,
            "revision_id": revision["revision_id"],
            "source_orchestration_id": revision["source_orchestration_id"],
            "target_orchestration_id": target_orchestration_id,
            "scope_comparison": comparison.to_dict(),
        }
        if existing is not None:
            return {
                **common,
                **_state(
                    authorized=True,
                    status=existing.decision,
                    grant_id=existing.approval_grant_id,
                    blocking_reason=None,
                    authorization_id=existing.authorization_id,
                    confirmed_by=existing.confirmed_by,
                ),
            }
        if not grants:
            return {
                **common,
                **_state(
                    authorized=False,
                    status="grant_required",
                    grant_id=None,
                    blocking_reason=("No active Approval Grant permits TestDataPlan execution"),
                ),
            }
        if comparison.changed:
            return {
                **common,
                **_state(
                    authorized=False,
                    status="confirmation_required",
                    grant_id=grants[0],
                    blocking_reason="Test Case execution scope requires confirmation",
                ),
            }
        return {
            **common,
            **_state(
                authorized=True,
                status="reusable",
                grant_id=grants[0],
                blocking_reason=None,
            ),
        }

    def confirm(
        self,
        *,
        target_orchestration_id: str,
        approval_grant_id: str,
        target_scope_digest: str,
        actor: str,
        at: datetime,
    ) -> TestCaseExecutionAuthorizationRecord:
        if not actor.strip():
            raise ValueError("Test Case execution confirmer must not be blank")
        revision = self._required_revision(target_orchestration_id)
        target_bundle = self._orchestrations.bundle(target_orchestration_id)
        comparison = self._comparison(revision, target_bundle)
        if not comparison.changed:
            raise ValueError("Unchanged Test Case execution scope does not require confirmation")
        if comparison.target_scope_digest != target_scope_digest:
            raise ValueError("Test Case execution scope changed after it was displayed")
        target = cast(dict[str, Any], target_bundle["orchestration"])
        self._require_eligible_grant(
            grant_id=approval_grant_id,
            project_id=str(target["project_id"]),
            analysis_case_id=str(target["analysis_case_id"]),
            ui_scenario_ids=_ui_scenario_ids(target_bundle),
            at=at,
        )
        return self._persist(
            revision=revision,
            comparison=comparison,
            grant_id=approval_grant_id,
            decision="reconfirmed",
            actor=actor,
        )

    def authorize_for_run(
        self,
        *,
        target_orchestration_id: str,
        approval_grant_id: str,
        actor: str,
        at: datetime,
    ) -> TestCaseExecutionAuthorizationRecord | None:
        revision = self._revision(target_orchestration_id)
        if revision is None:
            return None
        target_bundle = self._orchestrations.bundle(target_orchestration_id)
        target = cast(dict[str, Any], target_bundle["orchestration"])
        self._require_eligible_grant(
            grant_id=approval_grant_id,
            project_id=str(target["project_id"]),
            analysis_case_id=str(target["analysis_case_id"]),
            ui_scenario_ids=_ui_scenario_ids(target_bundle),
            at=at,
        )
        comparison = self._comparison(revision, target_bundle)
        existing = self._authorization_for_grant(
            revision=revision,
            comparison=comparison,
            grant_id=approval_grant_id,
        )
        if existing is not None:
            return existing
        if comparison.changed:
            raise ValueError("Test Case execution scope requires confirmation")
        return self._persist(
            revision=revision,
            comparison=comparison,
            grant_id=approval_grant_id,
            decision="reused",
            actor="system:scope-unchanged",
        )

    def _comparison(
        self, revision: dict[str, Any], target_bundle: dict[str, Any]
    ) -> TestCaseExecutionScopeComparison:
        source_bundle = self._orchestrations.bundle(str(revision["source_orchestration_id"]))
        return compare_test_case_execution_scope(source_bundle, target_bundle)

    def _revision(self, target_orchestration_id: str) -> dict[str, Any] | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT revision_id
                FROM test_case_revisions
                WHERE target_orchestration_id = %s
                """,
                (target_orchestration_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        artifact = self._artifacts.get(str(row[0]))
        if artifact is None or artifact.get("artifact_type") != "TestCaseRevision":
            raise PersistenceConflictError("Test Case Revision Artifact is missing")
        return artifact

    def _required_revision(self, target_orchestration_id: str) -> dict[str, Any]:
        revision = self._revision(target_orchestration_id)
        if revision is None:
            raise ValueError("Target Orchestration is not a Test Case revision")
        return revision

    def _eligible_grants(
        self,
        *,
        project_id: str,
        analysis_case_id: str,
        ui_scenario_ids: tuple[str, ...],
        at: datetime,
    ) -> list[str]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT grant_record.approval_grant_id,
                       grant_record.allowed_ui_scenarios
                FROM approval_grants AS grant_record
                WHERE grant_record.project_id = %s
                  AND grant_record.analysis_case_id = %s
                  AND grant_record.expires_at > %s
                  AND grant_record.allowed_actions
                      @> '["run_test", "record_evidence"]'::jsonb
                  AND NOT EXISTS (
                      SELECT 1 FROM approval_grant_events AS event
                      WHERE event.approval_grant_id = grant_record.approval_grant_id
                        AND event.project_id = grant_record.project_id
                        AND event.event_type = 'revoked'
                  )
                ORDER BY grant_record.expires_at DESC,
                         grant_record.approval_grant_id DESC
                """,
                (project_id, analysis_case_id, at.astimezone(UTC)),
            )
            rows = cursor.fetchall()
        expected = tuple(sorted(ui_scenario_ids))
        return [str(row[0]) for row in rows if tuple(sorted(_strings(row[1]))) == expected]

    def _require_eligible_grant(
        self,
        *,
        grant_id: str,
        project_id: str,
        analysis_case_id: str,
        ui_scenario_ids: tuple[str, ...],
        at: datetime,
    ) -> None:
        eligible = self._eligible_grants(
            project_id=project_id,
            analysis_case_id=analysis_case_id,
            ui_scenario_ids=ui_scenario_ids,
            at=at,
        )
        if grant_id not in eligible:
            raise ValueError("Approval Grant does not permit the revised Test Case scope")

    def _latest_valid_authorization(
        self,
        *,
        revision: dict[str, Any],
        comparison: TestCaseExecutionScopeComparison,
        eligible_grants: frozenset[str],
    ) -> TestCaseExecutionAuthorizationRecord | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT authorization_id
                FROM test_case_execution_authorizations
                WHERE revision_id = %s AND target_orchestration_id = %s
                ORDER BY created_at DESC, authorization_id DESC
                """,
                (revision["revision_id"], revision["target_orchestration_id"]),
            )
            rows = cursor.fetchall()
        for row in rows:
            record = self._load(str(row[0]), revision, comparison)
            if record.approval_grant_id in eligible_grants:
                return record
        return None

    def _authorization_for_grant(
        self,
        *,
        revision: dict[str, Any],
        comparison: TestCaseExecutionScopeComparison,
        grant_id: str,
    ) -> TestCaseExecutionAuthorizationRecord | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT authorization_id
                FROM test_case_execution_authorizations
                WHERE revision_id = %s
                  AND target_orchestration_id = %s
                  AND approval_grant_id = %s
                  AND target_scope_digest = %s
                ORDER BY created_at DESC, authorization_id DESC
                LIMIT 1
                """,
                (
                    revision["revision_id"],
                    revision["target_orchestration_id"],
                    grant_id,
                    comparison.target_scope_digest,
                ),
            )
            row = cursor.fetchone()
        return self._load(str(row[0]), revision, comparison) if row is not None else None

    def _persist(
        self,
        *,
        revision: dict[str, Any],
        comparison: TestCaseExecutionScopeComparison,
        grant_id: str,
        decision: str,
        actor: str,
    ) -> TestCaseExecutionAuthorizationRecord:
        material = {
            "revision_id": revision["revision_id"],
            "target_orchestration_id": revision["target_orchestration_id"],
            "approval_grant_id": grant_id,
            "project_id": revision["project_id"],
            "decision": decision,
            "source_scope_digest": comparison.source_scope_digest,
            "target_scope_digest": comparison.target_scope_digest,
            "changed_dimensions": list(comparison.changed_dimensions),
        }
        payload_digest = _digest(material)
        authorization_id = f"test-case-execution-auth-{payload_digest[:24]}"
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status
                FROM change_orchestrations
                WHERE orchestration_id = %s AND project_id = %s
                FOR UPDATE
                """,
                (revision["target_orchestration_id"], revision["project_id"]),
            )
            target = cursor.fetchone()
            if target is None or str(target[0]) != "ready":
                raise ValueError(
                    "Test Case execution authorization requires the current ready Orchestration"
                )
            cursor.execute(
                """
                INSERT INTO test_case_execution_authorizations (
                    authorization_id, revision_id, target_orchestration_id,
                    approval_grant_id, project_id, decision,
                    source_scope_digest, target_scope_digest,
                    changed_dimensions, confirmed_by, payload_digest
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                ) ON CONFLICT DO NOTHING
                """,
                (
                    authorization_id,
                    revision["revision_id"],
                    revision["target_orchestration_id"],
                    grant_id,
                    revision["project_id"],
                    decision,
                    comparison.source_scope_digest,
                    comparison.target_scope_digest,
                    Jsonb(list(comparison.changed_dimensions)),
                    actor,
                    payload_digest,
                ),
            )
            created = cursor.rowcount == 1
        record = self._load(authorization_id, revision, comparison)
        return TestCaseExecutionAuthorizationRecord(
            authorization_id=record.authorization_id,
            revision_id=record.revision_id,
            target_orchestration_id=record.target_orchestration_id,
            approval_grant_id=record.approval_grant_id,
            decision=record.decision,
            confirmed_by=record.confirmed_by,
            created_at=record.created_at,
            created=created,
        )

    def _load(
        self,
        authorization_id: str,
        revision: dict[str, Any],
        comparison: TestCaseExecutionScopeComparison,
    ) -> TestCaseExecutionAuthorizationRecord:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT revision_id, target_orchestration_id, approval_grant_id,
                       project_id, decision, source_scope_digest,
                       target_scope_digest, changed_dimensions, confirmed_by,
                       payload_digest, created_at
                FROM test_case_execution_authorizations
                WHERE authorization_id = %s
                """,
                (authorization_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise PersistenceConflictError("Test Case execution authorization is missing")
        material = {
            "revision_id": str(row[0]),
            "target_orchestration_id": str(row[1]),
            "approval_grant_id": str(row[2]),
            "project_id": str(row[3]),
            "decision": str(row[4]),
            "source_scope_digest": str(row[5]),
            "target_scope_digest": str(row[6]),
            "changed_dimensions": list(cast(list[object], row[7])),
        }
        expected = (
            revision["revision_id"],
            revision["target_orchestration_id"],
            revision["project_id"],
            comparison.source_scope_digest,
            comparison.target_scope_digest,
            list(comparison.changed_dimensions),
            _digest(material),
        )
        actual = (
            material["revision_id"],
            material["target_orchestration_id"],
            material["project_id"],
            material["source_scope_digest"],
            material["target_scope_digest"],
            material["changed_dimensions"],
            str(row[9]),
        )
        if actual != expected:
            raise PersistenceConflictError(
                "Test Case execution authorization immutable scope differs"
            )
        decision = str(row[4])
        if (decision == "reused") != (not comparison.changed):
            raise PersistenceConflictError(
                "Test Case execution authorization decision differs from scope"
            )
        return TestCaseExecutionAuthorizationRecord(
            authorization_id=authorization_id,
            revision_id=str(row[0]),
            target_orchestration_id=str(row[1]),
            approval_grant_id=str(row[2]),
            decision=decision,
            confirmed_by=str(row[8]),
            created_at=cast(datetime, row[10]),
            created=False,
        )


def _state(
    *,
    authorized: bool,
    status: str,
    grant_id: str | None,
    blocking_reason: str | None,
    authorization_id: str | None = None,
    confirmed_by: str | None = None,
) -> dict[str, Any]:
    return {
        "authorized": authorized,
        "status": status,
        "approval_grant_id": grant_id,
        "authorization_id": authorization_id,
        "confirmed_by": confirmed_by,
        "blocking_reason": blocking_reason,
    }


def _ui_scenario_ids(bundle: dict[str, Any]) -> tuple[str, ...]:
    orchestration = cast(dict[str, Any], bundle["orchestration"])
    return tuple(
        sorted(
            str(item["scenario_id"])
            for item in cast(list[dict[str, Any]], orchestration["ui_scenarios"])
        )
    )


def _strings(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in cast(list[object], value))


def _digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()
