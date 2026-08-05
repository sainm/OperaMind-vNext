from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]
FLOW_SCRIPT = ROOT / "src/operamind/web/static/change-flow-view.js"
APP_SCRIPT = ROOT / "src/operamind/web/static/app.js"
INDEX_HTML = ROOT / "src/operamind/web/static/index.html"
VSCODE_LINK_SCRIPT = ROOT / "src/operamind/web/static/vscode-link.js"


def test_browser_reads_flow_without_triggering_internal_progress() -> None:
    source = APP_SCRIPT.read_text(encoding="utf-8")

    assert "/flow/progress" not in source
    assert "${encodeURIComponent(state.requestId)}/flow`" in source
    assert "/test-case-revisions`" in source
    assert "/confirm`" in source
    assert "renderTestCaseRevisionProposal" in source


def test_project_form_allows_test_url_to_be_configured_later() -> None:
    source = APP_SCRIPT.read_text(encoding="utf-8")
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert "test_base_url: testBaseUrl || null" in source
    test_url_input = html.split('id="projectTestBaseUrl"', maxsplit=1)[1].split(">", 1)[0]
    assert "required" not in test_url_input


def test_project_target_database_dialect_is_explicit_and_fail_closed() -> None:
    source = APP_SCRIPT.read_text(encoding="utf-8")
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert 'id="projectTargetDataDialect"' in html
    assert '<option value="postgresql">PostgreSQL</option>' in html
    assert "未登録方言へは fallback しません" in html
    assert "dialect: targetDataDialect" in source
    assert 'targetData.dialect || "postgresql"' in source


def test_web_opens_selected_workspace_in_vscode_without_bridge_credentials() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    source = APP_SCRIPT.read_text(encoding="utf-8")

    assert 'id="openVsCodeButton"' in html
    assert 'src="/vscode-link.js' in html
    assert "vscodeLink.buildOpenUrl(project.workspace_root, state.requestId)" in source
    assert "Bridge Token" not in VSCODE_LINK_SCRIPT.read_text(encoding="utf-8")
    assert _run_module(
        VSCODE_LINK_SCRIPT,
        "buildOpenUrl",
        r"C:\work\expense-system",
        "change-42",
    ) == (
        "vscode://operamind-local.operamind-copilot-bridge/open?"
        "workspace=C%3A%5Cwork%5Cexpense-system&request=change-42"
    )


def test_project_onboarding_summary_exposes_canonical_and_rag_counts() -> None:
    source = APP_SCRIPT.read_text(encoding="utf-8")

    assert "onboarding.document_count" in source
    assert "onboarding.generated_vector_count" in source
    assert "RAG Vector" in source


def test_project_document_learning_is_visible_and_confirmation_is_gated() -> None:
    source = APP_SCRIPT.read_text(encoding="utf-8")
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert 'id="documentLearningButton"' in html
    assert 'id="documentLearningDialog"' in html
    assert "設計書学習" in html
    assert "profile.variants" in source
    assert "variant.field_aliases" in source
    assert "variant.stable_key_fields" in source
    assert 'learning.status === "draft_ready" && coverage === 100 && ambiguities === 0' in source
    assert 'requestProjectOnboarding("relearn")' in source


def test_stage_rail_renders_only_the_six_product_stages() -> None:
    stages = [
        _stage("requirement", "変更要件", "completed"),
        _stage("document_change", "設計書差分", "completed"),
        _stage("code_scope", "コード影響範囲", "running"),
        _stage("compile_test", "コード変更・コンパイル・テスト", "waiting"),
        _stage("ui_validation", "テストデータ・UI 検証", "waiting"),
        _stage("final_report", "最終レポート", "waiting"),
        _stage("approval_grant", "内部承認", "running"),
    ]

    html = _run("stageRail", stages, "code_scope")

    assert html.count('class="stage-step') == 6
    assert "VS Code GitHub Copilot" not in html
    assert "承認" not in html
    assert "approval_grant" not in html
    assert "キュー" not in html
    assert 'data-stage-target="code_scope"' in html
    assert "current" in html


def test_stage_details_show_copilot_deliverables_and_hide_internal_control_plane() -> None:
    stages = [
        {
            **_stage("compile_test", "コード変更・コンパイル・テスト", "running"),
            "executor": "vscode_github_copilot",
            "summary": "コードとテスト計画を更新します。",
            "details": {
                "copilot_task_id": "copilot-001",
                "copilot_task_state": "in_progress",
                "approval_grant_id": "grant-001",
                "scheduler_retry_count": 3,
                "lease_owner": "worker-001",
            },
        }
    ]

    html = _run("stageDetails", stages)

    assert "VS Code GitHub Copilot" in html
    assert "コードとテスト計画を更新します。" in html
    assert "copilot-001" not in html
    assert "copilot_task_id" not in html
    assert "approval_grant" not in html
    assert "grant-001" not in html
    assert "scheduler_retry_count" not in html
    assert "worker-001" not in html
    assert "Lease" not in html


