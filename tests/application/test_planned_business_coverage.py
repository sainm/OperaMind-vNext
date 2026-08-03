from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from operamind.application.planned_business_coverage import (
    assess_planned_business_coverage,
)

ROOT = Path(__file__).parents[2]


def _assess(
    *,
    request: dict[str, Any],
    test_plan: dict[str, Any],
    test_data_plan: dict[str, Any],
    scoped_test_files: frozenset[str] = frozenset(),
    passed_command_refs: frozenset[str] = frozenset(),
    canonical_artifact_refs: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    return assess_planned_business_coverage(
        request=request,
        test_plan=test_plan,
        test_data_plan=test_data_plan,
        scoped_test_files=scoped_test_files,
        passed_command_refs=passed_command_refs,
        canonical_artifact_refs=canonical_artifact_refs,
        required_ui_scenario_refs=tuple(
            next(
                (
                    str(case["test_case_id"])
                    for case in test_plan["test_cases"]
                    if str(rule["business_rule_id"]) in case["business_rule_refs"]
                ),
                f"required-{rule['business_rule_id']}",
            )
            for rule in request["business_rules"]
        ),
    )


def test_code_test_evidence_does_not_replace_executable_ui_requirement_coverage() -> None:
    request, test_plan, test_data_plan = _artifacts()
    request["business_rules"].append(
        {"business_rule_id": "rule-code", "text": "検索条件を正規化する"}
    )
    test_plan["requirement_evidence"] = [
        {
            "business_rule_id": "rule-code",
            "verification_kind": "code_test",
            "assertion": "空の検索条件がサービス層で正規化される",
            "test_file_refs": ["tests/ExpenseServiceTest.java"],
            "command_refs": ["unit-test"],
            "artifact_refs": [],
            "plan_component_refs": [],
        }
    ]

    result = _assess(
        request=request,
        test_plan=test_plan,
        test_data_plan=test_data_plan,
        scoped_test_files=frozenset({"tests/ExpenseServiceTest.java"}),
        passed_command_refs=frozenset({"unit-test"}),
        canonical_artifact_refs=frozenset(),
    )

    assert result["status"] == "failed"
    assert result["coverage_percent"] == 50
    assert result["covered_rule_count"] == 1
    assert result["items"][1]["verification_sources"][0]["source_kind"] == "code_test"


def test_ui_business_rule_is_uncovered_without_complete_playwright_step_mapping() -> None:
    request, test_plan, test_data_plan = _artifacts()
    test_data_plan["generation_flows"][0]["steps"][0]["test_step_refs"] = []

    result = _assess(
        request=request,
        test_plan=test_plan,
        test_data_plan=test_data_plan,
        scoped_test_files=frozenset(),
        passed_command_refs=frozenset(),
        canonical_artifact_refs=frozenset(),
    )

    assert result["status"] == "failed"
    assert result["coverage_percent"] == 0
    assert result["items"][0]["verification_sources"] == []


def test_unapproved_test_file_cannot_be_used_to_fabricate_coverage() -> None:
    request, test_plan, test_data_plan = _artifacts()
    test_plan["requirement_evidence"] = [
        {
            "business_rule_id": "rule-ui",
            "verification_kind": "code_test",
            "assertion": "偽のテスト",
            "test_file_refs": ["tests/NotApprovedTest.java"],
            "command_refs": ["unit-test"],
            "artifact_refs": [],
            "plan_component_refs": [],
        }
    ]

    with pytest.raises(ValueError, match="not backed by allowed current-scope sources"):
        _assess(
            request=request,
            test_plan=test_plan,
            test_data_plan=test_data_plan,
            scoped_test_files=frozenset({"tests/ExpenseServiceTest.java"}),
            passed_command_refs=frozenset({"unit-test"}),
            canonical_artifact_refs=frozenset(),
        )


def test_current_scope_canonical_evidence_is_supplemental_not_coverage() -> None:
    request, test_plan, test_data_plan = _artifacts()
    test_data_plan["generation_flows"][0]["steps"][0]["test_step_refs"] = []
    test_plan["requirement_evidence"] = [
        {
            "business_rule_id": "rule-ui",
            "verification_kind": "canonical_evidence",
            "assertion": "設計変更に要件が記録されている",
            "test_file_refs": [],
            "command_refs": [],
            "artifact_refs": ["document-change-1"],
            "plan_component_refs": [],
        }
    ]

    result = _assess(
        request=request,
        test_plan=test_plan,
        test_data_plan=test_data_plan,
        scoped_test_files=frozenset(),
        passed_command_refs=frozenset(),
        canonical_artifact_refs=frozenset({"document-change-1"}),
    )

    assert result["status"] == "failed"
    assert result["coverage_percent"] == 0
    assert result["items"][0]["verification_sources"][0]["source_kind"] == ("canonical_evidence")


def test_assertion_for_one_case_does_not_cover_another_case_in_same_flow() -> None:
    request, test_plan, test_data_plan = _artifacts()
    request["business_rules"].append(
        {"business_rule_id": "rule-other", "text": "承認済みを検索する"}
    )
    test_plan["test_cases"].append(
        {
            "test_case_id": "case-other",
            "level": "ui",
            "execution_mode": "browser",
            "business_rule_refs": ["rule-other"],
            "acceptance_criteria_refs": ["criterion-other"],
            "step_ids": ["open-other"],
            "expected_results": ["承認済みだけが表示される"],
            "test_data_refs": ["data-ui"],
        }
    )
    flow = test_data_plan["generation_flows"][0]
    flow["test_case_refs"].append("case-other")
    flow["steps"].append(
        {
            "channel": "ui",
            "test_step_refs": ["open-other"],
            "playwright": {
                "action": "goto",
                "mask_locators": [],
                "observations": [],
            },
            "postconditions": [],
        }
    )

    result = _assess(
        request=request,
        test_plan=test_plan,
        test_data_plan=test_data_plan,
        scoped_test_files=frozenset(),
        passed_command_refs=frozenset(),
        canonical_artifact_refs=frozenset(),
    )

    assert result["status"] == "failed"
    assert result["coverage_percent"] == 50
    assert result["items"][1]["status"] == "uncovered"


def test_executable_case_cannot_claim_a_rule_bound_to_another_required_scenario() -> None:
    request, test_plan, test_data_plan = _artifacts()
    request["business_rules"].append(
        {"business_rule_id": "rule-other", "text": "承認済みを検索する"}
    )
    copied = deepcopy(test_plan["test_cases"][0])
    copied["test_case_id"] = "case-other"
    copied["step_ids"] = ["open-other"]
    copied["business_rule_refs"] = ["rule-ui"]
    test_plan["test_cases"].append(copied)
    flow = test_data_plan["generation_flows"][0]
    flow["test_case_refs"].append("case-other")
    flow["steps"].append(
        {
            "channel": "ui",
            "test_step_refs": ["open-other"],
            "playwright": {
                "action": "goto",
                "mask_locators": [],
                "observations": [{"key": "other_rows", "kind": "count"}],
            },
            "postconditions": [
                {
                    "assertion_id": "other-rows-visible",
                    "observe_via": "ui",
                    "subject": "other_rows",
                    "operator": "count_equals",
                    "expected": 1,
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="Impact-bound business rule"):
        assess_planned_business_coverage(
            request=request,
            test_plan=test_plan,
            test_data_plan=test_data_plan,
            scoped_test_files=frozenset(),
            passed_command_refs=frozenset(),
            canonical_artifact_refs=frozenset(),
            required_ui_scenario_refs=("case-ui", "case-other"),
        )


def test_invented_canonical_evidence_cannot_fabricate_coverage() -> None:
    request, test_plan, test_data_plan = _artifacts()
    test_data_plan["generation_flows"][0]["steps"][0]["test_step_refs"] = []
    test_plan["requirement_evidence"] = [
        {
            "business_rule_id": "rule-ui",
            "verification_kind": "canonical_evidence",
            "assertion": "存在しない設計変更を参照する",
            "test_file_refs": [],
            "command_refs": [],
            "artifact_refs": ["invented-document-change"],
            "plan_component_refs": [],
        }
    ]

    with pytest.raises(ValueError, match="not backed by allowed current-scope sources"):
        _assess(
            request=request,
            test_plan=test_plan,
            test_data_plan=test_data_plan,
            scoped_test_files=frozenset(),
            passed_command_refs=frozenset(),
            canonical_artifact_refs=frozenset({"document-change-1"}),
        )


def _artifacts() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    request = {"business_rules": [{"business_rule_id": "rule-ui", "text": "差戻しを検索する"}]}
    test_plan = {
        "test_cases": [
            {
                "test_case_id": "case-ui",
                "level": "ui",
                "execution_mode": "browser",
                "business_rule_refs": ["rule-ui"],
                "acceptance_criteria_refs": ["criterion-ui"],
                "step_ids": ["open-list"],
                "expected_results": ["差戻しだけが表示される"],
                "test_data_refs": ["data-ui"],
            }
        ],
        "requirement_evidence": [],
    }
    test_data_plan = {
        "data_sets": [{"test_data_id": "data-ui"}],
        "generation_flows": [
            {
                "test_case_refs": ["case-ui"],
                "test_data_refs": ["data-ui"],
                "steps": [
                    {
                        "channel": "ui",
                        "test_step_refs": ["open-list"],
                        "playwright": {
                            "action": "goto",
                            "mask_locators": [],
                            "observations": [{"key": "rows", "kind": "count"}],
                        },
                        "postconditions": [
                            {
                                "assertion_id": "rows-visible",
                                "observe_via": "ui",
                                "subject": "rows",
                                "operator": "count_equals",
                                "expected": 1,
                            }
                        ],
                    }
                ],
                "cleanup_steps": [],
            }
        ],
    }
    return deepcopy(request), deepcopy(test_plan), deepcopy(test_data_plan)
