"""Transactional Canonical Change Request orchestration service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from psycopg import Connection

from operamind.application.change_loop_case import ChangeLoopCase
from operamind.application.change_orchestration import (
    ChangeOrchestrationBlockedError,
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
        reviewed_case = _select_reviewed_case(
            repository_root=self._root,
            project_id=str(evidence.change_request["project_id"]),
            repository_revision=str(evidence.impact_report["repository_revision"]),
            stable_keys={
                str(value["stable_key"]) for value in evidence.structured_changes
            },
        )
        result = self._planner.plan(
            ChangeOrchestrationInput(
                change_request=evidence.change_request,
                analysis_case_id=evidence.analysis_case_id,
                structured_changes=evidence.structured_changes,
                accepted_structured_change_refs=(
                    evidence.accepted_structured_change_refs
                ),
                impact_report=evidence.impact_report,
                impact_report_state=evidence.impact_report_state,
                impact_confirmation=evidence.impact_confirmation,
                reviewed_case=reviewed_case,
            )
        )
        record = self._repository.persist(result=result, created_by=actor)
        return ChangeOrchestrationServiceResult(
            created=record.created,
            orchestration=result.orchestration,
            artifacts=result.artifacts,
        )


def _select_reviewed_case(
    *,
    repository_root: Path,
    project_id: str,
    repository_revision: str,
    stable_keys: set[str],
) -> ChangeLoopCase:
    dataset_root = repository_root / "golden-dataset"
    manifest = cast(
        dict[str, Any],
        json.loads((dataset_root / "manifest.golden.json").read_text(encoding="utf-8")),
    )
    if manifest.get("dataset_stage") != "golden" or manifest.get("status") != "frozen":
        raise ChangeOrchestrationBlockedError("Golden Dataset is not frozen")
    project = next(
        (
            value
            for value in cast(list[dict[str, Any]], manifest["projects"])
            if value.get("project_id") == project_id
        ),
        None,
    )
    if project is None or project.get("repository_commit") != repository_revision:
        raise ChangeOrchestrationBlockedError(
            "Golden Dataset does not bind the current repository revision"
        )
    matches: list[ChangeLoopCase] = []
    for entry in cast(list[dict[str, Any]], manifest["cases"]):
        if entry.get("project_id") != project_id:
            continue
        expected_path = (dataset_root / str(entry["expected_changes"])).resolve()
        if not expected_path.is_relative_to(dataset_root.resolve()):
            raise ChangeOrchestrationBlockedError("Golden case path escapes dataset root")
        expected = cast(
            dict[str, Any], json.loads(expected_path.read_text(encoding="utf-8"))
        )
        expected_keys = {
            str(value["stable_key"])
            for value in cast(list[dict[str, Any]], expected["changes"])
        }
        if expected_keys != stable_keys:
            continue
        case_root = expected_path.parent
        case = ChangeLoopCase.load(case_root)
        if str(case.repository["base_revision"]) == repository_revision:
            matches.append(case)
    if len(matches) != 1:
        raise ChangeOrchestrationBlockedError(
            f"Expected exactly one reviewed Golden case, found {len(matches)}"
        )
    return matches[0]
