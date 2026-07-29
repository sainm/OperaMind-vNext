"""Canonical natural-language Test Case proposal and revision persistence."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from psycopg import Connection
from psycopg.types.json import Jsonb

from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres.artifact_repository import ArtifactRepository
from operamind.infrastructure.postgres.change_orchestration_repository import (
    ChangeOrchestrationRepository,
)
from operamind.infrastructure.postgres.errors import PersistenceConflictError

if TYPE_CHECKING:
    from operamind.application.test_case_revision import TestCaseRevisionPlan


@dataclass(frozen=True, slots=True)
class TestCaseProposalRecord:
    proposal_id: str
    analysis_status: str
    created_at: datetime
    created: bool


@dataclass(frozen=True, slots=True)
class TestCaseRevisionRecord:
    revision_id: str
    target_orchestration_id: str
    created_at: datetime
    created: bool


@dataclass(frozen=True, slots=True)
class TestCaseStaleScope:
    run_ids: tuple[str, ...]
    artifact_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    closure_result_ids: tuple[str, ...]


class TestCaseRevisionRepository:
    """Persist immutable proposals, one applied revision, and explicit stale scope."""

    def __init__(self, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._connection = connection
        self._artifacts = ArtifactRepository(connection, contracts)
        self._orchestrations = ChangeOrchestrationRepository(connection, contracts)

    def store_proposal(
        self, *, proposal: dict[str, Any], created_by: str
    ) -> TestCaseProposalRecord:
        if not created_by.strip():
            raise ValueError("Test Case proposal actor must not be blank")
        if proposal.get("artifact_type") != "TestCaseChangeProposal":
            raise ValueError("Expected TestCaseChangeProposal Artifact")
        proposal_id = str(proposal["proposal_id"])
        instruction_digest = hashlib.sha256(str(proposal["instruction"]).encode()).hexdigest()
        with self._connection.transaction(), self._connection.cursor() as cursor:
            self._artifacts.store(
                artifact_id=proposal_id,
                project_id=str(proposal["project_id"]),
                analysis_case_id=self._analysis_case_id(str(proposal["source_orchestration_id"])),
                artifact=proposal,
            )
            cursor.execute(
                """
                INSERT INTO test_case_change_proposals (
                    proposal_id, change_request_id, project_id,
                    source_orchestration_id, source_test_plan_id,
                    analysis_status, instruction_digest, created_by
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    proposal_id,
                    proposal["change_request_id"],
                    proposal["project_id"],
                    proposal["source_orchestration_id"],
                    proposal["source_test_plan_id"],
                    proposal["analysis_status"],
                    instruction_digest,
                    created_by,
                ),
            )
            created = cursor.rowcount == 1
            cursor.execute(
                """
                SELECT change_request_id, project_id, source_orchestration_id,
                       source_test_plan_id, analysis_status, instruction_digest,
                       created_by, created_at
                FROM test_case_change_proposals
                WHERE proposal_id = %s
                """,
                (proposal_id,),
            )
            row = cursor.fetchone()
        expected = (
            proposal["change_request_id"],
            proposal["project_id"],
            proposal["source_orchestration_id"],
            proposal["source_test_plan_id"],
            proposal["analysis_status"],
            instruction_digest,
            created_by,
        )
        if row is None or tuple(row[:7]) != expected:
            raise PersistenceConflictError(
                "Test Case proposal identity has different immutable content"
            )
        return TestCaseProposalRecord(
            proposal_id=proposal_id,
            analysis_status=str(proposal["analysis_status"]),
            created_at=cast(datetime, row[7]),
            created=created,
        )

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        artifact = self._artifacts.get(proposal_id)
        if artifact is None:
            return None
        if artifact.get("artifact_type") != "TestCaseChangeProposal":
            raise PersistenceConflictError("Proposal ledger points to wrong Artifact type")
        return artifact

    def stale_scope(
        self, *, source_orchestration_id: str, source_bundle: dict[str, Any]
    ) -> TestCaseStaleScope:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT run_id, execution_result_id
                FROM test_data_execution_runs
                WHERE orchestration_id = %s
                ORDER BY started_at, run_id
                """,
                (source_orchestration_id,),
            )
            run_rows = cursor.fetchall()
            run_ids = [str(row[0]) for row in run_rows]
            result_ids = [str(row[1]) for row in run_rows]
            if run_ids:
                cursor.execute(
                    """
                    SELECT evidence_ref
                    FROM test_data_execution_evidence
                    WHERE run_id = ANY(%s)
                    ORDER BY evidence_ref
                    """,
                    (run_ids,),
                )
                test_data_evidence = [str(row[0]) for row in cursor.fetchall()]
            else:
                test_data_evidence = []
            cursor.execute(
                """
                SELECT closure_result_id, ui_verification_result_id
                FROM change_closure_results
                WHERE orchestration_id = %s
                ORDER BY created_at, closure_result_id
                """,
                (source_orchestration_id,),
            )
            closure_rows = cursor.fetchall()
            closure_ids = [str(row[0]) for row in closure_rows]
            orchestration = cast(dict[str, Any], source_bundle["orchestration"])
            cursor.execute(
                """
                SELECT artifact_id
                FROM artifact_records
                WHERE project_id = %s
                  AND analysis_case_id = %s
                  AND artifact_type = 'UiVerificationResult'
                  AND schema_version = 'v2'
                  AND payload ->> 'orchestration_id' = %s
                ORDER BY created_at, artifact_id
                """,
                (
                    orchestration["project_id"],
                    orchestration["analysis_case_id"],
                    source_orchestration_id,
                ),
            )
            ui_result_ids = sorted(
                {str(row[1]) for row in closure_rows if row[1] is not None}
                | {str(row[0]) for row in cursor.fetchall()}
            )
        refs = cast(dict[str, str], orchestration["artifact_refs"])
        artifact_refs = {
            source_orchestration_id,
            *refs.values(),
            *result_ids,
            *closure_ids,
            *ui_result_ids,
        }
        return TestCaseStaleScope(
            run_ids=tuple(sorted(set(run_ids))),
            artifact_refs=tuple(sorted(artifact_refs)),
            evidence_refs=tuple(sorted(set(test_data_evidence))),
            closure_result_ids=tuple(sorted(set(closure_ids))),
        )

    def persist_revision(self, *, plan: TestCaseRevisionPlan) -> TestCaseRevisionRecord:
        revision = plan.revision
        source_id = str(revision["source_orchestration_id"])
        target_id = str(revision["target_orchestration_id"])
        proposal_id = str(revision["proposal_id"])
        project_id = str(revision["project_id"])
        with self._connection.transaction():
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT status, change_request_id
                    FROM change_orchestrations
                    WHERE orchestration_id = %s AND project_id = %s
                    FOR UPDATE
                    """,
                    (source_id, project_id),
                )
                source = cursor.fetchone()
                if source is None:
                    raise ValueError("Source Test Case Orchestration does not exist")
                cursor.execute(
                    """
                    SELECT source_orchestration_id, analysis_status
                    FROM test_case_change_proposals
                    WHERE proposal_id = %s AND project_id = %s
                    FOR UPDATE
                    """,
                    (proposal_id, project_id),
                )
                proposal = cursor.fetchone()
                if proposal is None or str(proposal[0]) != source_id:
                    raise ValueError("Test Case proposal source differs")
                cursor.execute(
                    """
                    SELECT revision_id, target_orchestration_id, created_at
                    FROM test_case_revisions
                    WHERE proposal_id = %s
                    """,
                    (proposal_id,),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    existing_artifact = self._artifacts.get(str(existing[0]))
                    if existing_artifact != revision:
                        raise PersistenceConflictError(
                            "Test Case proposal already has a different revision"
                        )
                    return TestCaseRevisionRecord(
                        revision_id=str(existing[0]),
                        target_orchestration_id=str(existing[1]),
                        created_at=cast(datetime, existing[2]),
                        created=False,
                    )
                if str(source[0]) not in {"ready", "blocked"}:
                    raise ValueError("Source Test Case version is already stale")
            self._orchestrations.persist(
                result=plan.orchestration,
                created_by=str(revision["applied_by"]),
            )
            self._artifacts.store(
                artifact_id=str(revision["revision_id"]),
                project_id=project_id,
                analysis_case_id=str(plan.orchestration.orchestration["analysis_case_id"]),
                artifact=revision,
            )
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE change_orchestrations
                    SET status = 'superseded',
                        superseded_by_orchestration_id = %s,
                        superseded_at = now()
                    WHERE orchestration_id = %s
                      AND project_id = %s
                      AND status IN ('ready', 'blocked')
                    """,
                    (target_id, source_id, project_id),
                )
                if cursor.rowcount != 1:
                    raise PersistenceConflictError(
                        "Source Test Case version changed during revision"
                    )
                if str(plan.orchestration.orchestration["status"]) == "ready":
                    cursor.execute(
                        """
                        UPDATE analysis_cases
                        SET status = CASE
                                WHEN status = 'editing' THEN 'editing'
                                ELSE 'verifying_ui'
                            END,
                            updated_at = now()
                        WHERE analysis_case_id = %s AND project_id = %s
                        """,
                        (
                            plan.orchestration.orchestration["analysis_case_id"],
                            project_id,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise PersistenceConflictError(
                            "Revised Test Case Analysis Case scope disappeared"
                        )
                cursor.execute(
                    """
                    INSERT INTO test_case_revisions (
                        revision_id, proposal_id, change_request_id, project_id,
                        source_orchestration_id, target_orchestration_id,
                        source_test_plan_id, target_test_plan_id,
                        stale_run_ids, stale_artifact_refs, stale_evidence_refs,
                        stale_closure_result_ids, applied_by, revision_kind,
                        undo_of_revision_id
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        revision["revision_id"],
                        proposal_id,
                        revision["change_request_id"],
                        project_id,
                        source_id,
                        target_id,
                        revision["source_test_plan_id"],
                        revision["target_test_plan_id"],
                        Jsonb(revision["stale_run_ids"]),
                        Jsonb(revision["stale_artifact_refs"]),
                        Jsonb(revision["stale_evidence_refs"]),
                        Jsonb(revision["stale_closure_result_ids"]),
                        revision["applied_by"],
                        revision.get("revision_kind", "modification"),
                        revision.get("undo_of_revision_id"),
                    ),
                )
                cursor.execute(
                    """
                    SELECT target_orchestration_id, created_at
                    FROM test_case_revisions
                    WHERE revision_id = %s
                    """,
                    (revision["revision_id"],),
                )
                row = cursor.fetchone()
        if row is None or str(row[0]) != target_id:
            raise PersistenceConflictError("Test Case revision persistence failed")
        return TestCaseRevisionRecord(
            revision_id=str(revision["revision_id"]),
            target_orchestration_id=target_id,
            created_at=cast(datetime, row[1]),
            created=True,
        )

    def latest_state(self, change_request_id: str) -> dict[str, Any] | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT proposal.proposal_id, revision.revision_id,
                       proposal.analysis_status, proposal.created_at
                FROM test_case_change_proposals AS proposal
                LEFT JOIN test_case_revisions AS revision
                  ON revision.proposal_id = proposal.proposal_id
                WHERE proposal.change_request_id = %s
                ORDER BY proposal.created_at DESC, proposal.proposal_id DESC
                LIMIT 1
                """,
                (change_request_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        proposal = self.get_proposal(str(row[0]))
        if proposal is None:
            raise PersistenceConflictError("Proposal row has no Artifact")
        revision = self._artifacts.get(str(row[1])) if row[1] is not None else None
        if revision is not None and revision.get("artifact_type") != "TestCaseRevision":
            raise PersistenceConflictError("Revision row points to wrong Artifact type")
        return {
            "state": (
                "applied"
                if revision is not None
                else "ready_for_confirmation"
                if proposal["analysis_status"] == "deterministic"
                else str(proposal["analysis_status"])
            ),
            "proposal": proposal,
            "revision": revision,
            "created_at": cast(datetime, row[3]).isoformat(),
        }

    def get_revision(self, revision_id: str) -> dict[str, Any] | None:
        artifact = self._artifacts.get(revision_id)
        if artifact is None:
            return None
        if artifact.get("artifact_type") != "TestCaseRevision":
            raise PersistenceConflictError("Revision ledger points to wrong Artifact type")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT revision_kind, undo_of_revision_id
                FROM test_case_revisions
                WHERE revision_id = %s
                """,
                (revision_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise PersistenceConflictError("Revision Artifact has no normalized row")
        expected_kind = str(artifact.get("revision_kind", "modification"))
        expected_undo = artifact.get("undo_of_revision_id")
        if (str(row[0]), row[1]) != (expected_kind, expected_undo):
            raise PersistenceConflictError("Revision undo metadata differs")
        return artifact

    def undo_for_revision(self, revision_id: str) -> dict[str, Any] | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT revision_id
                FROM test_case_revisions
                WHERE undo_of_revision_id = %s
                """,
                (revision_id,),
            )
            row = cursor.fetchone()
        return self.get_revision(str(row[0])) if row is not None else None

    def revision_history(self, change_request_id: str) -> list[dict[str, Any]]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT revision.revision_id, revision.created_at,
                       revision.revision_kind, revision.undo_of_revision_id,
                       target.status,
                       EXISTS (
                           SELECT 1
                           FROM test_case_revisions AS undo
                           WHERE undo.undo_of_revision_id = revision.revision_id
                       ) AS was_undone
                FROM test_case_revisions AS revision
                JOIN change_orchestrations AS target
                  ON target.orchestration_id = revision.target_orchestration_id
                 AND target.project_id = revision.project_id
                WHERE revision.change_request_id = %s
                ORDER BY (target.status IN ('ready', 'blocked')) DESC,
                         revision.created_at DESC, revision.revision_id DESC
                """,
                (change_request_id,),
            )
            rows = cursor.fetchall()
        history: list[dict[str, Any]] = []
        for revision_id, created_at, kind, undo_of, target_status, was_undone in rows:
            artifact = self.get_revision(str(revision_id))
            if artifact is None:
                raise PersistenceConflictError("Revision history Artifact is missing")
            status = (
                "undone"
                if bool(was_undone)
                else "current"
                if str(target_status) in {"ready", "blocked"}
                else "superseded"
            )
            history.append(
                {
                    "revision": artifact,
                    "created_at": cast(datetime, created_at).isoformat(),
                    "status": status,
                    "revision_kind": str(kind),
                    "undo_of_revision_id": str(undo_of) if undo_of is not None else None,
                    "can_undo": status == "current",
                }
            )
        return history

    def _analysis_case_id(self, orchestration_id: str) -> str:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT analysis_case_id
                FROM change_orchestrations
                WHERE orchestration_id = %s
                """,
                (orchestration_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise ValueError("Source Test Case Orchestration does not exist")
        return str(row[0])
