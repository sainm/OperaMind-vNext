"""Versioned, reusable business-data templates for cross-screen test plans."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from operamind.application.test_data_flow import validate_test_data_plan_artifact
from operamind.contracts import ContractCatalog

_PARAMETER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_RUNTIME_VARIABLE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


@dataclass(frozen=True, slots=True)
class BusinessDataTemplateRequest:
    """Instance identity and non-secret inputs used to build one TestDataPlan."""

    instance_id: str
    test_data_plan_id: str
    test_plan_id: str
    project_id: str
    test_case_refs: tuple[str, ...]
    parameters: Mapping[str, object]

    def __post_init__(self) -> None:
        identities = (
            self.instance_id,
            self.test_data_plan_id,
            self.test_plan_id,
            self.project_id,
            *self.test_case_refs,
        )
        if not self.test_case_refs or any(not value.strip() for value in identities):
            raise ValueError("Business data template instance identity must not be blank")


class BusinessDataTemplateInstantiator:
    """Validate an approved template and materialize an executable plan safely."""

    def __init__(self, contracts: ContractCatalog) -> None:
        self._contracts = contracts

    def instantiate(
        self,
        *,
        template: dict[str, Any],
        request: BusinessDataTemplateRequest,
    ) -> dict[str, Any]:
        self._contracts.validate_artifact(template)
        if template["artifact_type"] != "BusinessDataTemplate":
            raise ValueError("Business data template Artifact is required")
        if template["status"] != "approved":
            raise ValueError("Only an approved BusinessDataTemplate can be instantiated")
        if template["project_id"] != request.project_id:
            raise ValueError("Business data template Project does not match the request")

        semantic_reasons = validate_business_data_template(template)
        if semantic_reasons:
            raise ValueError("Invalid BusinessDataTemplate: " + "; ".join(semantic_reasons))
        parameters = _bind_parameters(template, request.parameters)
        preconditions = _evaluate_preconditions(template, parameters)
        blocking_reasons = [
            f"{value['precondition_id']}: {value['description_ja']}"
            for value in preconditions
            if value["status"] == "blocked"
        ]

        prefix = request.instance_id
        data_sets = cast(
            list[dict[str, Any]],
            _substitute(copy.deepcopy(template["data_sets"]), parameters),
        )
        for data_set in data_sets:
            data_set["test_data_id"] = _prefixed(prefix, str(data_set["test_data_id"]))
            data_set["test_case_refs"] = list(request.test_case_refs)
        flow = cast(
            dict[str, Any],
            _substitute(copy.deepcopy(template["generation_flow"]), parameters),
        )
        _prefix_flow(flow, prefix)
        flow["test_data_refs"] = [value["test_data_id"] for value in data_sets]
        flow["test_case_refs"] = list(request.test_case_refs)
        plan: dict[str, Any] = {
            "artifact_type": "TestDataPlan",
            "schema_version": "v1",
            "test_data_plan_id": request.test_data_plan_id,
            "test_plan_id": request.test_plan_id,
            "project_id": request.project_id,
            "status": "blocked" if blocking_reasons else "ready",
            "template_instances": [
                {
                    "instance_id": request.instance_id,
                    "template_id": template["template_id"],
                    "template_key": template["template_key"],
                    "template_version": template["template_version"],
                    "parameter_names": sorted(parameters),
                    "precondition_results": preconditions,
                    "entity_order": _entity_order(template),
                    "cleanup_entity_order": list(reversed(_entity_order(template))),
                }
            ],
            "data_sets": data_sets,
            "generation_flows": [flow],
            "blocking_reasons": blocking_reasons,
        }
        self._contracts.validate_artifact(plan)
        flow_reasons = validate_test_data_plan_artifact(plan)
        if flow_reasons:
            raise ValueError("Instantiated TestDataPlan is invalid: " + "; ".join(flow_reasons))
        return plan


def validate_business_data_template(template: Mapping[str, object]) -> list[str]:
    """Return deterministic semantic errors for entity and variable relationships."""

    reasons: list[str] = []
    parameters = cast(list[dict[str, Any]], template.get("parameters", []))
    parameter_names = [str(value.get("name", "")) for value in parameters]
    if len(parameter_names) != len(set(parameter_names)):
        reasons.append("Template parameter names must be unique")
    for condition in cast(list[dict[str, Any]], template.get("preconditions", [])):
        if str(condition.get("parameter", "")) not in parameter_names:
            reasons.append(
                f"{condition.get('precondition_id')}: precondition references an unknown parameter"
            )

    flow = cast(dict[str, Any], template.get("generation_flow", {}))
    setup_steps = cast(list[dict[str, Any]], flow.get("steps", []))
    cleanup_steps = cast(list[dict[str, Any]], flow.get("cleanup_steps", []))
    setup_by_id = {str(value.get("step_id", "")): value for value in setup_steps}
    cleanup_by_id = {str(value.get("step_id", "")): value for value in cleanup_steps}
    setup_sequence = {key: int(value.get("sequence", 0)) for key, value in setup_by_id.items()}
    cleanup_sequence = {key: int(value.get("sequence", 0)) for key, value in cleanup_by_id.items()}

    entities = cast(list[dict[str, Any]], template.get("entities", []))
    entity_by_ref = {str(value.get("entity_ref", "")): value for value in entities}
    if len(entity_by_ref) != len(entities):
        reasons.append("Entity refs must be unique")
    if not any(value.get("role") == "master" for value in entities):
        reasons.append("At least one master entity is required")
    if not any(value.get("role") == "detail" for value in entities):
        reasons.append("At least one detail entity is required")
    try:
        order = _entity_order(template)
    except ValueError as error:
        reasons.append(str(error))
        order = []
    order_index = {value: index for index, value in enumerate(order)}
    for entity_ref, entity in entity_by_ref.items():
        dependencies = cast(list[str], entity.get("depends_on", []))
        if entity.get("role") == "detail" and not dependencies:
            reasons.append(f"{entity_ref}: detail entity requires a master dependency")
        for dependency in dependencies:
            if dependency not in entity_by_ref:
                reasons.append(f"{entity_ref}: entity dependency {dependency} is unknown")
            elif (
                entity.get("role") == "master" and entity_by_ref[dependency].get("role") == "detail"
            ):
                reasons.append(f"{entity_ref}: master entity cannot depend on a detail entity")
            elif order_index.get(dependency, -1) >= order_index.get(entity_ref, -1):
                reasons.append(f"{entity_ref}: master/detail entity generation order is invalid")
        producer = str(entity.get("producer_step_id", ""))
        cleanup = str(entity.get("cleanup_step_id", ""))
        if producer not in setup_by_id:
            reasons.append(f"{entity_ref}: producer step is unknown")
        elif setup_by_id[producer].get("entity_ref") != entity_ref:
            reasons.append(f"{entity_ref}: producer step entity_ref does not match")
        if cleanup not in cleanup_by_id:
            reasons.append(f"{entity_ref}: cleanup step is unknown")
        elif cleanup_by_id[cleanup].get("entity_ref") != entity_ref:
            reasons.append(f"{entity_ref}: cleanup step entity_ref does not match")
        for dependency in dependencies:
            parent = entity_by_ref.get(dependency)
            if parent is None:
                continue
            parent_producer = str(parent.get("producer_step_id", ""))
            parent_cleanup = str(parent.get("cleanup_step_id", ""))
            if setup_sequence.get(parent_producer, 0) >= setup_sequence.get(producer, 0):
                reasons.append(f"{entity_ref}: detail must be generated after its master")
            if cleanup_sequence.get(cleanup, 0) >= cleanup_sequence.get(parent_cleanup, 0):
                reasons.append(f"{entity_ref}: detail must be cleaned before its master")

    all_steps = {**setup_by_id, **cleanup_by_id}
    shared = cast(list[dict[str, Any]], template.get("shared_variables", []))
    shared_names = [str(value.get("variable", "")) for value in shared]
    if len(shared_names) != len(set(shared_names)):
        reasons.append("Shared variable names must be unique")
    for value in shared:
        variable = str(value.get("variable", ""))
        producer_id = str(value.get("producer_step_id", ""))
        producer_step = setup_by_id.get(producer_id)
        outputs = {
            str(item.get("variable", ""))
            for item in cast(
                list[dict[str, Any]],
                (producer_step or {}).get("output_bindings", []),
            )
        }
        if producer_step is None or variable not in outputs:
            reasons.append(f"{variable}: shared variable producer is invalid")
        for consumer_id in cast(list[str], value.get("consumer_step_ids", [])):
            consumer = all_steps.get(consumer_id)
            if consumer is None:
                reasons.append(f"{variable}: consumer step {consumer_id} is unknown")
            elif variable not in _runtime_variables(consumer):
                reasons.append(f"{variable}: consumer step {consumer_id} does not use the variable")
    for entity_ref, entity in entity_by_ref.items():
        entity_producer_step = setup_by_id.get(str(entity.get("producer_step_id", "")), {})
        outputs = {
            str(item.get("variable", ""))
            for item in cast(list[dict[str, Any]], entity_producer_step.get("output_bindings", []))
        }
        missing = set(cast(list[str], entity.get("identifier_variables", []))) - outputs
        if missing:
            reasons.append(
                f"{entity_ref}: identifier variables are not produced: {sorted(missing)}"
            )
    return sorted(set(reasons))


def _bind_parameters(
    template: Mapping[str, object], supplied: Mapping[str, object]
) -> dict[str, object]:
    definitions = cast(list[dict[str, Any]], template["parameters"])
    known = {str(value["name"]) for value in definitions}
    unexpected = set(supplied) - known
    if unexpected:
        raise ValueError(f"Unknown business data template parameters: {sorted(unexpected)}")
    bound: dict[str, object] = {}
    missing: list[str] = []
    for definition in definitions:
        name = str(definition["name"])
        if name in supplied:
            bound[name] = supplied[name]
        elif "default" in definition:
            bound[name] = definition["default"]
        elif definition["required"]:
            missing.append(name)
        else:
            bound[name] = None
    if missing:
        raise ValueError(f"Required business data template parameters are missing: {missing}")
    return bound


def _evaluate_preconditions(
    template: Mapping[str, object], parameters: Mapping[str, object]
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for condition in cast(list[dict[str, Any]], template["preconditions"]):
        actual = parameters[str(condition["parameter"])]
        operator = str(condition["operator"])
        expected = condition.get("expected")
        passed = False
        if operator == "exists":
            passed = actual is not None
        elif operator == "non_blank":
            passed = isinstance(actual, str) and bool(actual.strip())
        elif operator == "equals":
            passed = actual == expected
        elif operator == "one_of":
            passed = isinstance(expected, list) and actual in expected
        elif operator == "greater_than":
            passed = (
                isinstance(actual, (int, float))
                and isinstance(expected, (int, float))
                and actual > expected
            )
        results.append(
            {
                "precondition_id": str(condition["precondition_id"]),
                "description_ja": str(condition["description_ja"]),
                "status": "passed" if passed else "blocked",
            }
        )
    return results


def _entity_order(template: Mapping[str, object]) -> list[str]:
    entities = cast(list[dict[str, Any]], template["entities"])
    dependencies = {
        str(value["entity_ref"]): set(cast(list[str], value["depends_on"])) for value in entities
    }
    order: list[str] = []
    while dependencies:
        ready = sorted(key for key, values in dependencies.items() if values.issubset(order))
        if not ready:
            raise ValueError("Entity dependencies must be acyclic")
        for entity_ref in ready:
            order.append(entity_ref)
            dependencies.pop(entity_ref)
    return order


def _substitute(value: object, parameters: Mapping[str, object]) -> object:
    if isinstance(value, str):
        exact = _PARAMETER.fullmatch(value)
        if exact is not None:
            return parameters[exact.group(1)]
        return _PARAMETER.sub(lambda match: str(parameters[match.group(1)]), value)
    if isinstance(value, list):
        return [_substitute(item, parameters) for item in value]
    if isinstance(value, dict):
        return {key: _substitute(item, parameters) for key, item in value.items()}
    return value


def _prefix_flow(flow: dict[str, Any], prefix: str) -> None:
    flow["flow_id"] = _prefixed(prefix, str(flow["flow_id"]))


def _runtime_variables(value: object) -> set[str]:
    if isinstance(value, str):
        return set(_RUNTIME_VARIABLE.findall(value))
    if isinstance(value, list):
        return set().union(*(_runtime_variables(item) for item in value), set())
    if isinstance(value, dict):
        return set().union(*(_runtime_variables(item) for item in value.values()), set())
    return set()


def _prefixed(prefix: str, value: str) -> str:
    return f"{prefix}-{value}"
