from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from operamind.application import change_orchestration_service as service_module
from operamind.application.change_orchestration_service import ChangeOrchestrationService


def test_orchestrate_passes_copilot_plans_from_one_evidence_basis(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    observed: dict[str, Any] = {}
    generated_test_plan = {"test_plan_id": "test-plan-1"}
    generated_test_data_plan = {"test_data_plan_id": "test-data-plan-1"}
    evidence = SimpleNamespace(
        change_request={"project_id": "visiondemo"},
        analysis_case_id="case-1",
        structured_changes=({"change_id": "change-1"},),
        accepted_structured_change_refs=frozenset({"change-1"}),
        impact_report={"repository_revision": "a" * 40},
        impact_report_state="confirmed",
        impact_confirmation={"impact_report_id": "impact-1"},
        copilot_coding_task_id="copilot-task-1",
        generated_test_plan=generated_test_plan,
        generated_test_data_plan=generated_test_data_plan,
    )

    class Repository:
        def __init__(self, connection: object, contracts: object) -> None:
            observed["repository_init"] = (connection, contracts)

        def load_evidence(self, change_request_id: str) -> object:
            observed["loaded"] = change_request_id
            return evidence

        def persist(self, *, result: object, created_by: str) -> object:
            observed["persisted"] = (result, created_by)
            return SimpleNamespace(created=True)

    planned = SimpleNamespace(
        orchestration={"orchestration_id": "orchestration-1"},
        artifacts=({"artifact_type": "TestPlan"},),
    )

    class Planner:
        def __init__(self, *, repository_root: Path) -> None:
            observed["planner_root"] = repository_root

        def plan(self, value: object) -> object:
            observed["plan_input"] = value
            return planned

    contracts = object()
    connection = object()
    monkeypatch.setattr(service_module.ContractCatalog, "load", lambda _path: contracts)
    monkeypatch.setattr(service_module, "ChangeOrchestrationRepository", Repository)
    monkeypatch.setattr(service_module, "ChangeOrchestrationPlanner", Planner)

    result = ChangeOrchestrationService(
        connection=connection, repository_root=tmp_path
    ).orchestrate(change_request_id="request-1", actor="worker-1")

    assert result.created is True
    assert result.orchestration == planned.orchestration
    assert result.artifacts == planned.artifacts
    assert observed["repository_init"] == (connection, contracts)
    assert observed["planner_root"] == tmp_path.resolve()
    assert observed["loaded"] == "request-1"
    plan_input = observed["plan_input"]
    assert plan_input.change_request is evidence.change_request
    assert plan_input.copilot_coding_task_id == "copilot-task-1"
    assert plan_input.generated_test_plan is generated_test_plan
    assert plan_input.generated_test_data_plan is generated_test_data_plan
    assert observed["persisted"] == (planned, "worker-1")
