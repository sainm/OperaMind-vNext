# ruff: noqa: RUF001

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "src/operamind/web/static"


def test_change_management_builds_japanese_sections() -> None:
    value = _run_module(
        "change-management.js",
        "orchestrationCards",
        {
            "orchestration": {
                "code_scope": [
                    {"recommended_action": "modify", "target_path": "ExpenseService.java"}
                ],
                "ui_scenarios": [
                    {
                        "scenario_id": "expense-filter-returned-option",
                        "expected_results": ["fallback"],
                    }
                ],
            },
            "test_data_plan": {
                "generation_flows": [
                    {
                        "test_data_refs": ["returned-expense"],
                        "steps": [{"sequence": 1, "step_id": "create-returned-expense"}],
                    }
                ]
            },
            "coverage_report": {
                "covered_rule_count": 1,
                "business_rule_count": 1,
                "coverage_percent": 100,
            },
        },
    )

    assert [section["title"] for section in value] == [
        "コード変更範囲",
        "テストデータ生成フロー（画面横断対応）",
        "UI シナリオ",
        "業務カバレッジ",
    ]
    assert value[0]["items"] == ["変更 · ExpenseService.java"]
    assert "差戻し経費を登録" in value[1]["items"][0]
    assert "差戻しを選択" in value[2]["items"][0]


def test_case_editor_reports_missing_ambiguity_selections() -> None:
    value = _run_module(
        "case-editor.js",
        "selectedAmbiguities",
        {
            "ambiguities": [
                {"ambiguity_id": "target", "options": ["一覧", "詳細"]},
                {"ambiguity_id": "status", "options": ["差戻し", "承認済み"]},
            ]
        },
        {"target": "一覧"},
    )

    assert value == {"missing": ["status"], "selected": {"target": "一覧"}}


def test_test_data_module_extracts_cross_screen_variables() -> None:
    value = _run_module(
        "test-data-management.js",
        "inputVariableNames",
        {
            "employee": "{{employee_no}}",
            "expense": {"owner": "{{employee_no}}", "number": "{{expense_no}}"},
        },
    )

    assert value == ["employee_no", "expense_no"]


def test_verification_module_localizes_coverage_blockers() -> None:
    value = _run_module(
        "verification-results.js",
        "closureView",
        {
            "status": "blocked",
            "ui_status": "passed",
            "business_coverage_percent": 100,
            "changed_line_coverage_percent": 72.5,
            "modified_paths": ["ExpenseService.java"],
            "test_results": [],
            "unresolved_items": [
                "Changed-line coverage: 72.5% < 80%",
                "Uncovered changed line: ExpenseService.java:42",
                "Business Coverage Report summary is inconsistent",
                "Change Closure is stale for current Edit Result",
                "Legacy ChangeClosureResult v1 requires changed-line coverage re-evaluation",
            ],
        },
    )

    assert value["changedLineCoveragePercent"] == 72.5
    assert value["blockers"] == [
        {
            "raw": "Changed-line coverage: 72.5% < 80%",
            "label": "変更行カバレッジ 72.5% は基準値 80% 未満です。",
        },
        {
            "raw": "Uncovered changed line: ExpenseService.java:42",
            "label": "未カバーの変更行があります：ExpenseService.java:42",
        },
        {
            "raw": "Business Coverage Report summary is inconsistent",
            "label": "業務カバレッジの集計値と明細が一致していません。",
        },
        {
            "raw": "Change Closure is stale for current Edit Result",
            "label": "現在の変更実行結果に対応する変更完了判定を再生成してください。",
        },
        {
            "raw": "Legacy ChangeClosureResult v1 requires changed-line coverage re-evaluation",
            "label": (
                "旧形式の変更完了判定には変更行カバレッジがないため、再評価が必要です。"
            ),
        },
    ]


def test_coverage_view_localizes_changed_line_blocking_reasons() -> None:
    value = _run_module(
        "verification-results.js",
        "coverageView",
        {"coverage_percent": 100, "covered_rule_count": 1, "business_rule_count": 1},
        {
            "coverage_percent": 50,
            "covered_changed_line_count": 1,
            "changed_line_count": 2,
            "minimum_coverage_percent": 80,
            "status": "failed",
            "files": [],
            "evidence_refs": ["command-1"],
            "blocking_reasons": [
                "Uncovered changed line: ExpenseService.java:42",
                "Coverage evidence does not include changed source file: Missing.java",
            ],
        },
    )

    assert value["changedLines"]["blockingReasons"] == [
        {
            "raw": "Uncovered changed line: ExpenseService.java:42",
            "label": "未カバーの変更行があります：ExpenseService.java:42",
        },
        {
            "raw": "Coverage evidence does not include changed source file: Missing.java",
            "label": "変更されたソースファイルのカバレッジ証跡がありません：Missing.java",
        },
    ]


