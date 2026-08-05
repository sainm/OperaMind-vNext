from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from operamind.application.test_case_revision import (
    TestCaseChangeAnalyzer as ChangeAnalyzer,
)
from operamind.application.test_case_revision import (
    TestCaseRevisionPlanner as RevisionPlanner,
)
from operamind.application.test_case_revision import (
    build_undo_proposal,
    resolve_ambiguities,
)
from operamind.application.test_case_revision_service import (
    TestCaseRevisionService as RevisionService,
)

ROOT = Path(__file__).parents[2]


def test_deterministic_natural_language_change_regenerates_dependent_artifacts() -> None:
    bundle = _bundle()
    instruction = "\n".join(
        (
            "ケース「経費一覧を確認」のステップ「一覧を開く」を「経費一覧画面を開く」に変更",
            "ケース「経費一覧を確認」の期待結果「4 件を表示する」を「5 件を表示する」に変更",
            "ケース「経費一覧を確認」の業務アサーション「4 件を表示する」を"
            "「5 件を表示する」に変更",
            "ケース「経費一覧を確認」のテストデータ「expense-data」の項目"
            "「expected_count」を「5」に変更",
        )
    )
    proposal = (
        ChangeAnalyzer(repository_root=ROOT)
        .analyze(
            bundle=bundle,
            instruction=instruction,
        )
        .proposal
    )

    assert proposal["analysis_status"] == "deterministic"
    assert [operation["field"] for operation in proposal["operations"]] == [
        "steps",
        "expected_results",
        "business_assertions",
        "test_data_values",
    ]

    planned = RevisionPlanner(repository_root=ROOT).plan(
        source_bundle=bundle,
        proposal=proposal,
        operations=proposal["operations"],
        applied_by="qa-user",
        stale_run_ids=["run-v1"],
        stale_artifact_refs=["test-plan-v1", "test-data-plan-v1"],
        stale_evidence_refs=["evidence-v1"],
        stale_closure_result_ids=["closure-v1"],
    )
    result = planned.orchestration
    case = result.test_plan["test_cases"][0]
    flow = result.test_data_plan["generation_flows"][0]
    data_set = result.test_data_plan["data_sets"][0]

    assert case["steps"] == ["経費一覧画面を開く", "ステータスを確認する"]
    assert case["expected_results"] == ["5 件を表示する"]
    assert result.acceptance_criteria["criteria"][0]["expected"] == "5 件を表示する"
    assert flow["final_assertions"][0]["expected"] == "5 件を表示する"
    assert data_set["setup_actions"][0]["payload"]["expected_count"] == 5
    assert result.coverage_report["coverage_percent"] == 100
    assert result.test_plan["test_plan_id"] != "test-plan-v1"
    assert result.test_data_plan["test_plan_id"] == result.test_plan["test_plan_id"]
    assert (
        result.orchestration["artifact_refs"]["coverage_report_id"]
        == (result.coverage_report["coverage_report_id"])
    )
    assert planned.revision["stale_run_ids"] == ["run-v1"]
    assert planned.revision["stale_evidence_refs"] == ["evidence-v1"]
    assert planned.revision["stale_closure_result_ids"] == ["closure-v1"]


def test_copilot_regeneration_must_apply_every_confirmed_operation() -> None:
    bundle = _bundle()
    proposal = (
        ChangeAnalyzer(repository_root=ROOT)
        .analyze(
            bundle=bundle,
            instruction=(
                "ケース「経費一覧を確認」のステップ「一覧を開く」を"
                "「経費一覧画面を開く」に変更"
            ),
        )
        .proposal
    )
    planner = RevisionPlanner(repository_root=ROOT)

    with pytest.raises(ValueError, match="did not apply confirmed operation"):
        planner.validate_regenerated_operation_effects(
            source_bundle=bundle,
            operations=proposal["operations"],
            test_plan=bundle["test_plan"],
            test_data_plan=bundle["test_data_plan"],
        )

    deterministic = planner.plan(
        source_bundle=bundle,
        proposal=proposal,
        operations=proposal["operations"],
        applied_by="qa-user",
    )
    planner.validate_regenerated_operation_effects(
        source_bundle=bundle,
        operations=proposal["operations"],
        test_plan=deterministic.orchestration.test_plan,
        test_data_plan=deterministic.orchestration.test_data_plan,
    )


