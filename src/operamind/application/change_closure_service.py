"""Create and persist one ChangeClosureResult from current Canonical evidence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg import Connection

from operamind.application.change_closure import ChangeClosureEvaluator, ChangeClosureInput
from operamind.contracts import ContractCatalog
from operamind.infrastructure.code_graph import GitWorktreeDiffInspector
from operamind.infrastructure.postgres.change_closure_repository import (
    ChangeClosureEvidence,
    ChangeClosureRecord,
    ChangeClosureRepository,
)


@dataclass(frozen=True, slots=True)
class ChangeClosureServiceResult:
    artifact: dict[str, Any]
    record: ChangeClosureRecord


class ChangeClosureService:
    def __init__(self, connection: Connection[Any], contracts: ContractCatalog) -> None:
        self._repository = ChangeClosureRepository(connection, contracts)
        self._evaluator = ChangeClosureEvaluator(contracts)

    def close(self, *, orchestration_id: str, actor: str) -> ChangeClosureServiceResult:
        evidence = self._repository.load_evidence(orchestration_id)
        workspace_evidence_current, workspace_evidence_reason = _workspace_evidence_status(
            evidence
        )
        artifact = self._evaluator.evaluate(
            ChangeClosureInput(
                change_request=evidence.change_request,
                orchestration=evidence.orchestration,
                test_plan=evidence.test_plan,
                test_data_plan=evidence.test_data_plan,
                coverage_report=evidence.coverage_report,
                edit_result=evidence.edit_result,
                changed_line_coverage=evidence.changed_line_coverage,
                test_data_result=evidence.test_data_result,
                ui_result=evidence.ui_result,
                ui_test_case_refs=evidence.ui_test_case_refs,
                verification_only=evidence.verification_only,
                workspace_evidence_current=workspace_evidence_current,
                workspace_evidence_reason=workspace_evidence_reason,
            )
        )
        record = self._repository.persist(
            evidence=evidence,
            artifact=artifact,
            created_by=actor,
        )
        return ChangeClosureServiceResult(artifact=artifact, record=record)


def _workspace_evidence_status(
    evidence: ChangeClosureEvidence,
) -> tuple[bool, str | None]:
    workspace_root = evidence.workspace_root
    edit_result = evidence.edit_result
    if not workspace_root or not isinstance(edit_result, dict):
        return True, None
    try:
        inspected = GitWorktreeDiffInspector().inspect_committed(
            Path(workspace_root),
            base_sha=str(edit_result["base_repository_revision"]),
            allow_unchanged_head=evidence.verification_only,
        )
    except (OSError, ValueError):
        return False, "Code workspace no longer matches committed Edit Result"
    expected_revision = str(
        edit_result.get("result_repository_revision")
        or edit_result["base_repository_revision"]
    )
    if inspected.result_sha != expected_revision:
        return False, "Code workspace HEAD differs from committed Edit Result"
    return True, None
