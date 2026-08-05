"""Build and validate executable, dependency-aware test-data generation flows."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from operamind.application.data_identity import (
    DEFAULT_DATA_IDENTITY_PROVIDER_TYPES,
    is_sensitive_data_identity_name,
)
from operamind.application.run_context import SYSTEM_RUNTIME_VARIABLE_NAMES

_VARIABLE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
_HTTP_MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_HTTP_GENERATION_METHODS = frozenset({"POST", "PUT", "PATCH"})
_DATA_GENERATION_EFFECTS = frozenset({"creates", "updates"})
_STATE_CHANGING_PLAYWRIGHT_ACTIONS = frozenset(
    {
        "click",
        "double_click",
        "fill",
        "type",
        "clear",
        "select_option",
        "check",
        "uncheck",
        "press",
        "drag_to",
    }
)
_UNSAFE_ORDINAL_LOCATOR = re.compile(
    r"(?i)(:nth-(?:child|of-type)|aria-rowindex|data-(?:row-)?index|row[_-]?number)"
)
_TABLE_LOCATOR = re.compile(
    r"(?i)(?:^|[\s>+~.#\[])(?:table|tbody|tr|td|grid|row)(?:$|[\s>+~.#:\[])"
)
_DYNAMIC_CSS_LOCATOR = re.compile(
    r"(?i)(?:\[class\s*[\^*$]=|\.css-[0-9a-f]{6,}|__[A-Za-z0-9_-]*[0-9a-f]{8,})"
)
_IDENTITY_SOURCE_GROUP = {
    "database": "database",
    "api": "api",
    "response": "api",
    "ui": "ui",
}


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
    *,
    runtime_variables: frozenset[str] = frozenset(),
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
        available_variables: set[str] = set(runtime_variables)
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
            reserved_outputs = set(output_names).intersection(runtime_variables)
            if reserved_outputs:
                reasons.append(
                    f"{flow_id}/{step_id}: system Run variables are read-only: "
                    f"{sorted(reserved_outputs)}"
                )
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


def validate_test_data_plan_artifact(
    plan: dict[str, Any],
    *,
    identity_provider_types: Mapping[str, str] = DEFAULT_DATA_IDENTITY_PROVIDER_TYPES,
) -> list[str]:
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
        runtime_variables=(
            SYSTEM_RUNTIME_VARIABLE_NAMES if plan.get("schema_version") == "v3" else frozenset()
        ),
    )
    reasons.extend(
        f"TestDataPlan contains a Secret-like field: {path}"
        for path in _secret_field_paths(plan)
    )
    channels = {
        str(step.get("channel") or "")
        for flow in cast(list[dict[str, Any]], plan.get("generation_flows", []))
        for collection in ("steps", "cleanup_steps")
        for step in cast(list[dict[str, Any]], flow.get(collection, []))
    }
    # SQL is executable only after the Project-aware Target Data Profile gate.
    # Fixture remains an explicitly injected test adapter, never a production channel.
    unavailable = sorted(channels - {"http", "sql", "ui"})
    reasons.extend(
        f"Test data channel has no project-bound executor: {channel}" for channel in unavailable
    )
    if plan.get("schema_version") in {"v2", "v3"}:
        reasons.extend(
            _validate_v2_generation_contract(
                data_sets,
                cast(list[dict[str, Any]], plan.get("generation_flows", [])),
                identity_provider_types=identity_provider_types,
                allow_cross_flow=plan.get("schema_version") == "v3",
            )
        )
    if plan.get("schema_version") in {"v2", "v3"}:
        reasons.extend(
            _validate_v2_data_coverage_contract(
                data_sets,
                cast(list[dict[str, Any]], plan.get("generation_flows", [])),
            )
        )
    if plan.get("schema_version") == "v3":
        reasons.extend(
            _validate_v3_run_context_contract(
                data_sets,
                cast(list[dict[str, Any]], plan.get("generation_flows", [])),
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


def _validate_v3_run_context_contract(
    data_sets: list[dict[str, Any]],
    flows: list[dict[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    flow_ids = {str(flow.get("flow_id", "")) for flow in flows}
    dependencies = {
        str(flow.get("flow_id", "")): {
            str(value) for value in cast(list[object], flow.get("depends_on_flows", []))
        }
        for flow in flows
    }
    for flow_id, values in dependencies.items():
        unknown = values - flow_ids
        if unknown:
            reasons.append(f"{flow_id}: Flow dependencies do not exist: {sorted(unknown)}")
        if flow_id in values:
            reasons.append(f"{flow_id}: Flow cannot depend on itself")
    remaining = {key: set(values).intersection(flow_ids) for key, values in dependencies.items()}
    while remaining:
        ready = {key for key, values in remaining.items() if not values}
        if not ready:
            reasons.append(f"Flow dependency cycle exists: {sorted(remaining)}")
            break
        for key in ready:
            remaining.pop(key)
        for values in remaining.values():
            values.difference_update(ready)

    writes_by_data: dict[str, set[tuple[str, str]]] = {}
    source_channels_by_data: dict[str, set[str]] = {}
    for data_set in data_sets:
        test_data_id = str(data_set.get("test_data_id", ""))
        writes = {
            (str(value.get("variable", "")), str(value.get("target_field", "")))
            for value in cast(list[dict[str, Any]], data_set.get("runtime_variable_writes", []))
        }
        identity = cast(dict[str, Any], data_set.get("identity_binding") or {})
        source_channels_by_data[test_data_id] = {
            {"database": "sql", "api": "http"}[group]
            for group in _identity_source_groups(identity)
            if group in {"database", "api"}
        }
        if identity.get("binding_mode") == "adopted" and writes:
            reasons.append(
                f"{test_data_id}: adopted data cannot overwrite business values with Run variables"
            )
        writes_by_data[test_data_id] = writes

    for flow in flows:
        flow_id = str(flow.get("flow_id", ""))
        allowed = set().union(
            *(
                writes_by_data.get(str(reference), set())
                for reference in cast(list[object], flow.get("test_data_refs", []))
            ),
            set(),
        )
        for collection in ("steps", "cleanup_steps"):
            collection_steps = cast(list[dict[str, Any]], flow.get(collection, []))
            for step_index, step in enumerate(collection_steps):
                step_id = str(step.get("step_id", "<unknown>"))
                forbidden = (
                    _variables_in(step.get("target", ""))
                    | _variables_in(step.get("playwright", {}))
                ).intersection(SYSTEM_RUNTIME_VARIABLE_NAMES)
                if forbidden:
                    reasons.append(
                        f"{flow_id}/{step_id}: system Run variables may only be written to "
                        f"explicit business input fields: {sorted(forbidden)}"
                    )
                uses = _runtime_variable_uses(step.get("inputs", {}))
                unauthorized = uses - allowed
                if unauthorized:
                    reasons.append(
                        f"{flow_id}/{step_id}: Run variable writes are not explicitly allowed: "
                        f"{sorted(unauthorized)}"
                    )
                if (
                    collection == "cleanup_steps"
                    and step.get("channel") == "ui"
                    and step.get("data_binding_ref")
                ):
                    binding_ref = str(step["data_binding_ref"])
                    has_zero_ui_assertion = any(
                        assertion.get("observe_via") == "ui"
                        and assertion.get("subject")
                        == "cleanup_record_scope_match_count"
                        and assertion.get("operator") == "count_equals"
                        and assertion.get("expected") == 0
                        for assertion in cast(list[dict[str, Any]], step.get("postconditions", []))
                    )
                    if not has_zero_ui_assertion:
                        reasons.append(
                            f"{flow_id}/{step_id}: bound UI cleanup must verify match count 0"
                        )
                    later = collection_steps[step_index + 1 :]
                    required_channels = source_channels_by_data.get(binding_ref, set())
                    verified_channels = {
                        str(candidate.get("channel"))
                        for candidate in later
                        if candidate.get("data_binding_ref") == binding_ref
                        and any(
                            (
                                assertion.get("observe_via") == "database"
                                if candidate.get("channel") == "sql"
                                else assertion.get("observe_via") in {"response", "api"}
                            )
                            and
                            assertion.get("operator") == "count_equals"
                            and assertion.get("expected") == 0
                            for assertion in cast(
                                list[dict[str, Any]], candidate.get("postconditions", [])
                            )
                        )
                    }
                    missing_channels = required_channels - verified_channels
                    if missing_channels:
                        reasons.append(
                            f"{flow_id}/{step_id}: cleanup absence verification is missing "
                            f"for {sorted(missing_channels)}"
                        )
    return reasons


def _secret_field_paths(value: object, *, path: str = "") -> list[str]:
    if isinstance(value, Mapping):
        paths: list[str] = []
        for key, item in value.items():
            key_name = str(key)
            child_path = f"{path}.{key_name}" if path else key_name
            if is_sensitive_data_identity_name(key_name):
                paths.append(child_path)
            else:
                paths.extend(_secret_field_paths(item, path=child_path))
        return paths
    if isinstance(value, list | tuple):
        return [
            reason
            for index, item in enumerate(value)
            for reason in _secret_field_paths(item, path=f"{path}[{index}]")
        ]
    return []


def _runtime_variable_uses(
    value: object,
    *,
    path: str = "",
) -> set[tuple[str, str]]:
    if isinstance(value, str):
        return {
            (name, path)
            for name in _VARIABLE.findall(value)
            if name in SYSTEM_RUNTIME_VARIABLE_NAMES
        }
    if isinstance(value, list):
        return set().union(
            *(
                _runtime_variable_uses(item, path=f"{path}[{index}]")
                for index, item in enumerate(value)
            ),
            set(),
        )
    if isinstance(value, dict):
        return set().union(
            *(
                _runtime_variable_uses(
                    item,
                    path=f"{path}.{key}" if path else str(key),
                )
                for key, item in value.items()
            ),
            set(),
        )
    return set()


def _flow_dependency_closure(
    dependencies: Mapping[str, set[str]],
) -> dict[str, set[str]]:
    closure = {key: set(values) for key, values in dependencies.items()}
    changed = True
    while changed:
        changed = False
        for flow_id, values in closure.items():
            expanded = set(values)
            for dependency in tuple(values):
                expanded.update(closure.get(dependency, set()))
            if expanded != values:
                closure[flow_id] = expanded
                changed = True
    return closure


def _validate_v2_generation_contract(
    data_sets: list[dict[str, Any]],
    flows: list[dict[str, Any]],
    *,
    identity_provider_types: Mapping[str, str],
    allow_cross_flow: bool = False,
) -> list[str]:
    """Reject UI plans that merely assume target-system data already exists."""

    reasons: list[str] = []
    data_by_id = {str(value.get("test_data_id", "")): value for value in data_sets}
    source_positions: dict[str, tuple[str, int]] = {}
    flow_dependencies = {
        str(flow.get("flow_id", "")): {
            str(value) for value in cast(list[object], flow.get("depends_on_flows", []))
        }
        for flow in flows
    }
    dependency_closure = _flow_dependency_closure(flow_dependencies)
    bound_ui_refs: set[str] = set()
    for test_data_id, data_set in data_by_id.items():
        identity = data_set.get("identity_binding")
        if not isinstance(identity, dict):
            reasons.append(f"{test_data_id}: v2 test data requires an identity_binding")
            continue
        source_flow_id = str(identity.get("source_flow_id", ""))
        source_step_id = str(identity.get("source_step_id", ""))
        matching_flow = next(
            (flow for flow in flows if str(flow.get("flow_id", "")) == source_flow_id),
            None,
        )
        if matching_flow is None or test_data_id not in {
            str(value) for value in cast(list[object], matching_flow.get("test_data_refs", []))
        }:
            reasons.append(
                f"{test_data_id}: identity source flow must exist and reference the test data"
            )
            continue
        steps = cast(list[dict[str, Any]], matching_flow.get("steps", []))
        source_index = next(
            (index for index, step in enumerate(steps) if step.get("step_id") == source_step_id),
            None,
        )
        if source_index is None:
            reasons.append(f"{test_data_id}: identity source step does not exist in setup")
            continue
        source_step = steps[source_index]
        provider = cast(dict[str, Any], identity.get("provider") or {})
        provider_type = str(provider.get("type", ""))
        expected_channels = {
            "database": {"sql"},
            "api": {"http"},
            "ui": {"ui"},
            "hybrid": {"sql", "http", "ui"},
        }.get(provider_type, set())
        if source_step.get("channel") not in expected_channels:
            reasons.append(
                f"{test_data_id}: {provider_type or '<blank>'} identity source step has "
                "an incompatible channel"
            )
        if provider_type == "hybrid":
            required_channels = {
                {"database": "sql", "api": "http", "ui": "ui"}[group]
                for group in _identity_source_groups(identity)
                if group in {"database", "api", "ui"}
            }
            observed_channels = {str(step.get("channel")) for step in steps[: source_index + 1]}
            missing_channels = required_channels - observed_channels
            if missing_channels:
                reasons.append(
                    f"{test_data_id}: hybrid identity has no executable source steps for "
                    f"channels {sorted(missing_channels)}"
                )
        if identity.get("binding_mode") == "generated" and not any(
            _is_explicit_data_generation_step(step) for step in steps[: source_index + 1]
        ):
            reasons.append(
                f"{test_data_id}: generated identity requires an earlier explicit data "
                "generation step"
            )
        source_positions[test_data_id] = (source_flow_id, source_index)
        reasons.extend(
            _validate_identity_definition(
                test_data_id,
                identity,
                identity_provider_types=identity_provider_types,
            )
        )
    for flow in flows:
        flow_id = str(flow.get("flow_id", "<unknown>"))
        steps = cast(list[dict[str, Any]], flow.get("steps", []))
        for collection in ("steps", "cleanup_steps"):
            for step_index, step in enumerate(cast(list[dict[str, Any]], flow.get(collection, []))):
                if step.get("channel") == "http":
                    reasons.extend(
                        _validate_http_step(
                            flow_id,
                            step,
                            cleanup=collection == "cleanup_steps",
                        )
                    )
                if step.get("channel") != "ui":
                    continue
                reasons.extend(_validate_ui_locator_safety(flow_id, step))
                operation_scope = str(step.get("operation_scope", ""))
                binding_ref = str(step.get("data_binding_ref", ""))
                if operation_scope not in {"screen", "bound_record"}:
                    reasons.append(
                        f"{flow_id}/{step.get('step_id')}: v2 UI operation requires an "
                        "explicit screen or bound_record operation_scope"
                    )
                elif operation_scope == "bound_record" and not binding_ref:
                    reasons.append(
                        f"{flow_id}/{step.get('step_id')}: bound_record operation requires "
                        "data_binding_ref"
                    )
                elif operation_scope == "screen" and binding_ref:
                    reasons.append(
                        f"{flow_id}/{step.get('step_id')}: screen operation cannot carry "
                        "data_binding_ref"
                    )
                if not binding_ref:
                    continue
                bound_ui_refs.add(binding_ref)
                source = source_positions.get(binding_ref)
                if binding_ref not in {
                    str(value) for value in cast(list[object], flow.get("test_data_refs", []))
                }:
                    reasons.append(
                        f"{flow_id}/{step.get('step_id')}: data_binding_ref is outside the flow"
                    )
                source_is_available = source is not None and (
                    (
                        source[0] == flow_id
                        and (
                            collection == "cleanup_steps"
                            or (collection == "steps" and step_index > source[1])
                        )
                    )
                    or (allow_cross_flow and source[0] in dependency_closure.get(flow_id, set()))
                )
                if not source_is_available:
                    reasons.append(
                        f"{flow_id}/{step.get('step_id')}: bound UI operation must follow its "
                        "identity source through an explicit Flow dependency"
                    )
                action = step.get("playwright")
                if not isinstance(action, dict) or not isinstance(action.get("locator"), dict):
                    reasons.append(
                        f"{flow_id}/{step.get('step_id')}: bound UI operation requires a "
                        "relative Playwright locator"
                    )
                if step.get("computer_use_fallback") is not None:
                    reasons.append(
                        f"{flow_id}/{step.get('step_id')}: bound UI operation cannot use AI "
                        "computer-use fallback"
                    )
    for test_data_id, source in source_positions.items():
        source_flow = next(flow for flow in flows if flow.get("flow_id") == source[0])
        later_ui = any(
            step.get("channel") == "ui"
            for step in cast(list[dict[str, Any]], source_flow.get("steps", []))[source[1] + 1 :]
        )
        if later_ui and test_data_id not in bound_ui_refs:
            reasons.append(
                f"{test_data_id}: post-binding UI flow requires at least one exact bound "
                "record operation"
            )
    return reasons


def _validate_v2_data_coverage_contract(
    data_sets: list[dict[str, Any]],
    flows: list[dict[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    flow_by_id = {str(value.get("flow_id", "")): value for value in flows}
    positions: dict[tuple[str, str], int] = {}
    test_ui_positions: list[int] = []
    position = 0
    for flow in flows:
        flow_id = str(flow.get("flow_id", ""))
        for step in cast(list[dict[str, Any]], flow.get("steps", [])):
            positions[(flow_id, str(step.get("step_id", "")))] = position
            if step.get("channel") == "ui" and cast(list[object], step.get("test_step_refs", [])):
                test_ui_positions.append(position)
            position += 1

    condition_ids: list[str] = []
    coverage_positions: list[int] = []
    kind_operators = {
        "field": {"equals", "not_equals", "contains", "exists", "in"},
        "status": {"equals", "not_equals", "in"},
        "boundary": {
            "greater_than",
            "greater_than_or_equal",
            "less_than",
            "less_than_or_equal",
            "between",
        },
        "relationship": {"equals_path", "not_equals_path"},
    }
    for data_set in data_sets:
        test_data_id = str(data_set.get("test_data_id", ""))
        conditions = cast(list[dict[str, Any]], data_set.get("coverage_conditions", []))
        if not conditions:
            reasons.append(f"{test_data_id}: v2 test data requires executable coverage_conditions")
        for condition in conditions:
            condition_id = str(condition.get("condition_id", ""))
            condition_ids.append(condition_id)
            prefix = f"{test_data_id}/{condition_id or '<unknown>'}"
            if condition.get("test_data_id") != test_data_id:
                reasons.append(f"{prefix}: condition test_data_id differs from its data set")
            if condition.get("test_case_ref") not in cast(
                list[object], data_set.get("test_case_refs", [])
            ):
                reasons.append(f"{prefix}: condition TestCase is outside its data set")
            source = (
                str(condition.get("source_flow_id", "")),
                str(condition.get("source_step_id", "")),
            )
            source_position = positions.get(source)
            if source_position is None:
                reasons.append(f"{prefix}: coverage source step does not exist")
            else:
                coverage_positions.append(source_position)
                flow = flow_by_id.get(source[0], {})
                source_step = next(
                    (
                        value
                        for value in cast(list[dict[str, Any]], flow.get("steps", []))
                        if value.get("step_id") == source[1]
                    ),
                    None,
                )
                if source_step is None or source_step.get("channel") != "sql":
                    reasons.append(f"{prefix}: coverage source must be a SQL readback")
            kind = str(condition.get("condition_kind", ""))
            operator = str(condition.get("operator", ""))
            if operator not in kind_operators.get(kind, set()):
                reasons.append(
                    f"{prefix}: {operator or '<blank>'} is invalid for {kind or '<blank>'}"
                )
    if len(condition_ids) != len(set(condition_ids)):
        reasons.append("Test data coverage condition IDs must be globally unique")
    if (
        coverage_positions
        and test_ui_positions
        and max(coverage_positions) >= min(test_ui_positions)
    ):
        reasons.append(
            "All real database data coverage conditions must execute before the first "
            "TestPlan UI step"
        )
    return reasons


def _validate_identity_definition(
    test_data_id: str,
    identity: dict[str, Any],
    *,
    identity_provider_types: Mapping[str, str],
) -> list[str]:
    reasons: list[str] = []
    provider = identity.get("provider")
    if not isinstance(provider, dict):
        reasons.append(f"{test_data_id}: DataIdentityProvider is not configured")
        provider_type = ""
    else:
        provider_type = str(provider.get("type", ""))
        provider_ref = str(provider.get("provider_ref", ""))
        if provider_type not in {"database", "api", "ui", "hybrid"} or not provider_ref:
            reasons.append(f"{test_data_id}: DataIdentityProvider configuration is invalid")
        elif identity_provider_types.get(provider_ref) != provider_type:
            reasons.append(
                f"{test_data_id}: DataIdentityProvider is not configured for "
                f"{provider_type}:{provider_ref}"
            )
    screen_values = cast(list[dict[str, Any]], identity.get("screen_identity_values") or [])
    if not screen_values:
        screen_values = [cast(dict[str, Any], identity.get("screen_key") or {})]
    elif any(
        cast(dict[str, Any], identity.get("screen_key") or {}).get(key) != screen_values[0].get(key)
        for key in ("name", "source", "path")
    ):
        reasons.append(
            f"{test_data_id}: screen_key must equal the first screen_identity_values entry"
        )
    values = [
        cast(dict[str, Any], identity.get("primary_key") or {}),
        *cast(list[dict[str, Any]], identity.get("business_unique_keys") or []),
        *screen_values,
    ]
    names = [str(value.get("name", "")) for value in values]
    business_names = [
        str(value.get("name", ""))
        for value in cast(list[dict[str, Any]], identity.get("business_unique_keys") or [])
    ]
    if any(not name for name in names) or len(business_names) != len(set(business_names)):
        reasons.append(
            f"{test_data_id}: identity key names must be non-empty and business keys unique"
        )
    screen_names = [str(value.get("name", "")) for value in screen_values]
    if len(screen_names) != len(set(screen_names)):
        reasons.append(f"{test_data_id}: screen identity names must be unique")
    match_count = cast(dict[str, Any], identity.get("match_count") or {})
    source_values = [*values, match_count]
    source_groups = _identity_source_groups(identity)
    expected_groups = {
        "database": {"database"},
        "api": {"api"},
        "ui": {"ui"},
    }
    if provider_type == "hybrid":
        if "invalid" in source_groups or len(source_groups) < 2:
            reasons.append(
                f"{test_data_id}: hybrid DataIdentityProvider requires at least two real sources"
            )
    elif source_groups != expected_groups.get(provider_type, set()):
        reasons.append(
            f"{test_data_id}: identity value sources do not match the {provider_type or '<blank>'} "
            "DataIdentityProvider"
        )
    for value in source_values:
        name = str(value.get("name", ""))
        path = str(value.get("path", ""))
        components = [name, *re.split(r"[.\[\]]+", path)]
        if any(is_sensitive_data_identity_name(component) for component in components if component):
            reasons.append(f"{test_data_id}: Secret-like fields cannot be identity values")
    dom_values = [
        *cast(list[dict[str, Any]], identity.get("business_unique_keys") or []),
        *screen_values,
    ]
    for value in dom_values:
        name = str(value.get("name") or "<blank>")
        observation = value.get("dom_observation")
        if not isinstance(observation, dict):
            reasons.append(f"{test_data_id}: {name} の DOM 身元観測定義がありません")
            continue
        kind = str(observation.get("kind") or "")
        if kind not in {"text", "input_value", "attribute"}:
            reasons.append(f"{test_data_id}: {name} の DOM 身元観測種別が不正です")
        if kind == "attribute" and not str(observation.get("attribute_name") or "").strip():
            reasons.append(f"{test_data_id}: {name} の DOM 属性名がありません")
        locator = observation.get("locator")
        if isinstance(locator, dict):
            if locator.get("exact") is not True:
                reasons.append(f"{test_data_id}: {name} の DOM Locator は exact 必須です")
            reasons.extend(
                f"{test_data_id}: {name} DOM Locator: {reason}"
                for reason in _locator_safety_reasons(locator)
            )
    if not str(match_count.get("path", "")).strip():
        reasons.append(f"{test_data_id}: DataIdentityProvider match_count path is required")
    screen = screen_values[0]
    template = screen.get("locator_template")
    if isinstance(template, dict):
        placeholders = _locator_placeholders(template)
        allowed_placeholders = {"value", *screen_names}
        if "value" not in placeholders and screen_names[0] not in placeholders:
            reasons.append(
                f"{test_data_id}: screen locator template must contain the primary "
                "screen identity placeholder"
            )
        unknown_placeholders = placeholders - allowed_placeholders
        if unknown_placeholders:
            reasons.append(
                f"{test_data_id}: screen locator template has unknown placeholders: "
                f"{sorted(unknown_placeholders)}"
            )
        if len(screen_values) > 1 and not set(screen_names).issubset(placeholders):
            reasons.append(
                f"{test_data_id}: composite screen locator must match all screen identity values"
            )
        reasons.extend(f"{test_data_id}: {reason}" for reason in _locator_safety_reasons(template))
    return reasons


def _identity_source_groups(identity: Mapping[str, object]) -> set[str]:
    screen_values = identity.get("screen_identity_values")
    screens = (
        cast(list[Mapping[str, object]], screen_values)
        if isinstance(screen_values, list) and screen_values
        else [cast(Mapping[str, object], identity.get("screen_key") or {})]
    )
    values = [
        cast(Mapping[str, object], identity.get("primary_key") or {}),
        *cast(list[Mapping[str, object]], identity.get("business_unique_keys") or []),
        *screens,
        cast(Mapping[str, object], identity.get("match_count") or {}),
    ]
    return {_IDENTITY_SOURCE_GROUP.get(str(value.get("source", "")), "invalid") for value in values}


def _validate_ui_locator_safety(flow_id: str, step: dict[str, Any]) -> list[str]:
    action = step.get("playwright")
    if not isinstance(action, dict):
        return []
    locators: list[dict[str, Any]] = []
    for key in ("locator", "target_locator"):
        if isinstance(action.get(key), dict):
            locators.append(cast(dict[str, Any], action[key]))
    for observation_key in ("observations", "pre_action_observations"):
        locators.extend(
            cast(dict[str, Any], observation["locator"])
            for observation in cast(list[dict[str, Any]], action.get(observation_key, []))
            if isinstance(observation.get("locator"), dict)
        )
    locators.extend(
        cast(dict[str, Any], value)
        for value in cast(list[object], action.get("mask_locators", []))
        if isinstance(value, dict)
    )
    prefix = f"{flow_id}/{step.get('step_id')}"
    reasons = [
        f"{prefix}: {reason}" for locator in locators for reason in _locator_safety_reasons(locator)
    ]
    pre_action_observations = cast(
        list[dict[str, Any]], action.get("pre_action_observations", [])
    )
    if (
        str(action.get("action") or "") in _STATE_CHANGING_PLAYWRIGHT_ACTIONS
        and not pre_action_observations
    ):
        reasons.append(
            f"{prefix}: state-changing Playwright action requires reviewed "
            "pre_action_observations"
        )
    for observation in pre_action_observations:
        key = str(observation.get("key") or "")
        attribute_name = str(observation.get("attribute_name") or "")
        if any(
            is_sensitive_data_identity_name(value)
            for value in (key, attribute_name)
            if value
        ):
            reasons.append(
                f"{prefix}: pre-action observation cannot expose a Secret field"
            )
    if not step.get("data_binding_ref") and any(
        locator.get("by") == "css" and _TABLE_LOCATOR.search(str(locator.get("value", "")))
        for locator in locators
    ):
        reasons.append(f"{prefix}: table locator requires data_binding_ref")
    return reasons


def _locator_safety_reasons(locator: Mapping[str, object]) -> list[str]:
    value = str(locator.get("value", ""))
    reasons: list[str] = []
    if locator.get("exact") is False:
        reasons.append("fuzzy locator matching is forbidden")
    if (
        locator.get("by") in {"text", "label", "placeholder", "alt_text", "title", "role"}
        and locator.get("exact") is not True
    ):
        reasons.append("semantic/text locators must declare exact=true")
    if _UNSAFE_ORDINAL_LOCATOR.search(value):
        reasons.append("row-number and ordinal locators are forbidden")
    if locator.get("by") == "role" and not str(locator.get("name") or "").strip():
        reasons.append("role locator requires an accessible name")
    if locator.get("by") == "css" and _DYNAMIC_CSS_LOCATOR.search(value):
        reasons.append("unverified dynamic CSS locator is forbidden")
    filters = locator.get("all")
    if filters is not None:
        if (
            not isinstance(filters, list)
            or not filters
            or any(not isinstance(item, Mapping) for item in filters)
        ):
            reasons.append("composite locator filters must be non-empty locator objects")
        else:
            for item in filters:
                reasons.extend(_locator_safety_reasons(item))
    return reasons


def _locator_placeholders(value: object) -> set[str]:
    if isinstance(value, str):
        return set(re.findall(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", value))
    if isinstance(value, list):
        return set().union(*(_locator_placeholders(item) for item in value), set())
    if isinstance(value, Mapping):
        return set().union(*(_locator_placeholders(item) for item in value.values()), set())
    return set()


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
    if step.get("channel") == "sql":
        return any(
            value.get("source") == "database" and value.get("required") is True
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
