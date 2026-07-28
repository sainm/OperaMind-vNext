"""Transactional natural-language Test Case revision workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from psycopg import Connection

from operamind.application.test_case_revision import (
    TestCaseChangeAnalyzer,
    TestCaseRevisionPlanner,
    build_undo_proposal,
    resolve_ambiguities,
)
from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres.change_orchestration_repository import (
    ChangeOrchestrationRepository,
)
from operamind.infrastructure.postgres.test_case_revision_repository import (
    TestCaseRevisionRepository,
)


class TestCaseRevisionService:
    """Preview every change and apply it only after one explicit confirmation."""

    def __init__(self, *, connection: Connection[Any], repository_root: Path) -> None:
        root = repository_root.resolve()
        self._root = root
        contracts = ContractCatalog.load(root / "contracts")
        self._orchestrations = ChangeOrchestrationRepository(connection, contracts)
        self._revisions = TestCaseRevisionRepository(connection, contracts)
        self._analyzer = TestCaseChangeAnalyzer(repository_root=root)
        self._planner = TestCaseRevisionPlanner(repository_root=root)

    def propose(self, *, change_request_id: str, instruction: str, actor: str) -> dict[str, Any]:
        bundle = self._orchestrations.latest_bundle(change_request_id)
        if bundle is None:
            raise ValueError("Generated Test Case does not exist")
        analysis = self._analyzer.analyze(bundle=bundle, instruction=instruction)
        record = self._revisions.store_proposal(
            proposal=analysis.proposal,
            created_by=actor,
        )
        return {
            "created": record.created,
            "state": (
                "ready_for_confirmation"
                if analysis.deterministic
                else analysis.proposal["analysis_status"]
            ),
            "proposal": analysis.proposal,
            "revision": None,
            "bundle": bundle,
        }

    def confirm(
        self,
        *,
        change_request_id: str,
        proposal_id: str,
        selections: dict[str, str],
        actor: str,
    ) -> dict[str, Any]:
        proposal = self._revisions.get_proposal(proposal_id)
        if proposal is None or proposal["change_request_id"] != change_request_id:
            raise ValueError("Test Case change proposal does not exist")
        status = str(proposal["analysis_status"])
        if status not in {"deterministic", "needs_confirmation"}:
            raise ValueError("Test Case change proposal cannot be confirmed")
        if status == "deterministic" and selections:
            raise ValueError("Deterministic Test Case proposal has no ambiguity selections")
        operations = (
            resolve_ambiguities(proposal, selections)
            if status == "needs_confirmation"
            else cast(list[dict[str, Any]], proposal["operations"])
        )
        return self._apply(
            proposal=proposal,
            operations=operations,
            selections=selections,
            actor=actor,
            proposal_created=False,
        )

    def undo(
        self,
        *,
        change_request_id: str,
        revision_id: str,
        idempotency_key: str,
        actor: str,
    ) -> dict[str, Any]:
        revision = self._revisions.get_revision(revision_id)
        if revision is None or revision["change_request_id"] != change_request_id:
            raise ValueError("Test Case revision does not exist")
        existing = self._revisions.undo_for_revision(revision_id)
        if existing is not None:
            proposal = self._revisions.get_proposal(str(existing["proposal_id"]))
            if proposal is None:
                raise ValueError("Test Case undo proposal does not exist")
            return {
                "created": False,
                "state": "applied",
                "proposal": proposal,
                "revision": existing,
                "bundle": self._orchestrations.bundle(str(existing["target_orchestration_id"])),
            }
        current_bundle = self._orchestrations.latest_bundle(change_request_id)
        if current_bundle is None:
            raise ValueError("Generated Test Case does not exist")
        proposal = build_undo_proposal(
            repository_root=self._root,
            current_bundle=current_bundle,
            revision=revision,
            idempotency_key=idempotency_key,
        )
        proposal_record = self._revisions.store_proposal(
            proposal=proposal,
            created_by=actor,
        )
        restore_bundle = self._orchestrations.bundle(str(revision["source_orchestration_id"]))
        stale = self._revisions.stale_scope(
            source_orchestration_id=str(revision["target_orchestration_id"]),
            source_bundle=current_bundle,
        )
        plan = self._planner.restore(
            source_bundle=current_bundle,
            restore_bundle=restore_bundle,
            proposal=proposal,
            applied_by=actor,
            stale_run_ids=list(stale.run_ids),
            stale_artifact_refs=list(stale.artifact_refs),
            stale_evidence_refs=list(stale.evidence_refs),
            stale_closure_result_ids=list(stale.closure_result_ids),
        )
        record = self._revisions.persist_revision(plan=plan)
        return {
            "created": proposal_record.created or record.created,
            "state": "applied",
            "proposal": proposal,
            "revision": plan.revision,
            "bundle": self._orchestrations.bundle(record.target_orchestration_id),
        }

    def state(self, change_request_id: str) -> dict[str, Any]:
        return {
            "latest": self._revisions.latest_state(change_request_id),
            "history": self._revisions.revision_history(change_request_id),
        }

    def _apply(
        self,
        *,
        proposal: dict[str, Any],
        operations: list[dict[str, Any]],
        selections: dict[str, str],
        actor: str,
        proposal_created: bool,
    ) -> dict[str, Any]:
        source_bundle = self._orchestrations.bundle(str(proposal["source_orchestration_id"]))
        stale = self._revisions.stale_scope(
            source_orchestration_id=str(proposal["source_orchestration_id"]),
            source_bundle=source_bundle,
        )
        plan = self._planner.plan(
            source_bundle=source_bundle,
            proposal=proposal,
            operations=operations,
            applied_by=actor,
            selections=selections,
            stale_run_ids=list(stale.run_ids),
            stale_artifact_refs=list(stale.artifact_refs),
            stale_evidence_refs=list(stale.evidence_refs),
            stale_closure_result_ids=list(stale.closure_result_ids),
        )
        record = self._revisions.persist_revision(plan=plan)
        return {
            "created": proposal_created or record.created,
            "state": "applied",
            "proposal": proposal,
            "revision": plan.revision,
            "bundle": self._orchestrations.bundle(record.target_orchestration_id),
        }