def test_ai_regeneration_rejects_an_unexecutable_test_data_plan_before_persistence() -> None:
    bundle = _bundle()
    invalid_plan = copy.deepcopy(bundle["test_data_plan"])
    invalid_plan["generation_flows"][0]["steps"][0]["postconditions"] = []
    service = object.__new__(RevisionService)
    service._identity_provider_types = lambda _project_id: {  # type: ignore[method-assign]
        "database.v1": "database"
    }

    with pytest.raises(ValueError, match="postconditions are required"):
        service.apply_ai_regeneration(
            change_request_id="change-request-1",
            proposal_id="proposal-1",
            source_orchestration_id="orchestration-v1",
            test_plan=copy.deepcopy(bundle["test_plan"]),
            test_data_plan=invalid_plan,
            operations=[{"field": "plan_structure"}],
            selections={},
            actor="codex:fallback",
        )


def test_free_form_structural_instruction_queues_auditable_whole_plan_regeneration() -> None:
    bundle = _bundle()
    instruction = (
        "既存データを前提にせず、HTTP で申請中と差戻しを作成してください。"
        "保存した ID で cleanup を実行し、検索確認は Playwright で行ってください。"
    )

    proposal = ChangeAnalyzer(repository_root=ROOT).analyze(
        bundle=bundle,
        instruction=instruction,
    ).proposal

    assert proposal["analysis_status"] == "deterministic"
    assert proposal["blocking_reasons"] == []
    assert proposal["operations"] == [
        {
            "operation_id": proposal["operations"][0]["operation_id"],
            "test_case_id": "whole-ui-test-plan",
            "case_title": "UI TestPlan / TestDataPlan 全体",
            "field": "plan_structure",
            "action": "regenerate",
            "before": {
                "test_case_count": 1,
                "test_case_titles": ["経費一覧を確認"],
            },
            "after": instruction,
            "summary_before": "現在の計画: 1 Test Case",
            "summary_after": f"自然言語要求: {instruction}",
        }
    ]

    repeated = ChangeAnalyzer(repository_root=ROOT).analyze(
        bundle=bundle,
        instruction=instruction,
    ).proposal
    assert repeated["proposal_id"] == proposal["proposal_id"]


def test_whole_plan_regeneration_rejects_id_only_output_and_accepts_content_change() -> None:
    bundle = _bundle()
    proposal = ChangeAnalyzer(repository_root=ROOT).analyze(
        bundle=bundle,
        instruction="HTTP でテストデータを作成し、終了時に削除してください。",
    ).proposal
    planner = RevisionPlanner(repository_root=ROOT)

    unchanged_plan = copy.deepcopy(bundle["test_plan"])
    unchanged_plan["test_plan_id"] = "test-plan-id-only-change"
    unchanged_data = copy.deepcopy(bundle["test_data_plan"])
    unchanged_data["test_data_plan_id"] = "test-data-plan-id-only-change"
    with pytest.raises(ValueError, match="did not change planning content"):
        planner.validate_regenerated_operation_effects(
            source_bundle=bundle,
            operations=proposal["operations"],
            test_plan=unchanged_plan,
            test_data_plan=unchanged_data,
        )

    changed_data = copy.deepcopy(bundle["test_data_plan"])
    changed_data["generation_flows"][0]["steps"][0]["business_action"] = (
        "HTTP でテストデータを作成する"
    )
    planner.validate_regenerated_operation_effects(
        source_bundle=bundle,
        operations=proposal["operations"],
        test_plan=bundle["test_plan"],
        test_data_plan=changed_data,
    )


