"""Canonical planning from confirmed document change to executable verification assets."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from operamind.contracts import ContractCatalog


class ChangeOrchestrationBlockedError(ValueError):
    """Raised when reviewed evidence cannot support deterministic generation."""


@dataclass(frozen=True, slots=True)
class ChangeOrchestrationInput:
    change_request: dict[str, Any]
    analysis_case_id: str
    structured_changes: tuple[dict[str, Any], ...]
    accepted_structured_change_refs: frozenset[str]
    impact_report: dict[str, Any]
    impact_report_state: str
    impact_confirmation: dict[str, Any]
    copilot_coding_task_id: str | None = None
    generated_test_plan: dict[str, Any] | None = None
    generated_test_data_plan: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ChangeOrchestrationResult:
    orchestration: dict[str, Any]
    acceptance_criteria: dict[str, Any]
    test_plan: dict[str, Any]
    test_data_plan: dict[str, Any]
    coverage_report: dict[str, Any]

    @property
    def artifacts(self) -> tuple[dict[str, Any], ...]:
        return (
            self.acceptance_criteria,
            self.test_plan,
            self.test_data_plan,
            self.coverage_report,
            self.orchestration,
        )


class ChangeOrchestrationPlanner:
    """Bind current Canonical evidence to Copilot-generated verification plans."""

    def __init__(self, *, repository_root: Path) -> None:
        self._root = repository_root.resolve()
        self._contracts = ContractCatalog.load(self._root / "contracts")

    def plan(self, value: ChangeOrchestrationInput) -> ChangeOrchestrationResult:
        request = value.change_request
        request_id = str(request["change_request_id"])
        project_id = str(request["project_id"])
        report = value.impact_report
        confirmation = value.impact_confirmation
        if request.get("ambiguity_status") != "clear" or request.get("confirmation_required"):
            raise ChangeOrchestrationBlockedError("Change Request ambiguity is not resolved")
        if str(report.get("analysis_case_id")) != value.analysis_case_id:
            raise ChangeOrchestrationBlockedError("Impact Report Case does not match")
        if str(report.get("project_id")) != project_id or value.impact_report_state != "confirmed":
            raise ChangeOrchestrationBlockedError("Current confirmed Impact Report is required")
        if cast(list[object], report.get("blocking_unknowns", [])):
            raise ChangeOrchestrationBlockedError("Impact Report still has blocking unknowns")
        if confirmation.get("impact_report_id") != report.get("impact_report_id"):
            raise ChangeOrchestrationBlockedError("Impact Confirmation does not bind the report")

        changes = value.structured_changes
        change_ids = {str(change["change_id"]) for change in changes}
        if not changes or value.accepted_structured_change_refs != change_ids:
            raise ChangeOrchestrationBlockedError("All Structured Changes must be accepted")
        revision = str(report["repository_revision"])

        approved_ids = {
            str(value) for value in cast(list[object], confirmation["approved_item_ids"])
        }
        rejected_ids = {
            str(value) for value in cast(list[object], confirmation["rejected_item_ids"])
        }
        report_items = cast(list[dict[str, Any]], report["items"])
        report_ids = {str(item["impact_item_id"]) for item in report_items}
        if approved_ids | rejected_ids != report_ids or approved_ids & rejected_ids:
            raise ChangeOrchestrationBlockedError("Impact item decisions are incomplete")
        code_scope = [
            {
                "impact_item_id": item["impact_item_id"],
                "target_path": item["target_path"],
                "target_symbols": copy.deepcopy(item["target_symbols"]),
                "recommended_action": item["recommended_action"],
                "test_file_refs": copy.deepcopy(item.get("test_file_refs", [])),
            }
            for item in report_items
            if str(item["impact_item_id"]) in approved_ids
        ]
        if not code_scope:
            raise ChangeOrchestrationBlockedError("No Impact items were approved for code scope")

        (
            planning_source_id,
            planning_source_digest,
            criteria,
            test_plan,
            test_data,
        ) = _copilot_planning_outputs(value, request=request)
        test_cases = cast(list[dict[str, Any]], test_plan["test_cases"])
        basis = f"{report['impact_report_id']}:{planning_source_digest}"
        acceptance_id = _id("acceptance", request_id, basis)
        coverage_id = _id("coverage", request_id, basis)
        acceptance: dict[str, Any] = {
            "artifact_type": "AcceptanceCriteria",
            "schema_version": "v1",
            "acceptance_criteria_id": acceptance_id,
            "change_request_id": request_id,
            "project_id": project_id,
            "criteria": criteria,
        }
        test_plan_id = str(test_plan["test_plan_id"])
        test_data_plan_id = str(test_data["test_data_plan_id"])
        data_blockers = list(cast(list[str], test_data.get("blocking_reasons", [])))
        if test_plan.get("status") != "ready":
            data_blockers.append("Copilot TestPlan is blocked")
        coverage = _coverage(
            request=request,
            acceptance=acceptance,
            test_plan=test_plan,
            coverage_id=coverage_id,
        )
        ui_scenarios = [
            {
                "scenario_id": test["test_case_id"],
                "title": test["title"],
                "test_data_refs": copy.deepcopy(test["test_data_refs"]),
                "steps": copy.deepcopy(test["steps"]),
                "expected_results": copy.deepcopy(test["expected_results"]),
            }
            for test in test_cases
            if test["level"] == "ui"
        ]
        orchestration: dict[str, Any] = {
            "artifact_type": "ChangeOrchestrationPlan",
            "schema_version": "v1",
            "orchestration_id": _id("orchestration", request_id, value.analysis_case_id, basis),
            "change_request_id": request_id,
            "project_id": project_id,
            "analysis_case_id": value.analysis_case_id,
            "status": "blocked" if data_blockers else "ready",
            "structured_change_refs": sorted(str(change["change_id"]) for change in changes),
            "impact_report_id": report["impact_report_id"],
            "reviewed_case_id": planning_source_id,
            "reviewed_case_digest": planning_source_digest,
            "repository_revision": revision,
            "code_scope": code_scope,
            "artifact_refs": {
                "acceptance_criteria_id": acceptance_id,
                "test_plan_id": test_plan_id,
                "test_data_plan_id": test_data_plan_id,
                "coverage_report_id": coverage_id,
            },
            "ui_scenarios": ui_scenarios,
            "blocking_reasons": data_blockers,
        }
        result = ChangeOrchestrationResult(
            orchestration=orchestration,
            acceptance_criteria=acceptance,
            test_plan=test_plan,
            test_data_plan=test_data,
            coverage_report=coverage,
        )
        for artifact in result.artifacts:
            self._contracts.validate_artifact(artifact)
        return result


def _copilot_planning_outputs(
    value: ChangeOrchestrationInput,
    *,
    request: dict[str, Any],
) -> tuple[str, str, list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    task_id = value.copilot_coding_task_id
    test_plan_value = value.generated_test_plan
    test_data_value = value.generated_test_data_plan
    if (
        not isinstance(task_id, str)
        or not task_id.strip()
        or test_plan_value is None
        or test_data_value is None
    ):
        raise ChangeOrchestrationBlockedError(
            "Copilot TestPlan and TestDataPlan outputs are incomplete"
        )
    test_plan = copy.deepcopy(test_plan_value)
    test_data = copy.deepcopy(test_data_value)
    project_id = str(request["project_id"])
    request_id = str(request["change_request_id"])
    if (
        test_plan.get("project_id") != project_id
        or test_plan.get("change_request_id") != request_id
        or test_data.get("project_id") != project_id
        or test_data.get("test_plan_id") != test_plan.get("test_plan_id")
    ):
        raise ChangeOrchestrationBlockedError(
            "Copilot planning outputs do not match the Change Request"
        )
    request_rules = {
        str(rule["business_rule_id"])
        for rule in cast(list[dict[str, Any]], request["business_rules"])
    }
    tests = cast(list[dict[str, Any]], test_plan.get("test_cases", []))
    if not tests:
        raise ChangeOrchestrationBlockedError("Copilot TestPlan has no Test Cases")
    referenced_rules = {
        str(rule_ref)
        for test in tests
        for rule_ref in cast(list[object], test.get("business_rule_refs", []))
    }
    if referenced_rules != request_rules:
        raise ChangeOrchestrationBlockedError(
            "Copilot TestPlan does not cover exactly the Change Request business rules"
        )
    criterion_ids = {
        str(criterion_ref)
        for test in tests
        for criterion_ref in cast(list[object], test.get("acceptance_criteria_refs", []))
    }
    if not criterion_ids:
        raise ChangeOrchestrationBlockedError(
            "Copilot TestPlan has no acceptance criteria references"
        )
    criteria: list[dict[str, Any]] = []
    for criterion_id in sorted(criterion_ids):
        matching = [
            test
            for test in tests
            if criterion_id
            in {
                str(value)
                for value in cast(list[object], test.get("acceptance_criteria_refs", []))
            }
        ]
        expected = [
            str(result)
            for test in matching
            for result in cast(list[object], test.get("expected_results", []))
        ]
        if not expected:
            raise ChangeOrchestrationBlockedError(
                f"Copilot acceptance criterion has no expected result: {criterion_id}"
            )
        levels = {str(test["level"]) for test in matching}
        assertion_type = (
            "ui" if "ui" in levels else "api" if "api" in levels else "source"
        )
        criteria.append(
            {
                "criterion_id": criterion_id,
                "business_rule_refs": sorted(
                    {
                        str(rule_ref)
                        for test in matching
                        for rule_ref in cast(
                            list[object], test.get("business_rule_refs", [])
                        )
                    }
                ),
                "assertion_type": assertion_type,
                "subject": " / ".join(str(test["title"]) for test in matching),
                "operator": "equals",
                "expected": expected,
                "test_case_refs": sorted(str(test["test_case_id"]) for test in matching),
            }
        )
    material = {
        "coding_task_id": task_id,
        "test_plan": test_plan,
        "test_data_plan": test_data,
    }
    digest = hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return task_id, digest, criteria, test_plan, test_data


def _coverage(
    *,
    request: dict[str, Any],
    acceptance: dict[str, Any],
    test_plan: dict[str, Any],
    coverage_id: str,
) -> dict[str, Any]:
    criteria = cast(list[dict[str, Any]], acceptance["criteria"])
    tests = cast(list[dict[str, Any]], test_plan["test_cases"])
    items: list[dict[str, Any]] = []
    for rule in cast(list[dict[str, Any]], request["business_rules"]):
        rule_id = str(rule["business_rule_id"])
        test_refs = sorted(
            str(test["test_case_id"])
            for test in tests
            if rule_id in cast(list[str], test["business_rule_refs"])
        )
        criterion_refs = sorted(
            str(criterion["criterion_id"])
            for criterion in criteria
            if rule_id in cast(list[str], criterion["business_rule_refs"])
        )
        items.append(
            {
                "business_rule_id": rule_id,
                "test_case_refs": test_refs,
                "criterion_refs": criterion_refs,
                "status": "covered" if test_refs and criterion_refs else "uncovered",
            }
        )
    covered = sum(item["status"] == "covered" for item in items)
    return {
        "artifact_type": "BusinessCoverageReport",
        "schema_version": "v1",
        "coverage_report_id": coverage_id,
        "change_request_id": request["change_request_id"],
        "test_plan_id": test_plan["test_plan_id"],
        "acceptance_criteria_id": acceptance["acceptance_criteria_id"],
        "project_id": request["project_id"],
        "business_rule_count": len(items),
        "covered_rule_count": covered,
        "coverage_percent": covered * 100 / len(items),
        "items": items,
        "status": "passed" if covered == len(items) else "failed",
    }


def _id(prefix: str, *values: str) -> str:
    material = "\0".join(values).encode()
    return f"{prefix}-{hashlib.sha256(material).hexdigest()[:24]}"
