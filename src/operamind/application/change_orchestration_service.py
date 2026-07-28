"""Transactional Canonical Change Request orchestration service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg import Connection

from operamind.application.change_orchestration import (
    ChangeOrchestrationInput,
    ChangeOrchestrationPlanner,
)
from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres.change_orchestration_repository import (
    ChangeOrchestrationRepository,
)


@dataclass(frozen=True, slots=True)
class ChangeOrchestrationServiceResult:
    created: bool
    orchestration: dict[str, Any]
    artifacts: tuple[dict[str, Any], ...]


class ChangeOrchestrationService:
    """Use one current evidence basis for both Web and CLI entry points."""

    def __init__(self, *, connection: Connection[Any], repository_root: Path) -> None:
        self._root = repository_root.resolve()
        contracts = ContractCatalog.load(self._root / "contracts")
        self._repository = ChangeOrchestrationRepository(connection, contracts)
        self._planner = ChangeOrchestrationPlanner(repository_root=self._root)

    def orchestrate(
        self, *, change_request_id: str, actor: str
    ) -> ChangeOrchestrationServiceResult:
        evidence = self._repository.load_evidence(change_request_id)
        generated_test_plan = getattr(evidence, "generated_test_plan", None)
        generated_test_data_plan = getattr(evidence, "generated_test_data_plan", None)
        result = self._planner.plan(
            ChangeOrchestrationInput(
                change_request=evidence.change_request,
                analysis_case_id=evidence.analysis_case_id,
                structured_changes=evidence.structured_changes,
                accepted_structured_change_refs=(evidence.accepted_structured_change_refs),
                impact_report=evidence.impact_report,
                impact_report_state=evidence.impact_report_state,
                impact_confirmation=evidence.impact_confirmation,
                copilot_coding_task_id=getattr(evidence, "copilot_coding_task_id", None),
                generated_test_plan=generated_test_plan,
                generated_test_data_plan=generated_test_data_plan,
            )
        )
        record = self._repository.persist(result=result, created_by=actor)
        return ChangeOrchestrationServiceResult(
            created=record.created,
            orchestration=result.orchestration,
            artifacts=result.artifacts,
        )