def test_traceability_module_groups_stages_and_labels_gaps() -> None:
    value = _run_module(
        "traceability-view.js",
        "buildViewModel",
        {
            "nodes": [
                {"id": "code:1", "kind": "影響コード", "title": "ExpenseService.java"},
                {"id": "case:1", "kind": "Test Case", "title": "検索"},
            ],
            "edges": [{"from": "code:1", "to": "case:1", "relation": "検証"}],
            "gaps": [
                {
                    "code": "ui_result",
                    "severity": "critical",
                    "message": "UI 検証結果がありません。",
                }
            ],
            "summary": {
                "stage_order": ["影響コード", "Test Case"],
                "node_count": 2,
                "edge_count": 1,
                "gap_count": 1,
                "critical_gap_count": 1,
            },
        },
    )

    assert [stage["label"] for stage in value["stages"]] == ["影響コード", "テストケース"]
    assert value["gaps"][0]["label"] == "必須工程の欠落"
    assert value["edges"][0]["relation"] == "検証"
    assert value["edges"][0]["from_label"] == "ExpenseService.java"
    assert value["edges"][0]["to_label"] == "検索"


def test_traceability_svg_model_marks_critical_and_blocking_paths() -> None:
    value = _run_module(
        "traceability-view.js",
        "buildGraph",
        {
            "nodes": [
                {"id": "request", "kind": "変更要件", "title": "変更", "status": "confirmed"},
                {"id": "code", "kind": "影響コード", "title": "Service.java", "status": "modified"},
                {"id": "case", "kind": "Test Case", "title": "検索", "status": "blocked"},
                {
                    "id": "closure",
                    "kind": "Closure Result",
                    "title": "完了判定",
                    "status": "blocked",
                },
            ],
            "edges": [
                {"from": "request", "to": "code", "relation": "影響"},
                {"from": "code", "to": "case", "relation": "検証"},
                {"from": "case", "to": "closure", "relation": "判定"},
            ],
            "gaps": [
                {"severity": "critical", "node_id": "case", "message": "結果がありません"}
            ],
            "summary": {
                "stage_order": ["変更要件", "影響コード", "Test Case", "Closure Result"],
                "node_count": 4,
                "edge_count": 3,
                "gap_count": 1,
                "critical_gap_count": 1,
            },
        },
    )

    assert value["criticalPath"] == ["request", "code", "case", "closure"]
    assert value["blockingChain"] == ["case", "closure"]
    assert all(edge["critical"] for edge in value["edges"])
    assert value["edges"][2]["blocking"] is True
    assert value["width"] > 0


def test_code_graph_svg_model_marks_unresolved_blocking_chain() -> None:
    value = _run_module(
        "code-graph.js",
        "buildGraph",
        {
            "nodes": [
                {"id": "file", "kind": "file", "title": "Service.java"},
                {"id": "method", "kind": "symbol", "title": "search()"},
                {"id": "unknown", "kind": "external", "title": "unresolved"},
                {"id": "downstream", "kind": "symbol", "title": "render()"},
            ],
            "edges": [
                {"from": "file", "to": "method", "resolution": "resolved"},
                {"from": "method", "to": "unknown", "resolution": "unresolved"},
                {"from": "unknown", "to": "downstream", "resolution": "resolved"},
            ],
        },
    )

    assert value["criticalPath"] == ["file", "method", "unknown", "downstream"]
    assert value["blockingChain"] == ["unknown", "downstream"]
    assert value["edges"][1]["blocking"] is True
    assert value["edges"][2]["blocking"] is True


def test_shared_graph_canvas_clamps_and_fits_scale() -> None:
    assert _run_module("graph-canvas.js", "clampScale", 99) == 2.5
    assert _run_module("graph-canvas.js", "clampScale", 0.01) == 0.35
    transform = _run_module("graph-canvas.js", "fitTransform", 800, 400, 1200, 600)
    assert transform["scale"] == pytest.approx(0.6133333333)
    assert transform["x"] > 0


