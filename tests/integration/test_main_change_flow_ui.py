from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from playwright.sync_api import Page, expect, sync_playwright

from operamind.web.app import create_app
from operamind.web.dependencies import get_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("OPERAMIND_PLAYWRIGHT_LIVE") != "1",
        reason="OPERAMIND_PLAYWRIGHT_LIVE is not set",
    ),
]

ROOT = Path(__file__).parents[2]


class _MainFlowService:
    def list_projects(self) -> dict[str, object]:
        return {
            "projects": [{"project_id": "visiondemo", "name": "VisionDemo"}],
            "count": 1,
        }

    def list_change_requests(self, *, project_id: str) -> dict[str, object]:
        assert project_id == "visiondemo"
        return {
            "project_id": project_id,
            "count": 1,
            "change_requests": [
                {
                    "change_request_id": "change-ui-001",
                    "requirement_text": "経費一覧に差戻し状態を追加する",
                }
            ],
        }

    def main_change_flow(self, request_id: str) -> dict[str, object]:
        assert request_id == "change-ui-001"
        stages = [
            _stage("requirement", "変更要件", "completed", "user"),
            _stage("document_change", "設計書差分", "completed", "vscode_github_copilot"),
            _stage("code_scope", "コード影響範囲", "completed", "operamind"),
            _stage(
                "compile_test",
                "コード変更・コンパイル・テスト",
                "completed",
                "vscode_github_copilot",
            ),
            _stage("ui_validation", "テストデータ・UI 検証", "running", "operamind"),
            _stage("final_report", "最終レポート", "waiting", "operamind"),
        ]
        stages[0]["details"] = {"requirement_text": "経費一覧に差戻し状態を追加する"}
        stages[1]["details"] = {
            "change_count": 1,
            "changes": [
                {
                    "summary": "状態説明を更新",
                    "domain": "expense",
                    "fact_type": "screen_field",
                    "change_type": "modified",
                    "field_deltas": [
                        {"field": "label", "before": "承認済", "after": "差戻し"}
                    ],
                }
            ],
        }
        stages[2]["details"] = {
            "items": [
                {
                    "target_path": "src/ExpenseService.java",
                    "target_symbols": ["search"],
                    "recommended_action": "modify",
                    "test_file_refs": ["test/ExpenseServiceTest.java"],
                    "rationale": "状態検索条件を変更するため",
                }
            ],
            "impact_graph": {
                "nodes": [
                    {
                        "path": "src/ExpenseService.java",
                        "role": "production",
                        "language": "java",
                        "directly_impacted": True,
                        "recommended_action": "modify",
                        "rationale": "状態検索条件を変更するため",
                        "symbols": ["search"],
                        "related_tests": ["test/ExpenseServiceTest.java"],
                    },
                    {
                        "path": "src/ExpenseRepository.java",
                        "role": "production",
                        "language": "java",
                        "directly_impacted": False,
                        "rationale": "Code Graph の calls 関係で変更対象に接続しています。",
                        "symbols": ["findByStatus"],
                        "related_tests": [],
                    },
                    {
                        "path": "test/ExpenseServiceTest.java",
                        "role": "test",
                        "language": "java",
                        "directly_impacted": False,
                        "rationale": "関連テストとして影響範囲に含まれています。",
                        "symbols": ["searchReturned"],
                        "related_tests": [],
                    },
                ],
                "edges": [
                    {
                        "from_path": "src/ExpenseService.java",
                        "to_path": "src/ExpenseRepository.java",
                        "relation": "calls",
                        "evidence_source": "code_graph",
                    },
                    {
                        "from_path": "src/ExpenseService.java",
                        "to_path": "test/ExpenseServiceTest.java",
                        "relation": "related_test",
                        "evidence_source": "impact_report",
                    },
                ],
                "total_file_count": 3,
                "visible_file_count": 3,
                "relation_count": 2,
                "truncated": False,
            },
        }
        stages[3]["details"] = {
            "copilot_task_state": "in_progress",
            "test_cases": [
                {
                    "title": "差戻し状態で検索する",
                    "level": "ui",
                    "execution_mode": "browser",
                    "preconditions": ["差戻し申請が存在する"],
                    "steps": ["一覧を開く", "差戻しを検索する"],
                    "expected_results": ["対象申請が一件表示される"],
                }
            ],
            "commands": [
                {"command_ref": "targeted-unit", "status": "passed", "exit_code": 0}
            ],
        }
        stages[4]["details"] = {
            "generation_flows": [
                {
                    "title": "差戻し申請を作成して検証する",
                    "status": "pending",
                    "steps": [
                        {
                            "sequence": 1,
                            "channel": "http",
                            "business_action": "差戻し申請を作成する",
                            "status": "pending",
                            "input_variables": ["employee"],
                            "output_variables": ["expense_id"],
                            "assertions": [
                                {
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
                            "status": "pending",
                        }
                    ],
                }
            ]
        }
        return {
            "change_request_id": request_id,
            "project_id": "visiondemo",
            "status": "in_progress",
            "current_stage": "ui_validation",
            "progress_percent": 67,
            "stages": stages,
            "blocking_reasons": [],
        }


def test_main_change_flow_is_the_only_web_workspace_on_desktop_and_mobile() -> None:
    app = create_app(
        repository_root=ROOT,
        database_url="postgresql:///unused",
        enable_internal_coordinator=False,
    )
    app.dependency_overrides[get_service] = lambda: _MainFlowService()
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    errors: list[str] = []

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                channel=os.getenv("OPERAMIND_PLAYWRIGHT_CHANNEL", "msedge"),
            )
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on(
                "console",
                lambda message: errors.append(
                    f"{message.text} ({message.location.get('url', 'unknown')})"
                )
                if message.type == "error"
                else None,
            )
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{port}", wait_until="networkidle")

            expect(
                page.get_by_role(
                    "heading",
                    name="経費一覧に差戻し状態を追加する",
                    level=1,
                )
            ).to_be_visible()
            expect(page.locator("#pageRequestId")).to_have_text("change-ui-001")
            expect(page.locator(".stage-step")).to_have_count(6)
            expect(page.locator(".stage-card")).to_have_count(6)
            expect(page.get_by_text("VS Code GitHub Copilot", exact=True)).to_have_count(2)
            page.locator("#stage-code_scope > summary").click()
            expect(page.get_by_text("src/ExpenseService.java", exact=True).first).to_be_visible()
            expect(page.locator(".impact-node")).to_have_count(3)
            page.locator('[data-impact-node-index="1"]').click()
            expect(page.locator("[data-impact-node-details]")).to_contain_text(
                "src/ExpenseRepository.java"
            )
            expect(page.locator("[data-impact-node-details]")).to_contain_text(
                "findByStatus"
            )
            page.wait_for_timeout(3200)
            expect(page.locator("[data-impact-node-details]")).to_contain_text(
                "src/ExpenseRepository.java"
            )
            page.locator("#stage-compile_test > summary").click()
            expect(page.get_by_text("targeted-unit", exact=True)).to_be_visible()
            expect(page.get_by_text("差戻し状態で検索する", exact=True)).to_be_visible()
            page.locator("#stage-compile_test").get_by_role(
                "button", name="自然言語で修正"
            ).click()
            expect(
                page.get_by_role("heading", name="UI テスト計画の修正")
            ).to_be_visible()
            page.get_by_role("button", name="閉じる").last.click()
            expect(page.get_by_text("出力変数: expense_id", exact=True)).to_be_visible()
            expect(page.get_by_text("作成した申請を削除する", exact=True)).to_be_visible()
            expect(page.get_by_text("copilot_task_id", exact=True)).to_have_count(0)
            expect(page.get_by_text("自動編成作業の管理", exact=True)).to_have_count(0)
            expect(page.get_by_text("標準設定プロファイル", exact=True)).to_have_count(0)
            _assert_no_horizontal_overflow(page)

            page.set_viewport_size({"width": 390, "height": 844})
            _assert_no_horizontal_overflow(page)
            expect(
                page.get_by_role(
                    "heading",
                    name="経費一覧に差戻し状態を追加する",
                    level=1,
                )
            ).to_be_visible()
            expect(page.locator("#pageRequestId")).to_have_text("change-ui-001")
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()

    assert errors == []


def _stage(stage_id: str, label: str, status: str, executor: str) -> dict[str, Any]:
    return {
        "stage_id": stage_id,
        "label": label,
        "status": status,
        "summary": f"{label} の状態です。",
        "executor": executor,
        "blocking_reasons": [],
        "details": {},
    }


def _assert_no_horizontal_overflow(page: Page) -> None:
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