def test_expected_result_alone_regenerates_assertion_data_cleanup_and_coverage() -> None:
    bundle = _bundle()
    flow = bundle["test_data_plan"]["generation_flows"][0]
    data_set = bundle["test_data_plan"]["data_sets"][0]
    flow["cleanup_policy"] = "delete_after_run"
    data_set["cleanup_policy"] = "delete_after_run"
    flow["cleanup_steps"] = [
        {
            "step_id": "cleanup-expense-data",
            "sequence": 1,
            "channel": "fixture",
            "business_action": "テストデータを削除する",
            "target": "visiondemo.default-seed",
            "inputs": {},
            "depends_on": ["setup-expense-data"],
            "output_bindings": [],
            "postconditions": [
                {
                    "assertion_id": "cleanup-expense-data-removed",
                    "observe_via": "fixture",
                    "subject": "removed",
                    "operator": "equals",
                    "expected": True,
                }
            ],
        }
    ]
    proposal = (
        ChangeAnalyzer(repository_root=ROOT)
        .analyze(
            bundle=bundle,
            instruction=(
                "ケース「経費一覧を確認」の期待結果「4 件を表示する」を「5 件を表示する」に変更"
            ),
        )
        .proposal
    )

    planned = (
        RevisionPlanner(repository_root=ROOT)
        .plan(
            source_bundle=bundle,
            proposal=proposal,
            operations=proposal["operations"],
            applied_by="qa-user",
        )
        .orchestration
    )

    regenerated_flow = planned.test_data_plan["generation_flows"][0]
    assert regenerated_flow["final_assertions"][0]["expected"] == "5 件を表示する"
    assert regenerated_flow["cleanup_steps"] == flow["cleanup_steps"]
    assert planned.test_data_plan["test_data_plan_id"] != "test-data-plan-v1"
    assert planned.acceptance_criteria["acceptance_criteria_id"] != "acceptance-v1"
    assert planned.coverage_report["coverage_report_id"] != "coverage-v1"
    assert planned.coverage_report["coverage_percent"] == 100


def test_japanese_ordinal_can_modify_a_business_visible_step() -> None:
    bundle = _bundle()
    proposal = (
        ChangeAnalyzer(repository_root=ROOT)
        .analyze(
            bundle=bundle,
            instruction=(
                "ケース「経費一覧を確認」の１番目のステップを「経費一覧画面を開く」に変更"
            ),
        )
        .proposal
    )

    assert proposal["analysis_status"] == "deterministic"
    assert proposal["operations"][0]["index"] == 0
    assert proposal["operations"][0]["before"] == "一覧を開く"
    planned = RevisionPlanner(repository_root=ROOT).plan(
        source_bundle=bundle,
        proposal=proposal,
        operations=proposal["operations"],
        applied_by="qa-user",
    )
    assert planned.orchestration.test_plan["test_cases"][0]["steps"][0] == ("経費一覧画面を開く")


def test_natural_language_can_target_generation_variable_and_cleanup_process() -> None:
    bundle = _bundle()
    flow = bundle["test_data_plan"]["generation_flows"][0]
    flow["steps"][0]["output_bindings"] = [
        {
            "variable": "expense_id",
            "source": "fixture",
            "path": "result.id",
            "required": True,
        }
    ]
    flow["cleanup_steps"] = [
        {
            "step_id": "cleanup-expense",
            "sequence": 1,
            "channel": "fixture",
            "business_action": "作成データを残す",
            "target": "visiondemo.default-seed",
            "inputs": {"expense_id": "${expense_id}"},
            "depends_on": ["setup-expense-data"],
            "output_bindings": [],
            "postconditions": [],
        }
    ]
    instruction = "\n".join(
        (
            "ケース「経費一覧を確認」の生成ステップ「既定データを準備する」を"
            "「差戻し経費を準備する」に変更",
            "ケース「経費一覧を確認」の変数「expense_id」の取得元を"
            "「response.body.id」に変更",
            "ケース「経費一覧を確認」のクリーンアップステップ「作成データを残す」を"
            "「作成した経費を削除する」に変更",
        )
    )

    proposal = ChangeAnalyzer(repository_root=ROOT).analyze(
        bundle=bundle,
        instruction=instruction,
    ).proposal

    assert proposal["analysis_status"] == "deterministic"
    assert [operation["field"] for operation in proposal["operations"]] == [
        "generation_steps",
        "variable_bindings",
        "cleanup_steps",
    ]
    planned = RevisionPlanner(repository_root=ROOT).plan(
        source_bundle=bundle,
        proposal=proposal,
        operations=proposal["operations"],
        applied_by="qa-user",
    ).orchestration
    regenerated_flow = planned.test_data_plan["generation_flows"][0]
    assert regenerated_flow["steps"][0]["business_action"] == "差戻し経費を準備する"
    assert regenerated_flow["steps"][0]["output_bindings"][0]["path"] == (
        "response.body.id"
    )
    assert regenerated_flow["cleanup_steps"][0]["business_action"] == (
        "作成した経費を削除する"
    )


