"""Deterministic TestData coverage alignment and runtime evaluation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast


def validate_test_data_coverage_alignment(
    *,
    test_plan: Mapping[str, object],
    test_data_plan: Mapping[str, object],
    acceptance_criteria: Mapping[str, object] | None = None,
) -> list[str]:
    """Require one executable real-readback condition per criterion/case/data tuple."""

    reasons: list[str] = []
    cases = cast(list[dict[str, Any]], test_plan.get("test_cases", []))
    data_sets = cast(list[dict[str, Any]], test_data_plan.get("data_sets", []))
    expected: set[tuple[str, str, str]] = set()
    for case in cases:
        case_id = str(case.get("test_case_id", ""))
        for criterion_ref in cast(list[object], case.get("acceptance_criteria_refs", [])):
            for test_data_ref in cast(list[object], case.get("test_data_refs", [])):
                expected.add((str(criterion_ref), case_id, str(test_data_ref)))

    criteria_by_id: dict[str, dict[str, Any]] = {}
    if acceptance_criteria is not None:
        criteria_by_id = {
            str(value.get("criterion_id", "")): value
            for value in cast(list[dict[str, Any]], acceptance_criteria.get("criteria", []))
        }
        referenced = {value[0] for value in expected}
        if referenced != set(criteria_by_id):
            reasons.append("AcceptanceCriteria identities must exactly match TestPlan references")

    actual: set[tuple[str, str, str]] = set()
    condition_ids: list[str] = []
    for data_set in data_sets:
        test_data_id = str(data_set.get("test_data_id", ""))
        identity = cast(dict[str, Any], data_set.get("identity_binding") or {})
        identity_paths = {
            str(cast(dict[str, Any], identity.get("primary_key") or {}).get("path", "")),
            str(cast(dict[str, Any], identity.get("screen_key") or {}).get("path", "")),
            *(
                str(value.get("path", ""))
                for value in cast(list[dict[str, Any]], identity.get("business_unique_keys", []))
            ),
        }
        identity_paths.discard("")
        for condition in cast(list[dict[str, Any]], data_set.get("coverage_conditions", [])):
            condition_id = str(condition.get("condition_id", ""))
            condition_ids.append(condition_id)
            tuple_key = (
                str(condition.get("criterion_ref", "")),
                str(condition.get("test_case_ref", "")),
                str(condition.get("test_data_id", "")),
            )
            actual.add(tuple_key)
            prefix = f"{test_data_id}/{condition_id or '<unknown>'}"
            if tuple_key[2] != test_data_id:
                reasons.append(f"{prefix}: condition test_data_id differs from its data set")
            if tuple_key not in expected:
                reasons.append(
                    f"{prefix}: condition is not bound to a TestPlan criterion/case/data tuple"
                )
            criterion = criteria_by_id.get(tuple_key[0])
            if criterion is not None and tuple_key[1] not in {
                str(value) for value in cast(list[object], criterion.get("test_case_refs", []))
            }:
                reasons.append(
                    f"{prefix}: condition TestCase is outside its AcceptanceCriteria scope"
                )
            if condition.get("source_flow_id") != identity.get("source_flow_id") or condition.get(
                "source_step_id"
            ) != identity.get("source_step_id"):
                reasons.append(
                    f"{prefix}: condition must use the frozen identity SQL readback step"
                )
            if identity_paths and str(condition.get("path", "")) in identity_paths:
                reasons.append(
                    f"{prefix}: identity keys alone cannot prove a business data condition"
                )
    if len(condition_ids) != len(set(condition_ids)):
        reasons.append("Test data coverage condition IDs must be globally unique")
    missing = expected - actual
    if missing:
        reasons.append(
            "Test data coverage conditions are missing for criterion/case/data tuples: "
            f"{sorted(missing)}"
        )
    return sorted(set(reasons))


def conditions_for_step(
    plan: Mapping[str, object], *, flow_id: str, step_id: str
) -> list[dict[str, Any]]:
    return [
        {**condition, "_test_data_id": str(data_set["test_data_id"])}
        for data_set in cast(list[dict[str, Any]], plan.get("data_sets", []))
        for condition in cast(list[dict[str, Any]], data_set.get("coverage_conditions", []))
        if condition.get("source_flow_id") == flow_id and condition.get("source_step_id") == step_id
    ]


def evaluate_condition(condition: Mapping[str, object], *, database: object) -> dict[str, object]:
    """Evaluate one condition only from the reviewed SQL readback observation."""

    exists, actual = extract_path(database, str(condition["path"]))
    operator = str(condition["operator"])
    expected_path = condition.get("expected_path")
    expected_exists = True
    expected = condition.get("expected")
    if expected_path is not None:
        expected_exists, expected = extract_path(database, str(expected_path))
    passed, reason = _compare(
        operator=operator,
        actual_exists=exists,
        actual=actual,
        expected_exists=expected_exists,
        expected=expected,
    )
    return {
        "condition_id": str(condition["condition_id"]),
        "criterion_ref": str(condition["criterion_ref"]),
        "test_case_ref": str(condition["test_case_ref"]),
        "test_data_id": str(condition["test_data_id"]),
        "condition_kind": str(condition["condition_kind"]),
        "source_flow_id": str(condition["source_flow_id"]),
        "source_step_id": str(condition["source_step_id"]),
        "path": str(condition["path"]),
        "operator": operator,
        "expected": expected,
        "actual": actual,
        "status": "passed" if passed else "failed",
        "failure_reason": reason,
    }


def summarize_data_coverage(
    *, plan: Mapping[str, object], proofs: list[dict[str, Any]]
) -> dict[str, object]:
    conditions = [
        condition
        for data_set in cast(list[dict[str, Any]], plan.get("data_sets", []))
        for condition in cast(list[dict[str, Any]], data_set.get("coverage_conditions", []))
    ]
    required_by_criterion: dict[str, set[str]] = {}
    for condition in conditions:
        required_by_criterion.setdefault(str(condition["criterion_ref"]), set()).add(
            str(condition["condition_id"])
        )
    passed_ids = {str(value["condition_id"]) for value in proofs if value.get("status") == "passed"}
    covered = sum(
        bool(required) and required.issubset(passed_ids)
        for required in required_by_criterion.values()
    )
    required_count = len(required_by_criterion)
    percent = covered * 100 / required_count if required_count else 0
    status = (
        "not_applicable"
        if not required_count
        else "passed"
        if covered == required_count
        else "failed"
    )
    return {
        "required_criterion_count": required_count,
        "covered_criterion_count": covered,
        "coverage_percent": percent,
        "condition_count": len(conditions),
        "passed_condition_count": len(passed_ids),
        "status": status,
        "proofs": sorted(proofs, key=lambda value: str(value["condition_id"])),
    }


def extract_path(source: object, path: str) -> tuple[bool, object | None]:
    if path in {"", "$"}:
        return source is not None, source
    normalized = path[2:] if path.startswith("$.") else path
    normalized = _normalize_array_indexes(normalized)
    if "[" in normalized or "]" in normalized:
        return False, None
    current = source
    for component in normalized.split("."):
        if not component:
            return False, None
        if isinstance(current, Mapping) and component in current:
            current = current[component]
        elif isinstance(current, list) and component.isdigit() and int(component) < len(current):
            current = current[int(component)]
        else:
            return False, None
    return True, current


def _normalize_array_indexes(path: str) -> str:
    result = ""
    index = 0
    while index < len(path):
        if path[index] != "[":
            result += path[index]
            index += 1
            continue
        end = path.find("]", index + 1)
        token = path[index + 1 : end] if end >= 0 else ""
        if end < 0 or not token.isdigit():
            return path
        result += f".{token}"
        index = end + 1
    return result


def _compare(
    *,
    operator: str,
    actual_exists: bool,
    actual: object,
    expected_exists: bool,
    expected: object,
) -> tuple[bool, str | None]:
    try:
        if operator == "exists":
            passed = actual_exists is bool(expected)
        elif not actual_exists:
            return False, "database readback path was not observed"
        elif not expected_exists:
            return False, "database relationship target path was not observed"
        elif operator in {"equals", "equals_path"}:
            passed = actual == expected
        elif operator in {"not_equals", "not_equals_path"}:
            passed = actual != expected
        elif operator == "contains":
            passed = expected in actual  # type: ignore[operator]
        elif operator == "in":
            passed = actual in cast(list[object], expected)
        elif operator == "greater_than":
            passed = _number(actual) > _number(expected)
        elif operator == "greater_than_or_equal":
            passed = _number(actual) >= _number(expected)
        elif operator == "less_than":
            passed = _number(actual) < _number(expected)
        elif operator == "less_than_or_equal":
            passed = _number(actual) <= _number(expected)
        elif operator == "between":
            bounds = cast(list[object], expected)
            passed = _number(bounds[0]) <= _number(actual) <= _number(bounds[1])
        else:
            return False, f"unsupported data coverage operator: {operator}"
    except (IndexError, TypeError, ValueError):
        return False, "database value is incompatible with the reviewed data condition"
    return passed, None if passed else "database readback did not satisfy the data condition"


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("value is not numeric")
    return float(value)
