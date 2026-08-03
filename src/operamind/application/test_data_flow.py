"""Build and validate executable, dependency-aware test-data generation flows."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol, cast

_VARIABLE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
_HTTP_MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_HTTP_GENERATION_METHODS = frozenset({"POST", "PUT", "PATCH"})
_DATA_GENERATION_EFFECTS = frozenset({"creates", "updates"})


class TestDataFlowSource(Protocol):
    @property
    def data_sets(self) -> list[dict[str, Any]]: ...

    @property
    def test_cases(self) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class _ArtifactFlowSource:
    data_sets: list[dict[str, Any]]
    test_cases: list[dict[str, Any]]


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
            referenced_variables = (
                _variables_in(step.get("inputs", {}))
                | _variables_in(step.get("target", ""))
                | _variables_in(step.get("playwright", {}))
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
            referenced_variables = (
                _variables_in(step.get("inputs", {}))
                | _variables_in(step.get("target", ""))
                | _variables_in(step.get("playwright", {}))
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
    reasons = validate_test_data_plan_flows(
        source,
        cast(list[dict[str, Any]], plan.get("generation_flows", [])),
    )
    channels = {
        str(step.get("channel") or "")
        for flow in cast(list[dict[str, Any]], plan.get("generation_flows", []))
        for collection in ("steps", "cleanup_steps")
        for step in cast(list[dict[str, Any]], flow.get(collection, []))
    }
    unavailable = sorted(channels - {"http", "ui"})
    reasons.extend(
        f"Test data channel has no project-bound executor: {channel}" for channel in unavailable
    )
    if plan.get("schema_version") == "v2":
        reasons.extend(
            _validate_v2_generation_contract(
                cast(list[dict[str, Any]], plan.get("generation_flows", []))
            )
        )
    return sorted(set(reasons))


def test_data_plan_channels(plan: dict[str, Any]) -> frozenset[str]:
    return frozenset(
        str(step.get("channel") or "")
        for flow in cast(list[dict[str, Any]], plan.get("generation_flows", []))
        for collection in ("steps", "cleanup_steps")
        for step in cast(list[dict[str, Any]], flow.get(collection, []))
    )


def _variables_in(value: object) -> set[str]:
    if isinstance(value, str):
        return set(_VARIABLE.findall(value))
    if isinstance(value, list):
        return set().union(*(_variables_in(item) for item in value), set())
    if isinstance(value, dict):
        return set().union(*(_variables_in(item) for item in value.values()), set())
    return set()


def _validate_v2_generation_contract(flows: list[dict[str, Any]]) -> list[str]:
    """Reject UI plans that merely assume target-system data already exists."""

    reasons: list[str] = []
    for flow in flows:
        flow_id = str(flow.get("flow_id", "<unknown>"))
        steps = cast(list[dict[str, Any]], flow.get("steps", []))
        if not any(_is_explicit_data_generation_step(step) for step in steps):
            reasons.append(
                f"{flow_id}: v2 UI flow requires an explicit target-system data generation "
                "step; unproved existing data is not executable test data"
            )
        for collection in ("steps", "cleanup_steps"):
            for step in cast(list[dict[str, Any]], flow.get(collection, [])):
                if step.get("channel") == "http":
                    reasons.extend(
                        _validate_http_step(
                            flow_id,
                            step,
                            cleanup=collection == "cleanup_steps",
                        )
                    )
    return reasons


def _is_explicit_data_generation_step(step: dict[str, Any]) -> bool:
    if cast(list[object], step.get("test_step_refs", [])):
        return False
    if step.get("data_effect") not in _DATA_GENERATION_EFFECTS:
        return False
    postconditions = cast(list[dict[str, Any]], step.get("postconditions", []))
    if not any(
        value.get("observe_via") in {"response", "api", "database", "ui"}
        for value in postconditions
    ):
        return False
    bindings = cast(list[dict[str, Any]], step.get("output_bindings", []))
    if step.get("channel") == "http":
        method, _path = _planned_http_target(step)
        return method in _HTTP_GENERATION_METHODS and any(
            value.get("source") == "response" and value.get("required") is True
            for value in bindings
        )
    if step.get("channel") != "ui":
        return False
    observations = {
        str(value.get("key") or "")
        for value in cast(
            list[dict[str, Any]],
            cast(dict[str, Any], step.get("playwright") or {}).get("observations", []),
        )
    }
    return any(
        value.get("source") == "ui"
        and value.get("required") is True
        and str(value.get("path") or "") in observations
        for value in bindings
    )


def _validate_http_step(
    flow_id: str,
    step: dict[str, Any],
    *,
    cleanup: bool,
) -> list[str]:
    step_id = str(step.get("step_id", "<unknown>"))
    prefix = f"{flow_id}/{step_id}"
    try:
        method, path = _planned_http_target(step)
    except ValueError as error:
        return [f"{prefix}: {error}"]
    effect = step.get("data_effect")
    if method == "DELETE" and not cleanup:
        return [f"{prefix}: HTTP setup cannot use DELETE as test data generation"]
    if method == "GET" and effect not in {None, "none"}:
        return [f"{prefix}: GET must declare data_effect=none"]
    if method in _HTTP_GENERATION_METHODS and effect not in _DATA_GENERATION_EFFECTS:
        return [f"{prefix}: mutating HTTP setup must declare creates or updates"]
    if method == "DELETE" and effect != "deletes":
        return [f"{prefix}: DELETE cleanup must declare data_effect=deletes"]
    if cleanup and effect == "creates":
        return [f"{prefix}: cleanup cannot declare data_effect=creates"]
    postconditions = cast(list[dict[str, Any]], step.get("postconditions", []))
    if not any(
        value.get("observe_via") == "api"
        and value.get("subject") == "status_code"
        and value.get("operator") == "equals"
        and isinstance(value.get("expected"), int)
        and 200 <= cast(int, value["expected"]) < 300
        for value in postconditions
    ):
        return [f"{prefix}: HTTP step must assert an exact successful status_code"]
    if method not in _HTTP_MUTATION_METHODS or method == "DELETE":
        return []
    unbound_reference_paths = _literal_nested_identity_paths(
        cast(dict[str, Any], step.get("inputs", {})).get("json")
    )
    if unbound_reference_paths:
        return [
            f"{prefix}: mutating HTTP setup must resolve nested identity fields from earlier "
            f"verified output bindings: {unbound_reference_paths}"
        ]
    response_assertions = [
        value for value in postconditions if value.get("observe_via") == "response"
    ]
    if not response_assertions:
        return [f"{prefix}: mutating HTTP setup must assert returned business fields"]
    output_bindings = cast(list[dict[str, Any]], step.get("output_bindings", []))
    if not any(
        value.get("source") == "response" and value.get("required") is True
        for value in output_bindings
    ):
        return [f"{prefix}: mutating HTTP setup must bind a required response identity"]
    if not path.startswith("/") or path.startswith("//"):
        return [f"{prefix}: HTTP path must be origin-relative"]
    return []


def _literal_nested_identity_paths(value: object, *, path: str = "") -> list[str]:
    """Reject hard-coded nested object IDs such as employee.id in setup payloads."""

    if isinstance(value, list):
        return [
            nested
            for index, item in enumerate(value)
            for nested in _literal_nested_identity_paths(item, path=f"{path}[{index}]")
        ]
    if not isinstance(value, dict):
        return []
    paths: list[str] = []
    for key, item in value.items():
        current = f"{path}.{key}" if path else str(key)
        if (
            key == "id"
            and path
            and not (isinstance(item, str) and _VARIABLE.fullmatch(item.strip()) is not None)
        ):
            paths.append(current)
        paths.extend(_literal_nested_identity_paths(item, path=current))
    return paths


def _planned_http_target(step: dict[str, Any]) -> tuple[str, str]:
    inputs = step.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("HTTP inputs must be an object")
    parts = str(step.get("target") or "").split(maxsplit=1)
    if len(parts) != 2:
        raise ValueError("HTTP target must contain the reviewed method and path")
    target_method, target_path = parts[0].upper(), parts[1]
    method = str(inputs.get("method") or target_method).upper()
    path = str(inputs.get("path") or target_path)
    if method not in _HTTP_METHODS:
        raise ValueError(f"unsupported HTTP method: {method}")
    if (method, path) != (target_method, target_path):
        raise ValueError("HTTP inputs differ from the reviewed target")
    if not path.startswith("/") or path.startswith("//") or "://" in path:
        raise ValueError("HTTP path must be origin-relative")
    return method, path