def test_stage_details_label_codex_fallback_executor_and_ai_source() -> None:
    stages = [
        {
            **_stage("compile_test", "コード変更・コンパイル・テスト", "completed"),
            "executor": "codex_fallback",
            "summary": "Codex fallback が検証しました。",
            "details": {"ai_source": "Codex fallback"},
        }
    ]

    html = _run("stageDetails", stages, "compile_test")

    assert "Codex fallback" in html
    assert "AI 実行元" in html
    assert "VS Code GitHub Copilot" not in html


def test_stage_details_ignore_unknown_internal_stages() -> None:
    stages = [
        _stage("requirement", "変更要件", "completed"),
        {
            **_stage("worker_lease", "内部 Worker Lease", "running"),
            "details": {"requirement_text": "表示してはいけない内部情報"},
        },
    ]

    html = _run("stageDetails", stages)

    assert "変更要件" in html
    assert "Worker" not in html
    assert "表示してはいけない内部情報" not in html


def test_next_action_renders_one_shared_human_confirmation() -> None:
    stages = [
        {
            **_stage("requirement", "変更要件", "waiting"),
            "details": {
                "requirement_text": "検索条件を変更する",
                "confirmation": {
                    "checkpoint": "requirement",
                    "stage_label": "変更要件の確認",
                    "message": "変更要件を確認してください。",
                    "subject_digest": "a" * 64,
                },
            },
        }
    ]

    html = _run(
        "nextAction",
        {"current_stage": "requirement", "stages": stages},
    )

    assert "変更要件の確認" in html
    assert "確認して進む" in html
    assert "差し戻す" in html
    assert 'data-confirm-checkpoint="requirement"' in html


def test_failed_ui_stage_renders_explicit_same_plan_rerun_action() -> None:
    stages = [
        {
            **_stage("ui_validation", "テストデータ・UI 検証", "blocked"),
            "details": {
                "execution_actions": {
                    "can_rerun": True,
                    "rerun_run_id": "run-failed-001",
                }
            },
        }
    ]

    html = _run("stageDetails", stages, "ui_validation")

    assert "同じ計画で再実行" in html
    assert 'data-rerun-test-data="run-failed-001"' in html


def test_ui_stage_renders_data_identity_provider_and_uniform_binding_fields() -> None:
    stages = [
        {
            **_stage("ui_validation", "テストデータ・UI 検証", "completed"),
            "details": {
                "data_bindings": [
                    {
                        "test_data_id": "expense-data",
                        "binding_mode": "generated",
                        "identity_provider_type": "hybrid",
                        "identity_provider_ref": "hybrid.v1",
                        "primary_key": {"name": "id", "value": 41},
                        "business_unique_keys": [
                            {"name": "expense_number", "value": "EXP-041"}
                        ],
                        "screen_identity_values": [
                            {"name": "expense_number", "value": "EXP-041"}
                        ],
                        "record_scope_locator": {
                            "by": "css",
                            "value": "[data-number='EXP-041']",
                            "exact": True,
                        },
                        "match_count": 1,
                        "content_digest": "a" * 64,
                        "evidence_ref": "artifact://result/binding-1",
                    }
                ]
            },
        }
    ]

    html = _run("stageDetails", stages, "ui_validation")

    assert "hybrid" in html
    assert "画面識別値" in html
    assert "主キー" not in html
    assert "Locator" not in html
    assert "hybrid.v1" not in html
    assert "レコード範囲 Locator" not in html
    assert "EXP-041" in html


def test_stage_details_expand_only_current_stage_and_hide_future_evidence() -> None:
    stages = [
        _stage("requirement", "変更要件", "completed"),
        _stage("document_change", "設計書差分", "waiting"),
        {
            **_stage("compile_test", "コード変更・コンパイル・テスト", "waiting"),
            "details": {"copilot_task_state": "cancelled"},
        },
    ]

    html = _run("stageDetails", stages, "document_change")

    assert 'id="stage-document_change"' in html
    assert 'id="stage-document_change" class="stage-card waiting " open' in html
    assert "前の工程が完了すると" in html
    assert "cancelled" not in html


def test_requirement_title_uses_business_text_instead_of_internal_id() -> None:
    title = _run(
        "requirementTitle",
        "経費申請一覧のステータス検索を変更する。既存動作は維持する。",
    )

    assert title == "経費申請一覧のステータス検索を変更する"