def test_ambiguous_case_target_requires_one_explicit_option() -> None:
    bundle = _bundle(two_cases=True)
    proposal = (
        ChangeAnalyzer(repository_root=ROOT)
        .analyze(
            bundle=bundle,
            instruction="ステップ「一覧を開く」を「対象一覧を開く」に変更",
        )
        .proposal
    )

    assert proposal["analysis_status"] == "needs_confirmation"
    assert proposal["operations"] == []
    ambiguity = proposal["ambiguities"][0]
    assert [option["label"] for option in ambiguity["options"]] == [
        "経費一覧を確認",
        "社員一覧を確認",
    ]

    selected = ambiguity["options"][1]
    operations = resolve_ambiguities(
        proposal,
        {ambiguity["ambiguity_id"]: selected["option_id"]},
    )

    assert len(operations) == 1
    assert operations[0]["case_title"] == "社員一覧を確認"


def test_deterministic_and_ambiguous_changes_are_confirmed_as_one_operation_set() -> None:
    proposal = (
        ChangeAnalyzer(repository_root=ROOT)
        .analyze(
            bundle=_bundle(two_cases=True),
            instruction="\n".join(
                (
                    "ケース「経費一覧を確認」の期待結果「4 件を表示する」を"
                    "「5 件を表示する」に変更",
                    "ステップ「一覧を開く」を「対象一覧を開く」に変更",
                )
            ),
        )
        .proposal
    )

    assert proposal["analysis_status"] == "needs_confirmation"
    assert len(proposal["operations"]) == 1
    ambiguity = proposal["ambiguities"][0]
    selected = ambiguity["options"][1]
    resolved = resolve_ambiguities(
        proposal,
        {ambiguity["ambiguity_id"]: selected["option_id"]},
    )
    assert [operation["test_case_id"] for operation in resolved] == [
        "expense-case",
        "employee-case",
    ]


def test_unknown_test_data_value_is_blocked_without_partial_application() -> None:
    proposal = (
        ChangeAnalyzer(repository_root=ROOT)
        .analyze(
            bundle=_bundle(),
            instruction=(
                "ケース「経費一覧を確認」のステップ「一覧を開く」を"
                "「経費一覧画面を開く」に変更\n"
                "ケース「経費一覧を確認」のテストデータ「expense-data」の項目"
                "「存在しない項目」を「値」に変更"
            ),
        )
        .proposal
    )

    assert proposal["analysis_status"] == "blocked"
    assert proposal["operations"] == []
    assert "存在しない項目" in proposal["blocking_reasons"][0]


def test_chinese_instruction_can_modify_a_business_visible_step() -> None:
    proposal = (
        ChangeAnalyzer(repository_root=ROOT)
        .analyze(
            bundle=_bundle(),
            instruction=("测试Case“経費一覧を確認”将步骤“一覧を開く”修改为“経費一覧画面を開く”"),
        )
        .proposal
    )

    assert proposal["analysis_status"] == "deterministic"
    assert proposal["operations"][0]["after"] == "経費一覧画面を開く"


def test_one_instruction_previews_multiple_cases_steps_data_and_assertions() -> None:
    proposal = (
        ChangeAnalyzer(repository_root=ROOT)
        .analyze(
            bundle=_bundle(two_cases=True),
            instruction="\n".join(
                (
                    "ケース「経費一覧を確認」のステップ「一覧を開く」を"
                    "「経費一覧画面を開く」に変更",
                    "ケース「社員一覧を確認」のステップ「一覧を開く」を"
                    "「社員一覧画面を開く」に変更",
                    "ケース「経費一覧を確認」のテストデータ「expense-data」の項目"
                    "「expected_count」を「5」に変更",
                    "ケース「社員一覧を確認」の業務アサーション「4 件を表示する」を"
                    "「社員 3 件を表示する」に変更",
                )
            ),
        )
        .proposal
    )

    assert proposal["analysis_status"] == "deterministic"
    assert {item["test_case_id"] for item in proposal["operations"]} == {
        "expense-case",
        "employee-case",
    }
    assert {item["field"] for item in proposal["operations"]} == {
        "steps",
        "test_data_values",
        "business_assertions",
    }
    assert len(proposal["operations"]) == 4