def test_shared_japanese_copy_hides_internal_terms() -> None:
    assert _run_module("ui-copy.js", "statusLabel", "submitted") == "結果提出済み・照合待ち"
    assert _run_module("ui-copy.js", "statusLabel", "needs_review") == "レビューが必要"
    assert _run_module("ui-copy.js", "statusLabel", "attention_required") == "確認が必要"
    assert _run_module("ui-copy.js", "term", "approvalGrant") == "実行許可"


def test_profile_registry_localizes_and_marks_requested_rebuilds() -> None:
    value = _run_module(
        "profile-registry.js",
        "buildViewModel",
        {
            "profile_versions": [
                {
                    "profile_version_id": "embedding-v2",
                    "profile_type": "EmbeddingProfile",
                    "semantic_version": "2.0.0",
                }
            ],
            "bindings": [
                {
                    "binding_key": "embedding:documents",
                    "profile_version_id": "embedding-v2",
                    "profile_type": "EmbeddingProfile",
                }
            ],
            "drift_events": [
                {
                    "drift_event_id": "drift-1",
                    "binding_key": "embedding:documents",
                    "previous_profile_version_id": "embedding-v1",
                    "activated_profile_version_id": "embedding-v2",
                    "impacts": [
                        {
                            "affected_layer": "impact",
                            "artifact_type": "ImpactReport",
                            "artifact_id": "impact-1",
                            "effective_status": "blocked",
                            "rebuild_action": "rerun_impact_analysis",
                            "reason": "Embedding Profile changed",
                            "resolved": False,
                        },
                        {
                            "affected_layer": "closure",
                            "artifact_type": "ChangeClosureResult",
                            "artifact_id": "closure-1",
                            "effective_status": "blocked",
                            "rebuild_action": "regenerate_change_closure",
                            "reason": "Embedding Profile changed",
                            "resolved": False,
                        },
                    ],
                }
            ],
            "rebuild_requests": [
                {
                    "rebuild_request_id": "request-1",
                    "drift_event_id": "drift-1",
                    "artifact_type": "ImpactReport",
                    "artifact_id": "impact-1",
                    "status": "requested",
                    "phase_order": 20,
                    "attempt_count": 0,
                    "max_attempts": 3,
                }
            ],
            "rebuild_batches": [
                {
                    "rebuild_batch_id": "batch-1",
                    "status": "requested",
                    "request_count": 2,
                    "completed_count": 0,
                }
            ],
            "open_drift_count": 1,
            "open_impact_count": 2,
        },
    )

    assert value["bindings"][0]["profile_label"] == "埋め込み検索"
    assert value["bindings"][0]["candidates"][0]["profile_version_id"] == "embedding-v2"
    assert value["impacts"][0]["layer_label"] == "影響分析"
    assert value["impacts"][0]["action_label"] == "影響分析を再実行"
    assert value["impacts"][0]["requested"] is True
    assert value["impacts"][1]["action_label"] == "変更完了判定を再生成"
    assert value["rebuildBatches"][0]["status_label"] == "待機中"
    assert value["rebuildRequests"][0]["phase_label"] == "2. 影響分析"
    assert value["rebuildRequests"][0]["retryable"] is False


def test_layout_module_separates_long_page_into_japanese_workspaces() -> None:
    operations = _run_module("layout.js", "activePanelIds", "operations")
    evidence = _run_module("layout.js", "activePanelIds", "evidence")
    tests = _run_module("layout.js", "viewDefinition", "tests")

    assert operations == ["orchestrationTaskManagementPanel", "environmentDiagnosticsPanel"]
    assert evidence == [
        "workbenchOverviewPanel",
        "traceabilityPanel",
        "codeGraphPanel",
        "uiKnowledgePanel",
        "unresolvedEvidencePanel",
        "evidenceReadinessPanel",
    ]
    assert tests["title"] == "テスト"
    assert tests["panels"] == [
        "workbenchOverviewPanel",
        "orchestrationPanel",
        "testDataManagementPanel",
        "failureManagementPanel",
        "closureManagementPanel",
    ]


def _run_module(filename: str, function_name: str, *arguments: object) -> object:
    script = """
const api = require(process.argv[1]);
const functionName = process.argv[2];
const args = JSON.parse(process.argv[3]);
process.stdout.write(JSON.stringify(api[functionName](...args)));
"""
    result = subprocess.run(
        [
            "node",
            "-e",
            script,
            str(STATIC / filename),
            function_name,
            json.dumps(arguments, ensure_ascii=False),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)