def test_stage_details_render_document_scope_and_command_results_as_business_content() -> None:
    stages = [
        {
            **_stage("document_change", "設計書差分", "completed"),
            "details": {
                "changes": [
                    {
                        "summary": "状態説明を更新",
                        "domain": "expense",
                        "fact_type": "screen_field",
                        "change_type": "modified",
                        "field_deltas": [{"field": "label", "before": "承認済", "after": "差戻し"}],
                    }
                ]
            },
        },
        {
            **_stage("code_scope", "コード影響範囲", "completed"),
            "details": {
                "items": [
                    {
                        "target_path": "src/ExpenseService.java",
                        "target_symbols": ["search"],
                        "recommended_action": "modify",
                        "test_file_refs": ["test/ExpenseServiceTest.java"],
                        "rationale": "状態条件を追加するため",
                    }
                ]
            },
        },
        {
            **_stage("compile_test", "コード変更・コンパイル・テスト", "completed"),
            "details": {
                "commands": [{"command_ref": "targeted-unit", "status": "passed", "exit_code": 0}]
            },
        },
    ]

    html = _run("stageDetails", stages)

    assert "承認済" in html
    assert "差戻し" in html
    assert "src/ExpenseService.java" in html
    assert "search" in html
    assert "test/ExpenseServiceTest.java" in html
    assert "targeted-unit" in html
    assert "exit 0" in html
    assert ">項目<" not in html


def test_stage_details_render_clickable_code_graph_and_inline_test_case_editor() -> None:
    stages = [
        {
            **_stage("code_scope", "コード影響範囲", "completed"),
            "details": {
                "impact_graph": {
                    "nodes": [
                        {
                            "path": "src/ExpenseService.java",
                            "role": "production",
                            "language": "java",
                            "directly_impacted": True,
                            "recommended_action": "modify",
                            "rationale": "状態条件を追加するため",
                            "symbols": ["search"],
                            "related_tests": ["test/ExpenseServiceTest.java"],
                        },
                        {
                            "path": "test/ExpenseServiceTest.java",
                            "role": "test",
                            "language": "java",
                            "directly_impacted": False,
                            "symbols": ["searchReturned"],
                            "related_tests": [],
                        },
                    ],
                    "edges": [
                        {
                            "from_path": "src/ExpenseService.java",
                            "to_path": "test/ExpenseServiceTest.java",
                            "relation": "related_test",
                            "evidence_source": "impact_report",
                        }
                    ],
                    "visible_file_count": 2,
                    "relation_count": 1,
                    "total_file_count": 2,
                    "truncated": False,
                }
            },
        },
        {
            **_stage("compile_test", "コード変更・コンパイル・テスト", "completed"),
            "details": {
                "test_cases": [
                    {
                        "title": "差戻し状態で検索する",
                        "steps": ["一覧を開く", "差戻しを選択して検索する"],
                        "expected_results": ["対象申請が表示される"],
                    }
                ]
            },
        },
    ]

    html = _run("stageDetails", stages)

    assert "Code Graph に基づく影響関係" in html
    assert "<svg" in html
    assert 'data-impact-node-index="0"' in html
    assert "状態条件を追加するため" in html
    assert "search" in html
    assert "関連テスト" in html
    assert 'data-open-test-case-revision' in html
    assert "自然言語で修正" in html


def test_stage_details_escape_user_content_and_render_blockers() -> None:
    stages = [
        {
            **_stage("requirement", "変更要件", "blocked"),
            "summary": "<script>alert(1)</script>",
            "blocking_reasons": ["RAG <index> が未準備です"],
            "details": {"requirement_text": "A & B"},
        }
    ]

    html = _run("stageDetails", stages)

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "RAG &lt;index&gt; が未準備です" in html
    assert "A &amp; B" in html