def test_undo_planner_restores_prior_content_as_a_new_immutable_version() -> None:
    source = _bundle()
    analyzer = ChangeAnalyzer(repository_root=ROOT)
    planner = RevisionPlanner(repository_root=ROOT)
    proposal = analyzer.analyze(
        bundle=source,
        instruction=(
            "ケース「経費一覧を確認」のステップ「一覧を開く」を「経費一覧画面を開く」に変更"
        ),
    ).proposal
    changed = planner.plan(
        source_bundle=source,
        proposal=proposal,
        operations=proposal["operations"],
        applied_by="qa-user",
    )
    current = {
        "orchestration": changed.orchestration.orchestration,
        "acceptance_criteria": changed.orchestration.acceptance_criteria,
        "test_plan": changed.orchestration.test_plan,
        "test_data_plan": changed.orchestration.test_data_plan,
        "coverage_report": changed.orchestration.coverage_report,
    }
    undo_proposal = build_undo_proposal(
        repository_root=ROOT,
        current_bundle=current,
        revision=changed.revision,
        idempotency_key="undo-1",
    )
    restored = planner.restore(
        source_bundle=current,
        restore_bundle=source,
        proposal=undo_proposal,
        applied_by="qa-user",
    )

    assert undo_proposal["proposal_kind"] == "undo"
    assert restored.revision["revision_kind"] == "undo"
    assert restored.revision["undo_of_revision_id"] == changed.revision["revision_id"]
    assert restored.orchestration.test_plan["test_cases"] == source["test_plan"]["test_cases"]
    assert restored.orchestration.orchestration["orchestration_id"] not in {
        source["orchestration"]["orchestration_id"],
        current["orchestration"]["orchestration_id"],
    }


def _bundle(*, two_cases: bool = False) -> dict[str, Any]:
    cases = [_case("expense-case", "経費一覧を確認", "expense-data")]
    criteria = [_criterion("expense-criterion", "expense-case")]
    data_sets = [_data_set("expense-data", "expense-case", 4)]
    flows = [_flow("expense-flow", "expense-data", "expense-case")]
    coverage_items = [
        {
            "business_rule_id": "rule-list",
            "test_case_refs": ["expense-case"],
            "criterion_refs": ["expense-criterion"],
            "status": "covered",
        }
    ]
    if two_cases:
        cases.append(_case("employee-case", "社員一覧を確認", "employee-data"))
        criteria.append(_criterion("employee-criterion", "employee-case"))
        data_sets.append(_data_set("employee-data", "employee-case", 3))
        flows.append(_flow("employee-flow", "employee-data", "employee-case"))
        coverage_items[0]["test_case_refs"].append("employee-case")
        coverage_items[0]["criterion_refs"].append("employee-criterion")
    return {
        "orchestration": {
            "artifact_type": "ChangeOrchestrationPlan",
            "schema_version": "v1",
            "orchestration_id": "orchestration-v1",
            "change_request_id": "change-request-1",
            "project_id": "visiondemo",
            "analysis_case_id": "analysis-case-1",
            "status": "ready",
            "structured_change_refs": ["change-1"],
            "impact_report_id": "impact-1",
            "reviewed_case_id": "golden-case-1",
            "reviewed_case_digest": "a" * 64,
            "repository_revision": "revision-1",
            "code_scope": [
                {
                    "impact_item_id": "impact-item-1",
                    "target_path": "src/ExpenseService.java",
                    "target_symbols": ["ExpenseService.search"],
                    "recommended_action": "modify",
                    "test_file_refs": ["tests/ExpenseServiceTest.java"],
                }
            ],
            "artifact_refs": {
                "acceptance_criteria_id": "acceptance-v1",
                "test_plan_id": "test-plan-v1",
                "test_data_plan_id": "test-data-plan-v1",
                "coverage_report_id": "coverage-v1",
            },
            "ui_scenarios": [],
            "blocking_reasons": [],
        },
        "acceptance_criteria": {
            "artifact_type": "AcceptanceCriteria",
            "schema_version": "v1",
            "acceptance_criteria_id": "acceptance-v1",
            "change_request_id": "change-request-1",
            "project_id": "visiondemo",
            "criteria": criteria,
        },
        "test_plan": {
            "artifact_type": "TestPlan",
            "schema_version": "v1",
            "test_plan_id": "test-plan-v1",
            "change_request_id": "change-request-1",
            "project_id": "visiondemo",
            "status": "ready",
            "test_cases": cases,
            "blocking_reasons": [],
        },
        "test_data_plan": {
            "artifact_type": "TestDataPlan",
            "schema_version": "v1",
            "test_data_plan_id": "test-data-plan-v1",
            "test_plan_id": "test-plan-v1",
            "project_id": "visiondemo",
            "status": "ready",
            "data_sets": data_sets,
            "generation_flows": flows,
            "blocking_reasons": [],
        },
        "coverage_report": {
            "artifact_type": "BusinessCoverageReport",
            "schema_version": "v1",
            "coverage_report_id": "coverage-v1",
            "change_request_id": "change-request-1",
            "test_plan_id": "test-plan-v1",
            "acceptance_criteria_id": "acceptance-v1",
            "project_id": "visiondemo",
            "business_rule_count": 1,
            "covered_rule_count": 1,
            "coverage_percent": 100,
            "items": coverage_items,
            "status": "passed",
        },
    }


