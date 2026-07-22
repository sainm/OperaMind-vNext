"""Build one fail-closed ChangeClosureResult from Canonical execution evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, cast

from operamind.contracts import ContractCatalog


@dataclass(frozen=True, slots=True)
class ChangeClosureInput:
    change_request: dict[str, Any]
    orchestration: dict[str, Any]
    test_plan: dict[str, Any]
    test_data_plan: dict[str, Any]
    coverage_report: dict[str, Any]
    edit_result: dict[str, Any] | None
    test_data_result: dict[str, Any] | None
    ui_result: dict[str, Any] | None
    ui_test_case_refs: tuple[tuple[str, tuple[str, ...]], ...] = ()


class ChangeClosureEvaluator:
    """Evaluate code, deterministic tests, data, coverage, and UI as one closure."""

    def __init__(self, contracts: ContractCatalog) -> None:
        self._contracts = contracts

    def evaluate(self, value: ChangeClosureInput) -> dict[str, Any]:
        self._validate_scope(value)
        test_cases = cast(list[dict[str, Any]], value.test_plan["test_cases"])
        flow_by_case = _flow_results_by_case(value.test_data_plan, value.test_data_result)
        ui_by_case = _ui_results_by_case(value.ui_result, value.ui_test_case_refs)
        test_results = [
            _test_result(
                test_case=test_case,
                edit_result=value.edit_result,
                flow_results=flow_by_case.get(str(test_case["test_case_id"]), ()),
                ui_result=ui_by_case.get(str(test_case["test_case_id"])),
                data_required=_case_requires_data(
                    value.test_data_plan, str(test_case["test_case_id"])
                ),
            )
            for test_case in test_cases
        ]
        ui_status = _ui_status(test_cases, value.ui_result)
        unresolved = _unresolved_items(
            value=value,
            test_results=test_results,
            ui_status=ui_status,
        )
        status = _closure_status(
            value=value,
            test_results=test_results,
            ui_status=ui_status,
            unresolved=unresolved,
        )
        component_refs = _component_refs(value)
        material = json.dumps(
            {
                "orchestration_id": value.orchestration["orchestration_id"],
                "component_refs": component_refs,
                "modified_paths": _modified_paths(value.edit_result),
                "test_results": test_results,
                "ui_status": ui_status,
                "coverage": value.coverage_report["coverage_percent"],
                "status": status,
                "unresolved": unresolved,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        artifact: dict[str, Any] = {
            "artifact_type": "ChangeClosureResult",
            "schema_version": "v1",
            "closure_result_id": f"closure-{hashlib.sha256(material).hexdigest()[:24]}",
            "change_request_id": value.change_request["change_request_id"],
            "project_id": value.change_request["project_id"],
            "input_mode": value.change_request["input_mode"],
            "artifact_refs": component_refs,
            "structured_change_refs": sorted(
                str(reference)
                for reference in cast(
                    list[object], value.orchestration["structured_change_refs"]
                )
            ),
            "modified_paths": _modified_paths(value.edit_result),
            "test_results": test_results,
            "ui_status": ui_status,
            "business_coverage_percent": value.coverage_report["coverage_percent"],
            "status": status,
            "unresolved_items": unresolved,
        }
        self._contracts.validate_artifact(artifact)
        return artifact

    @staticmethod
    def _validate_scope(value: ChangeClosureInput) -> None:
        request_id = str(value.change_request["change_request_id"])
        project_id = str(value.change_request["project_id"])
        if value.orchestration.get("change_request_id") != request_id:
            raise ValueError("Closure Orchestration does not match Change Request")
        artifacts = (
            value.orchestration,
            value.test_plan,
            value.test_data_plan,
            value.coverage_report,
        )
        if any(str(artifact.get("project_id")) != project_id for artifact in artifacts):
            raise ValueError("Closure component Project identity does not match")
        refs = cast(dict[str, str], value.orchestration["artifact_refs"])
        expected_refs = {
            "test_plan_id": value.test_plan["test_plan_id"],
            "test_data_plan_id": value.test_data_plan["test_data_plan_id"],
            "coverage_report_id": value.coverage_report["coverage_report_id"],
        }
        if any(refs[key] != expected for key, expected in expected_refs.items()):
            raise ValueError("Closure component is not bound to Orchestration")
        if value.test_data_plan["test_plan_id"] != value.test_plan["test_plan_id"]:
            raise ValueError("Test Data Plan does not match Test Plan")
        if value.coverage_report["test_plan_id"] != value.test_plan["test_plan_id"]:
            raise ValueError("Coverage Report does not match Test Plan")
        analysis_case_id = str(value.orchestration["analysis_case_id"])
        if value.edit_result is not None and (
            str(value.edit_result.get("project_id")) != project_id
            or str(value.edit_result.get("analysis_case_id")) != analysis_case_id
        ):
            raise ValueError("Edit Result is outside Closure scope")
        if value.test_data_result is not None and (
            str(value.test_data_result.get("project_id")) != project_id
            or str(value.test_data_result.get("test_data_plan_id"))
            != str(value.test_data_plan["test_data_plan_id"])
        ):
            raise ValueError("Test Data Result is outside Closure scope")
        if (
            value.ui_result is not None
            and str(value.ui_result.get("analysis_case_id")) != analysis_case_id
        ):
            raise ValueError("UI Result is outside Closure scope")


def _component_refs(value: ChangeClosureInput) -> list[str]:
    refs = cast(dict[str, str], value.orchestration["artifact_refs"])
    values = [
        str(value.orchestration["orchestration_id"]),
        str(refs["acceptance_criteria_id"]),
        str(value.test_plan["test_plan_id"]),
        str(value.test_data_plan["test_data_plan_id"]),
        str(value.coverage_report["coverage_report_id"]),
    ]
    if value.edit_result is not None:
        values.append(str(value.edit_result["edit_result_id"]))
    if value.test_data_result is not None:
        values.append(str(value.test_data_result["execution_result_id"]))
    if value.ui_result is not None:
        values.append(str(value.ui_result["verification_result_id"]))
    return sorted(set(values))


def _modified_paths(edit_result: dict[str, Any] | None) -> list[str]:
    if edit_result is None:
        return []
    return sorted({str(path) for path in cast(list[object], edit_result["changed_paths"])})


def _case_requires_data(plan: dict[str, Any], test_case_id: str) -> bool:
    return any(
        test_case_id in {str(value) for value in cast(list[object], flow["test_case_refs"])}
        for flow in cast(list[dict[str, Any]], plan["generation_flows"])
    )


def _flow_results_by_case(
    plan: dict[str, Any], result: dict[str, Any] | None
) -> dict[str, tuple[dict[str, Any], ...]]:
    if result is None:
        return {}
    result_by_id = {
        str(flow["flow_id"]): flow
        for flow in cast(list[dict[str, Any]], result["flow_results"])
    }
    collected: dict[str, list[dict[str, Any]]] = {}
    for flow in cast(list[dict[str, Any]], plan["generation_flows"]):
        flow_result = result_by_id.get(str(flow["flow_id"]))
        if flow_result is None:
            continue
        for test_case_id in cast(list[object], flow["test_case_refs"]):
            collected.setdefault(str(test_case_id), []).append(flow_result)
    return {key: tuple(values) for key, values in collected.items()}


def _ui_results_by_case(
    result: dict[str, Any] | None,
    mappings: tuple[tuple[str, tuple[str, ...]], ...],
) -> dict[str, dict[str, Any]]:
    if result is None:
        return {}
    refs_by_scenario = dict(mappings)
    collected: dict[str, dict[str, Any]] = {}
    for item in cast(list[dict[str, Any]], result["scenario_results"]):
        scenario_id = str(item["scenario_id"])
        refs = refs_by_scenario.get(scenario_id) or (scenario_id,)
        for test_case_id in refs:
            if test_case_id in collected:
                raise ValueError("Multiple UI Scenarios map to the same Test case")
            collected[test_case_id] = item
    return collected


def _test_result(
    *,
    test_case: dict[str, Any],
    edit_result: dict[str, Any] | None,
    flow_results: tuple[dict[str, Any], ...],
    ui_result: dict[str, Any] | None,
    data_required: bool,
) -> dict[str, Any]:
    test_case_id = str(test_case["test_case_id"])
    level = str(test_case["level"])
    evidence_refs: set[str] = set()
    data_status: str | None = None
    if data_required:
        if not flow_results:
            data_status = "blocked"
        else:
            statuses = {str(flow["status"]) for flow in flow_results}
            if "failed" in statuses:
                data_status = "failed"
            elif statuses - {"passed"}:
                data_status = "blocked"
            else:
                data_status = "passed"
            for flow in flow_results:
                for key in ("step_results", "cleanup_results"):
                    for step in cast(list[dict[str, Any]], flow[key]):
                        evidence_refs.update(
                            str(reference)
                            for reference in cast(list[object], step["evidence_refs"])
                        )
    if level == "ui":
        if ui_result is None:
            status = "blocked"
            summary = "Required UI Scenario has no verification result."
        else:
            raw = str(ui_result["status"])
            status = raw if raw in {"passed", "failed", "blocked"} else "blocked"
            evidence_refs.update(
                str(reference)
                for reference in cast(list[object], ui_result["evidence_refs"])
            )
            summary = str(ui_result.get("summary") or f"UI Scenario is {raw}.")
    elif edit_result is None or str(edit_result.get("validation_mode")) != "committed":
        status = "blocked"
        summary = "Committed Edit Result test evidence is missing."
    elif not cast(list[object], edit_result.get("test_result_refs", [])):
        status = "blocked"
        summary = "Committed Edit Result has no command test evidence."
    else:
        evidence_refs.update(
            str(reference)
            for reference in cast(list[object], edit_result["test_result_refs"])
        )
        status = "passed" if edit_result.get("tests_passed") is True else "failed"
        summary = (
            "Approved deterministic command suite passed."
            if status == "passed"
            else "Approved deterministic command suite failed."
        )
    if data_status in {"failed", "blocked"}:
        status = data_status
        summary = f"Test data execution is {data_status}."
    return {
        "test_case_id": test_case_id,
        "status": status,
        "evidence_refs": sorted(evidence_refs),
        "summary": summary,
    }


def _ui_status(test_cases: list[dict[str, Any]], result: dict[str, Any] | None) -> str:
    if not any(str(test["level"]) == "ui" for test in test_cases):
        return "not_impacted"
    if result is None:
        return "blocked"
    raw = str(result["status"])
    return raw if raw in {"passed", "failed", "blocked"} else "blocked"


def _unresolved_items(
    *, value: ChangeClosureInput, test_results: list[dict[str, Any]], ui_status: str
) -> list[str]:
    items: set[str] = set()
    if value.orchestration["status"] != "ready":
        items.update(
            str(reason)
            for reason in cast(list[object], value.orchestration["blocking_reasons"])
        )
    if value.edit_result is None:
        items.add("Committed Edit Result is missing")
    else:
        if value.edit_result.get("validation_mode") != "committed":
            items.add("Edit Result is not committed")
        if value.edit_result.get("status") != "in_scope":
            items.add("Edit Result is not in scope")
        if value.edit_result.get("command_evidence_status") != "verified":
            items.add("Edit Result command evidence is not verified")
        items.update(
            f"Out-of-scope file: {path}"
            for path in cast(list[object], value.edit_result.get("out_of_scope_files", []))
        )
    if value.test_data_plan["status"] == "ready" and cast(
        list[object], value.test_data_plan["generation_flows"]
    ) and value.test_data_result is None:
        items.add("Test Data Execution Result is missing")
    if value.test_data_result is not None and value.test_data_result["cleanup_status"] in {
        "failed",
        "interrupted",
    }:
        items.add("Test data cleanup failed")
    if (
        value.coverage_report["status"] != "passed"
        or value.coverage_report["coverage_percent"] < 100
    ):
        for item in cast(list[dict[str, Any]], value.coverage_report["items"]):
            if item["status"] != "covered":
                items.add(f"Uncovered business rule: {item['business_rule_id']}")
    items.update(
        f"Test case {result['test_case_id']} is {result['status']}"
        for result in test_results
        if result["status"] != "passed"
    )
    if ui_status == "blocked":
        items.add("UI verification is blocked or missing")
    if value.ui_result is not None:
        items.update(
            f"Unresolved impact item: {item}"
            for item in cast(list[object], value.ui_result["unresolved_impact_item_ids"])
        )
        items.update(
            f"UI out-of-scope file: {path}"
            for path in cast(list[object], value.ui_result["out_of_scope_files"])
        )
        items.update(
            str(reason)
            for reason in cast(
                list[object], value.ui_result.get("failure_reasons", [])
            )
        )
    return sorted(items)


def _closure_status(
    *,
    value: ChangeClosureInput,
    test_results: list[dict[str, Any]],
    ui_status: str,
    unresolved: list[str],
) -> str:
    if value.edit_result is not None and (
        value.edit_result.get("status") == "out_of_scope"
        or bool(cast(list[object], value.edit_result.get("out_of_scope_files", [])))
    ):
        return "reanalysis_required"
    if value.ui_result is not None and (
        value.ui_result["status"] == "reanalysis_required"
        or bool(cast(list[object], value.ui_result["unresolved_impact_item_ids"]))
        or bool(cast(list[object], value.ui_result["out_of_scope_files"]))
    ):
        return "reanalysis_required"
    if (
        value.orchestration["status"] != "ready"
        or value.edit_result is None
        or value.edit_result.get("validation_mode") != "committed"
        or value.edit_result.get("command_evidence_status") != "verified"
        or (value.test_data_result is None
        and bool(cast(list[object], value.test_data_plan["generation_flows"])))
        or any(result["status"] == "blocked" for result in test_results)
        or ui_status == "blocked"
    ):
        return "blocked"
    if (
        value.edit_result.get("status") != "in_scope"
        or value.edit_result.get("tests_passed") is not True
        or (value.test_data_result is not None and value.test_data_result["status"] != "passed")
        or (
            value.test_data_result is not None
            and value.test_data_result["cleanup_status"] == "failed"
        )
        or value.coverage_report["status"] != "passed"
        or value.coverage_report["coverage_percent"] < 100
        or any(result["status"] == "failed" for result in test_results)
        or ui_status == "failed"
    ):
        return "failed"
    return "passed" if not unresolved else "blocked"
