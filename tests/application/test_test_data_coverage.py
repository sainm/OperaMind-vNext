from __future__ import annotations

from copy import deepcopy

import pytest

from operamind.application.test_data_coverage import (
    conditions_for_step,
    evaluate_condition,
    summarize_data_coverage,
    validate_test_data_coverage_alignment,
)


def test_alignment_requires_every_criterion_case_and_data_tuple() -> None:
    test_plan, data_plan, acceptance = _artifacts()
    assert validate_test_data_coverage_alignment(
        test_plan=test_plan,
        test_data_plan=data_plan,
        acceptance_criteria=acceptance,
    ) == []

    missing = deepcopy(data_plan)
    missing["data_sets"][0]["coverage_conditions"] = []
    assert validate_test_data_coverage_alignment(
        test_plan=test_plan,
        test_data_plan=missing,
        acceptance_criteria=acceptance,
    ) == [
        "Test data coverage conditions are missing for criterion/case/data tuples: "
        "[('criterion-returned', 'case-returned', 'expense-returned')]"
    ]


def test_alignment_rejects_identity_only_and_ai_invented_scope() -> None:
    test_plan, data_plan, acceptance = _artifacts()
    condition = data_plan["data_sets"][0]["coverage_conditions"][0]
    condition["path"] = "rows[0].expense_number"
    condition["criterion_ref"] = "invented-criterion"

    reasons = validate_test_data_coverage_alignment(
        test_plan=test_plan,
        test_data_plan=data_plan,
        acceptance_criteria=acceptance,
    )

    assert any("not bound to a TestPlan" in value for value in reasons)
    assert any("identity keys alone cannot prove" in value for value in reasons)
    assert any("conditions are missing" in value for value in reasons)


@pytest.mark.parametrize(
    ("kind", "path", "operator", "expected", "expected_path", "actual", "passed"),
    [
        ("status", "rows[0].status", "equals", "RETURNED", None, "RETURNED", True),
        ("field", "rows[0].title", "contains", "旅費", None, "東京旅費", True),
        ("boundary", "rows[0].amount", "between", [100, 200], None, 150, True),
        (
            "relationship",
            "rows[0].employee_id",
            "equals_path",
            None,
            "rows[0].owner_id",
            41,
            True,
        ),
        ("boundary", "rows[0].amount", "less_than", 100, None, 150, False),
    ],
)
def test_real_readback_condition_operators_are_computed_not_declared(
    kind: str,
    path: str,
    operator: str,
    expected: object,
    expected_path: str | None,
    actual: object,
    passed: bool,
) -> None:
    condition: dict[str, object] = {
        "condition_id": "condition-1",
        "criterion_ref": "criterion-returned",
        "test_case_ref": "case-returned",
        "test_data_id": "expense-returned",
        "condition_kind": kind,
        "source_flow_id": "expense-flow",
        "source_step_id": "read-expense",
        "path": path,
        "operator": operator,
    }
    if expected_path is None:
        condition["expected"] = expected
    else:
        condition["expected_path"] = expected_path
    row = {
        "status": actual,
        "title": actual,
        "amount": actual,
        "employee_id": actual,
        "owner_id": 41,
    }

    proof = evaluate_condition(condition, database={"rows": [row], "row_count": 1})

    assert proof["actual"] == actual
    assert proof["status"] == ("passed" if passed else "failed")
    assert (proof["failure_reason"] is None) is passed


def test_coverage_percentage_is_derived_from_all_condition_proofs() -> None:
    _test_plan, data_plan, _acceptance = _artifacts()
    condition = data_plan["data_sets"][0]["coverage_conditions"][0]
    proof = {
        "condition_id": condition["condition_id"],
        "criterion_ref": condition["criterion_ref"],
        "status": "passed",
    }

    passed = summarize_data_coverage(plan=data_plan, proofs=[proof])
    failed = summarize_data_coverage(plan=data_plan, proofs=[])

    assert passed["status"] == "passed"
    assert passed["coverage_percent"] == 100
    assert failed["status"] == "failed"
    assert failed["coverage_percent"] == 0


@pytest.mark.parametrize(
    ("provider_type", "explicit_source", "observation_source"),
    [
        ("api", None, "response"),
        ("ui", None, "ui"),
        ("hybrid", "api", "api"),
    ],
)
def test_provider_coverage_uses_the_reviewed_observation_source(
    provider_type: str,
    explicit_source: str | None,
    observation_source: str,
) -> None:
    condition = {
        "condition_id": "condition-1",
        "criterion_ref": "criterion-1",
        "test_case_ref": "case-1",
        "test_data_id": "data-1",
        "condition_kind": "status",
        "source_flow_id": "flow-1",
        "source_step_id": "observe-1",
        "path": "status",
        "operator": "equals",
        "expected": "READY",
        **({"source": explicit_source} if explicit_source else {}),
    }
    plan = {
        "data_sets": [
            {
                "test_data_id": "data-1",
                "identity_binding": {"provider": {"type": provider_type}},
                "coverage_conditions": [condition],
            }
        ]
    }

    resolved = conditions_for_step(plan, flow_id="flow-1", step_id="observe-1")[0]
    proof = evaluate_condition(resolved, observation={"status": "READY"})

    assert resolved["_observation_source"] == observation_source
    assert proof["observation_source"] == observation_source
    assert proof["status"] == "passed"


def _artifacts() -> tuple[dict, dict, dict]:
    test_plan = {
        "test_cases": [
            {
                "test_case_id": "case-returned",
                "acceptance_criteria_refs": ["criterion-returned"],
                "test_data_refs": ["expense-returned"],
            }
        ]
    }
    data_plan = {
        "data_sets": [
            {
                "test_data_id": "expense-returned",
                "test_case_refs": ["case-returned"],
                "identity_binding": {
                    "source_flow_id": "expense-flow",
                    "source_step_id": "read-expense",
                    "primary_key": {"path": "rows[0].id"},
                    "business_unique_keys": [
                        {"path": "rows[0].expense_number"}
                    ],
                    "screen_key": {"path": "rows[0].expense_number"},
                },
                "coverage_conditions": [
                    {
                        "condition_id": "returned-status-condition",
                        "criterion_ref": "criterion-returned",
                        "test_case_ref": "case-returned",
                        "test_data_id": "expense-returned",
                        "condition_kind": "status",
                        "source_flow_id": "expense-flow",
                        "source_step_id": "read-expense",
                        "path": "rows[0].status",
                        "operator": "equals",
                        "expected": "RETURNED",
                    }
                ],
            }
        ]
    }
    acceptance = {
        "criteria": [
            {
                "criterion_id": "criterion-returned",
                "test_case_refs": ["case-returned"],
            }
        ]
    }
    return test_plan, data_plan, acceptance
