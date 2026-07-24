"""Build and validate executable, dependency-aware test-data generation flows."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Protocol, cast

from operamind.application.change_loop_case import ChangeLoopCase

_VARIABLE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


class TestDataFlowSource(Protocol):
    @property
    def data_sets(self) -> list[dict[str, Any]]: ...

    @property
    def test_cases(self) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class _ArtifactFlowSource:
    data_sets: list[dict[str, Any]]
    test_cases: list[dict[str, Any]]


def build_test_data_plan_flows(
    case: ChangeLoopCase,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return reviewed flows or convert legacy setup actions into explicit flows."""

    configured = case.payload.get("data_generation_flows")
    if isinstance(configured, list) and configured:
        flows = copy.deepcopy(cast(list[dict[str, Any]], configured))
    else:
        flows = [_legacy_flow(case, data_set) for data_set in case.data_sets]
    return flows, validate_test_data_plan_flows(case, flows)


def validate_test_data_plan_flows(
    case: TestDataFlowSource,
    flows: list[dict[str, Any]],
) -> list[str]:
    """Find conditions that make data generation unsafe or non-executable."""

    reasons: list[str] = []
    known_data = {str(value["test_data_id"]) for value in case.data_sets}
    known_tests = {str(value["test_case_id"]) for value in case.test_cases}
    flow_ids = [str(flow.get("flow_id", "")) for flow in flows]
    if len(flow_ids) != len(set(flow_ids)):
        reasons.append("Data generation flow IDs must be unique")

    referenced_data: set[str] = set()
    for flow in flows:
        flow_id = str(flow.get("flow_id", "<unknown>"))
        data_refs = {str(value) for value in cast(list[object], flow.get("test_data_refs", []))}
        test_refs = {str(value) for value in cast(list[object], flow.get("test_case_refs", []))}
        referenced_data.update(data_refs)
        if not data_refs or not data_refs.issubset(known_data):
            reasons.append(f"{flow_id}: test_data_refs contain unknown data")
        if not test_refs or not test_refs.issubset(known_tests):
            reasons.append(f"{flow_id}: test_case_refs contain unknown tests")
        steps = cast(list[dict[str, Any]], flow.get("steps", []))
        if not steps:
            reasons.append(f"{flow_id}: at least one generation step is required")
            continue
        step_ids = [str(step.get("step_id", "")) for step in steps]
        if len(step_ids) != len(set(step_ids)):
            reasons.append(f"{flow_id}: step IDs must be unique")
        sequences = [step.get("sequence") for step in steps]
        if sequences != list(range(1, len(steps) + 1)):
            reasons.append(f"{flow_id}: step sequence must be ordered and contiguous")

        available_steps: set[str] = set()
        available_variables: set[str] = set()
        for step in steps:
            step_id = str(step.get("step_id", "<unknown>"))
            dependencies = {str(value) for value in cast(list[object], step.get("depends_on", []))}
            if not dependencies.issubset(available_steps):
                reasons.append(f"{flow_id}/{step_id}: depends_on must reference earlier steps")
            referenced_variables = _variables_in(step.get("inputs", {})) | _variables_in(
                step.get("target", "")
            )
            missing_variables = referenced_variables - available_variables
            if missing_variables:
                reasons.append(
                    f"{flow_id}/{step_id}: input variables are not produced by earlier steps: "
                    f"{sorted(missing_variables)}"
                )
            if step.get("channel") == "ui" and (
                not str(step.get("screen_ref", "")).strip()
                or not str(step.get("ui_action_ref", "")).strip()
            ):
                reasons.append(
                    f"{flow_id}/{step_id}: UI generation requires reviewed screen/action refs"
                )
            if not cast(list[object], step.get("postconditions", [])):
                reasons.append(f"{flow_id}/{step_id}: postconditions are required")
            outputs = cast(list[dict[str, Any]], step.get("output_bindings", []))
            output_names = [str(value.get("variable", "")) for value in outputs]
            duplicate = available_variables.intersection(output_names)
            if duplicate or len(output_names) != len(set(output_names)):
                reasons.append(f"{flow_id}/{step_id}: output variables must be unique")
            postcondition_variables = _variables_in(step.get("postconditions", []))
            missing_postcondition_variables = postcondition_variables - (
                available_variables | set(output_names)
            )
            if missing_postcondition_variables:
                reasons.append(
                    f"{flow_id}/{step_id}: postcondition variables are not available: "
                    f"{sorted(missing_postcondition_variables)}"
                )
            available_variables.update(output_names)
            available_steps.add(step_id)
        cleanup_steps = cast(list[dict[str, Any]], flow.get("cleanup_steps", []))
        if flow.get("cleanup_policy") == "delete_after_run" and not cleanup_steps:
            reasons.append(f"{flow_id}: delete_after_run requires cleanup steps")
        cleanup_sequences = [step.get("sequence") for step in cleanup_steps]
        if cleanup_sequences != list(range(1, len(cleanup_steps) + 1)):
            reasons.append(f"{flow_id}: cleanup sequence must be ordered and contiguous")
        cleanup_step_ids: set[str] = set()
        for step in cleanup_steps:
            step_id = str(step.get("step_id", "<unknown>"))
            dependencies = {str(value) for value in cast(list[object], step.get("depends_on", []))}
            if not dependencies.issubset(available_steps | cleanup_step_ids):
                reasons.append(f"{flow_id}/{step_id}: cleanup depends_on references unknown steps")
            referenced_variables = _variables_in(step.get("inputs", {})) | _variables_in(
                step.get("target", "")
            )
            missing_variables = referenced_variables - available_variables
            if missing_variables:
                reasons.append(
                    f"{flow_id}/{step_id}: cleanup variables are not available: "
                    f"{sorted(missing_variables)}"
                )
            if step.get("channel") == "ui" and (
                not str(step.get("screen_ref", "")).strip()
                or not str(step.get("ui_action_ref", "")).strip()
            ):
                reasons.append(
                    f"{flow_id}/{step_id}: cleanup UI requires reviewed screen/action refs"
                )
            if not cast(list[object], step.get("postconditions", [])):
                reasons.append(f"{flow_id}/{step_id}: cleanup postconditions are required")
            cleanup_step_ids.add(step_id)
        if not cast(list[object], flow.get("final_assertions", [])):
            reasons.append(f"{flow_id}: final business assertions are required")
        missing_final_variables = _variables_in(flow.get("final_assertions", [])) - (
            available_variables
        )
        if missing_final_variables:
            reasons.append(
                f"{flow_id}: final assertion variables are not available: "
                f"{sorted(missing_final_variables)}"
            )

    missing_data = known_data - referenced_data
    if missing_data:
        reasons.append(f"Test data are not generated by any flow: {sorted(missing_data)}")
    return sorted(set(reasons))