def _case(case_id: str, title: str, data_id: str) -> dict[str, object]:
    criterion_id = case_id.replace("case", "criterion")
    return {
        "test_case_id": case_id,
        "title": title,
        "level": "ui",
        "execution_mode": "browser",
        "business_rule_refs": ["rule-list"],
        "acceptance_criteria_refs": [criterion_id],
        "preconditions": ["ログイン済み"],
        "steps": ["一覧を開く", "ステータスを確認する"],
        "expected_results": ["4 件を表示する"],
        "test_data_refs": [data_id],
    }


def _criterion(criterion_id: str, case_id: str) -> dict[str, object]:
    return {
        "criterion_id": criterion_id,
        "business_rule_refs": ["rule-list"],
        "assertion_type": "ui",
        "subject": "一覧件数",
        "operator": "equals",
        "expected": "4 件を表示する",
        "test_case_refs": [case_id],
    }


def _data_set(data_id: str, case_id: str, expected_count: int) -> dict[str, object]:
    return {
        "test_data_id": data_id,
        "test_case_refs": [case_id],
        "setup_actions": [
            {
                "action_id": f"setup-{data_id}",
                "action_type": "fixture",
                "target": "visiondemo.default-seed",
                "payload": {"expected_count": expected_count},
            }
        ],
        "cleanup_policy": "isolated_environment",
    }


def _flow(flow_id: str, data_id: str, case_id: str) -> dict[str, object]:
    return {
        "flow_id": flow_id,
        "title": f"{data_id} を生成",
        "test_data_refs": [data_id],
        "test_case_refs": [case_id],
        "steps": [
            {
                "step_id": f"setup-{data_id}",
                "sequence": 1,
                "channel": "fixture",
                "business_action": "既定データを準備する",
                "target": "visiondemo.default-seed",
                "inputs": {},
                "depends_on": [],
                "output_bindings": [],
                "postconditions": [
                    {
                        "assertion_id": f"setup-{data_id}-ready",
                        "observe_via": "fixture",
                        "subject": "ready",
                        "operator": "equals",
                        "expected": True,
                    }
                ],
            }
        ],
        "final_assertions": [
            {
                "assertion_id": f"{flow_id}-result",
                "observe_via": "test",
                "subject": case_id,
                "operator": "satisfies",
                "expected": "4 件を表示する",
            }
        ],
        "cleanup_policy": "isolated_environment",
        "cleanup_steps": [],
    }
