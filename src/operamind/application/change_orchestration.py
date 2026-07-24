"""Canonical planning from confirmed document change to executable verification assets."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from operamind.application.change_loop_case import ChangeLoopCase
from operamind.application.test_data_flow import build_test_data_plan_flows
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
    reviewed_case: ChangeLoopCase


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
    """Generate only from current Canonical evidence and one reviewed Golden case."""

    def __init__(self, *, repository_root: Path) -> None:
        self._root = repository_root.resolve()
        self._contracts = ContractCatalog.load(self._root / "contracts")

    def plan(self, value: ChangeOrchestrationInput) -> ChangeOrchestrationResult:
        request = value.change_request
        case = value.reviewed_case
        request_id = str(request["change_request_id"])
        project_id = str(request["project_id"])
        report = value.impact_report
        confirmation = value.impact_confirmation
        if request.get("ambiguity_status") != "clear" or request.get("confirmation_required"):
            raise ChangeOrchestrationBlockedError("Change Request ambiguity is not resolved")
        if case.project_id != project_id:
            raise ChangeOrchestrationBlockedError("Reviewed case Project does not match")
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
        expected = _expected_stable_keys(case)
        actual = {str(change["stable_key"]) for change in changes}
        if actual != expected:
            raise ChangeOrchestrationBlockedError(
                f"Reviewed case Structured Change mismatch: expected={sorted(expected)} "
                f"actual={sorted(actual)}"
            )
        revision = str(report["repository_revision"])
        if revision != str(case.repository["base_revision"]):
            raise ChangeOrchestrationBlockedError("Reviewed case repository revision differs")

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

        rule_mapping = _map_business_rules(request, case)
        criteria = _remap_rule_refs(case.acceptance_criteria, rule_mapping)
        test_cases = _remap_rule_refs(case.test_cases, rule_mapping)
        case_payload = json.dumps(
            case.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        case_digest = hashlib.sha256(case_payload).hexdigest()
        basis = f"{report['impact_report_id']}:{case_digest}"
        acceptance_id = _id("acceptance", request_id, basis)
        test_plan_id = _id("test-plan", request_id, basis)
        test_data_plan_id = _id("test-data-plan", request_id, basis)
        coverage_id = _id("coverage", request_id, basis)
        acceptance: dict[str, Any] = {
            "artifact_type": "AcceptanceCriteria",
            "schema_version": "v1",
            "acceptance_criteria_id": acceptance_id,
            "change_request_id": request_id,
            "project_id": project_id,
            "criteria": criteria,
        }
        test_plan: dict[str, Any] = {
            "artifact_type": "TestPlan",
            "schema_version": "v1",
            "test_plan_id": test_plan_id,
            "change_request_id": request_id,
            "project_id": project_id,
            "status": "ready",
            "test_cases": test_cases,
            "blocking_reasons": [],
        }
        flows, data_blockers = build_test_data_plan_flows(case)
        test_data: dict[str, Any] = {
            "artifact_type": "TestDataPlan",
            "schema_version": "v1",
            "test_data_plan_id": test_data_plan_id,
            "test_plan_id": test_plan_id,
            "project_id": project_id,
            "status": "blocked" if data_blockers else "ready",
            "data_sets": copy.deepcopy(case.data_sets),
            "generation_flows": flows,
            "blocking_reasons": data_blockers,
        }
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
            "reviewed_case_id": case.case_id,
            "reviewed_case_digest": case_digest,
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


def _expected_stable_keys(case: ChangeLoopCase) -> set[str]:
    path = case.root / "expected-changes.json"
    payload = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    return {str(value["stable_key"]) for value in cast(list[dict[str, Any]], payload["changes"])}


def _map_business_rules(
    request: dict[str, Any], case: ChangeLoopCase
) -> dict[str, tuple[str, ...]]:
    requested = cast(list[dict[str, Any]], request["business_rules"])
    if not requested:
        raise ChangeOrchestrationBlockedError("Change Request has no business rules")
    mapping: dict[str, tuple[str, ...]] = {}
    for template in cast(list[dict[str, Any]], case.requirements["business_rules"]):
        source_refs = {str(value) for value in cast(list[object], template["source_refs"])}
        matches = [
            str(rule["business_rule_id"])
            for rule in requested
            if source_refs & {str(value) for value in cast(list[object], rule["source_refs"])}
        ]
        if len(matches) == 1:
            mapping[str(template["business_rule_id"])] = (matches[0],)
        elif not matches and len(requested) == 1:
            mapping[str(template["business_rule_id"])] = (str(requested[0]["business_rule_id"]),)
        else:
            raise ChangeOrchestrationBlockedError(
                f"Business rule mapping is ambiguous: {template['business_rule_id']}"
            )
    return mapping


def _remap_rule_refs(
    values: list[dict[str, Any]], mapping: dict[str, tuple[str, ...]]
) -> list[dict[str, Any]]:
    remapped = copy.deepcopy(values)
    for value in remapped:
        refs: list[str] = []
        for source in cast(list[str], value["business_rule_refs"]):
            refs.extend(mapping[source])
        value["business_rule_refs"] = sorted(set(refs))
    return remapped


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