def validate_test_data_plan_artifact(plan: dict[str, Any]) -> list[str]:
    """Validate cross-reference and execution semantics of a schema-valid Artifact."""

    data_sets = cast(list[dict[str, Any]], plan.get("data_sets", []))
    test_ids = sorted(
        {
            str(reference)
            for data_set in data_sets
            for reference in cast(list[object], data_set.get("test_case_refs", []))
        }
    )
    source = _ArtifactFlowSource(
        data_sets=data_sets,
        test_cases=[{"test_case_id": value} for value in test_ids],
    )
    return validate_test_data_plan_flows(
        source,
        cast(list[dict[str, Any]], plan.get("generation_flows", [])),
    )


def _legacy_flow(case: ChangeLoopCase, data_set: dict[str, Any]) -> dict[str, Any]:
    data_id = str(data_set["test_data_id"])
    test_refs = list(cast(list[str], data_set["test_case_refs"]))
    actions = cast(list[dict[str, Any]], data_set["setup_actions"])
    steps = [
        _legacy_step(
            case,
            data_id,
            index,
            action,
            previous_action_id=(str(actions[index - 2]["action_id"]) if index > 1 else None),
        )
        for index, action in enumerate(actions, start=1)
    ]
    assertions: list[dict[str, Any]] = []
    for test in case.test_cases:
        test_id = str(test["test_case_id"])
        if test_id not in test_refs:
            continue
        for index, expected in enumerate(cast(list[str], test["expected_results"]), start=1):
            assertions.append(
                {
                    "assertion_id": f"{data_id}-{test_id}-result-{index}",
                    "observe_via": "test",
                    "subject": test_id,
                    "operator": "satisfies",
                    "expected": expected,
                }
            )
    return {
        "flow_id": f"flow-{data_id}",
        "title": f"Generate reviewed data set {data_id}",
        "test_data_refs": [data_id],
        "test_case_refs": test_refs,
        "steps": steps,
        "final_assertions": assertions,
        "cleanup_policy": data_set["cleanup_policy"],
        "cleanup_steps": [],
    }


def _legacy_step(
    case: ChangeLoopCase,
    data_id: str,
    sequence: int,
    action: dict[str, Any],
    previous_action_id: str | None,
) -> dict[str, Any]:
    action_id = str(action["action_id"])
    channel = str(action["action_type"])
    target = str(action["target"])
    setup_request = _setup_request(case, target)
    assertions = _setup_request_assertions(setup_request)
    if not assertions:
        assertions = [
            {
                "assertion_id": f"{data_id}-{action_id}-{key}",
                "observe_via": "fixture" if channel == "fixture" else "database",
                "subject": key,
                "operator": "equals",
                "expected": value,
            }
            for key, value in cast(dict[str, Any], action["payload"]).items()
            if key.startswith("expected_")
        ]
    return {
        "step_id": action_id,
        "sequence": sequence,
        "channel": channel,
        "business_action": action_id.replace("-", " "),
        "target": target,
        "inputs": copy.deepcopy(
            setup_request["request"] if setup_request is not None else action["payload"]
        ),
        "depends_on": [] if previous_action_id is None else [previous_action_id],
        "output_bindings": [],
        "postconditions": assertions,
    }


def _setup_request(case: ChangeLoopCase, target: str) -> dict[str, Any] | None:
    normalized = target.strip().upper()
    for setup in cast(list[dict[str, Any]], case.execution["setup_requests"]):
        request = cast(dict[str, Any], setup["request"])
        request_target = f"{request['method']} {request['path']}".upper()
        if request_target == normalized:
            return setup
    return None


def _setup_request_assertions(setup: dict[str, Any] | None) -> list[dict[str, Any]]:
    if setup is None:
        return []
    return [
        {
            "assertion_id": f"{setup['setup_id']}-{index}",
            "observe_via": "response",
            "subject": str(value["path"]),
            "operator": str(value["operator"]),
            "expected": value.get("expected"),
        }
        for index, value in enumerate(cast(list[dict[str, Any]], setup["assertions"]), start=1)
    ]


def _variables_in(value: object) -> set[str]:
    if isinstance(value, str):
        return set(_VARIABLE.findall(value))
    if isinstance(value, list):
        return set().union(*(_variables_in(item) for item in value), set())
    if isinstance(value, dict):
        return set().union(*(_variables_in(item) for item in value.values()), set())
    return set()
