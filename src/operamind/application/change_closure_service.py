"""Create and persist one ChangeClosureResult from current Canonical evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg import Connection

from operamind.application.change_closure import ChangeClosureEvaluator, ChangeClosureInput
from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres.change_closure_repository import (
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
            )
        )
        record = self._repository.persist(
            evidence=evidence,
            artifact=artifact,
            created_by=actor,
        )
        return ChangeClosureServiceResult(artifact=artifact, record=record)