def test_stage_details_render_test_plan_data_flow_cleanup_and_report_without_ids() -> None:
    stages = [
        {
            **_stage("compile_test", "コード変更・コンパイル・テスト", "completed"),
            "details": {
                "test_cases": [
                    {
                        "test_case_id": "internal-case-id",
                        "title": "差戻し状態で検索する",
                        "level": "ui",
                        "execution_mode": "browser",
                        "preconditions": ["差戻し申請が存在する"],
                        "steps": ["一覧を開く", "差戻しを検索する"],
                        "expected_results": ["対象申請が一件表示される"],
                    }
                ]
            },
        },
        {
            **_stage("ui_validation", "テストデータ・UI 検証", "completed"),
            "details": {
                "generation_flows": [
                    {
                        "flow_id": "internal-flow-id",
                        "title": "差戻し申請を作成して検証する",
                        "status": "passed",
                        "steps": [
                            {
                                "step_id": "internal-step-id",
                                "sequence": 1,
                                "channel": "http",
                                "business_action": "差戻し申請を作成する",
                                "status": "passed",
                                "mapped_test_step_count": 1,
                                "computer_use_fallback": {
                                    "reason": "canvas",
                                    "objective": "画面上の状態を選択する",
                                    "max_actions": 3,
                                },
                                "input_variables": ["employee"],
                                "output_variables": ["expense_id"],
                                "assertions": [
                                    {
                                        "assertion_id": "internal-assertion-id",
                                        "observe_via": "response",
                                        "subject": "status",
                                        "operator": "equals",
                                        "expected": "RETURNED",
                                    }
                                ],
                            }
                        ],
                        "final_assertions": [],
                        "cleanup_policy": "delete_after_run",
                        "cleanup_steps": [
                            {
                                "sequence": 1,
                                "channel": "http",
                                "business_action": "作成した申請を削除する",
                                "status": "passed",
                            }
                        ],
                    }
                ]
            },
        },
        {
            **_stage("final_report", "最終レポート", "completed"),
            "details": {
                "test_results": [
                    {
                        "title": "差戻し状態で検索する",
                        "status": "passed",
                        "summary": "期待結果を確認",
                    }
                ]
            },
        },
    ]

    html = _run("stageDetails", stages)

    assert "差戻し状態で検索する" in html
    assert "入力変数:" in html
    assert "自然言語手順との対応:" in html
    assert "AI 画面操作フォールバック" in html
    assert "画面上の状態を選択する" in html
    assert "expense_id" in html
    assert "RETURNED" in html
    assert "クリーンアップ" in html
    assert "作成した申請を削除する" in html
    assert "期待結果を確認" in html
    assert html.count("data-open-test-case-revision") == 2
    assert "internal-case-id" not in html
    assert "internal-flow-id" not in html
    assert "internal-step-id" not in html
    assert "internal-assertion-id" not in html


def test_stage_details_render_business_coverage_for_human_confirmation() -> None:
    stages = [
        {
            **_stage("ui_validation", "テストデータ・UI 検証", "waiting"),
            "details": {
                "business_coverage_status": "failed",
                "business_coverage_percent": 43,
                "business_coverage_items": [
                    {
                        "text": "ステータス選択肢を維持する",
                        "status": "uncovered",
                        "test_case_count": 0,
                        "criterion_count": 0,
                    },
                    {
                        "text": "すべてで全件を表示する",
                        "status": "covered",
                        "test_case_count": 1,
                        "criterion_count": 1,
                    },
                ],
            },
        }
    ]

    html = _run("stageDetails", stages, "ui_validation")

    assert "43%" in html
    assert "ステータス選択肢を維持する" in html
    assert "未カバー" in html
    assert "すべてで全件を表示する" in html
    assert "カバー済み" in html


def test_stage_details_render_real_database_coverage_proof() -> None:
    stages = [
        {
            **_stage("ui_validation", "テストデータ・UI 検証", "blocked"),
            "details": {
                "data_coverage_status": "failed",
                "data_coverage_percent": 0,
                "data_coverage_proofs": [
                    {
                        "condition_id": "expense-returned-status",
                        "criterion_ref": "criterion-returned",
                        "test_case_ref": "expense-returned-ui",
                        "test_data_id": "expense-returned-data",
                        "path": "rows[0].status",
                        "operator": "equals",
                        "expected": "RETURNED",
                        "actual": "APPROVED<script>",
                        "status": "failed",
                        "failure_reason": "database value differs",
                        "evidence_ref": "artifact://result/data-coverage/status",
                    }
                ],
            },
        }
    ]

    html = _run("stageDetails", stages, "ui_validation")

    assert "実 DB データ条件の検証結果" in html
    assert "criterion-returned" in html
    assert "expense-returned-ui" in html
    assert "expense-returned-data" in html
    assert "RETURNED" in html
    assert "APPROVED&lt;script&gt;" in html
    assert "artifact://result/data-coverage/status" in html
    assert "database value differs" in html
    assert "APPROVED<script>" not in html


def _stage(stage_id: str, label: str, status: str) -> dict[str, object]:
    return {
        "stage_id": stage_id,
        "label": label,
        "status": status,
        "summary": label,
        "executor": "operamind",
        "blocking_reasons": [],
        "details": {},
    }


def _run(function_name: str, *arguments: object) -> str:
    return _run_module(FLOW_SCRIPT, function_name, *arguments)


def _run_module(script: Path, function_name: str, *arguments: object) -> str:
    javascript = """
const flow = require(process.argv[1]);
const name = process.argv[2];
const args = JSON.parse(process.argv[3]);
process.stdout.write(JSON.stringify(flow[name](...args)));
"""
    result = subprocess.run(
        [
            "node",
            "-e",
            javascript,
            str(script),
            function_name,
            json.dumps(arguments, ensure_ascii=False),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    value: object = json.loads(result.stdout)
    assert isinstance(value, str)
    return value
