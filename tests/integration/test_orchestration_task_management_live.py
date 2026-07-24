from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from operamind.application.web_control_plane import (
    BusinessRuleInput,
    ChangeRequestInput,
    WebControlPlaneService,
)
from operamind.infrastructure.postgres import (
    MigrationCatalog,
    MigrationRunner,
    OrchestrationTaskRepository,
)
from operamind.web.app import create_app

ROOT = Path(__file__).parents[2]
DATABASE_URL = os.getenv("OPERAMIND_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set"),
    pytest.mark.skipif(
        os.getenv("OPERAMIND_PLAYWRIGHT_LIVE") != "1",
        reason="OPERAMIND_PLAYWRIGHT_LIVE is not set",
    ),
]


def test_real_postgres_task_management_browser_e2e() -> None:
    assert DATABASE_URL is not None
    suffix = uuid4().hex
    project_id = f"task-browser-project-{suffix}"
    ready_request_id = f"task-browser-ready-{suffix}"
    blocked_request_id = f"task-browser-blocked-{suffix}"
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        MigrationRunner(connection, MigrationCatalog.load(ROOT / "migrations")).apply()
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO projects (project_id, name) VALUES (%s, 'Task browser E2E')",
                (project_id,),
            )
        service = WebControlPlaneService(connection=connection, repository_root=ROOT)
        task_repository = OrchestrationTaskRepository(connection)
        ready_task = _create_task(service, project_id, ready_request_id)
        blocked_task = _create_task(service, project_id, blocked_request_id)
        expired_worker = task_repository.register_worker(
            executor_kind="agent",
            executor_id="expired-worker",
            capabilities=("requirement_review",),
            project_id=project_id,
            max_concurrent_tasks=1,
            lease_seconds=30,
        )
        blocking_worker = task_repository.register_worker(
            executor_kind="agent",
            executor_id="blocking-worker",
            capabilities=("requirement_review",),
            project_id=project_id,
            max_concurrent_tasks=1,
            lease_seconds=30,
        )
        first_claim = service.claim_selected_orchestration_task(
            task_id=str(blocked_task["orchestration_task_id"]),
            executor_kind="agent",
            executor_id="expired-worker",
            capabilities=("requirement_review",),
            project_id=project_id,
            worker_token=str(expired_worker["worker_token"]),
        )["task"]
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE orchestration_task_claims
                SET lease_expires_at = claimed_at + interval '1 microsecond'
                WHERE claim_id = %s
                """,
                (first_claim["claims"][0]["claim_id"],),
            )
        second_claim = service.claim_selected_orchestration_task(
            task_id=str(blocked_task["orchestration_task_id"]),
            executor_kind="agent",
            executor_id="blocking-worker",
            capabilities=("requirement_review",),
            project_id=project_id,
            worker_token=str(blocking_worker["worker_token"]),
        )["task"]
        service.complete_orchestration_task(
            task_id=str(blocked_task["orchestration_task_id"]),
            executor_id="blocking-worker",
            lease_token=str(second_claim["lease_token"]),
            outcome="blocked",
            summary="Owner confirmation is unavailable",
            artifact_refs=(),
            evidence={"blocking_reason": "owner unavailable"},
        )

    app = create_app(repository_root=ROOT, database_url=DATABASE_URL)
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    errors: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, channel="msedge")
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "response",
                lambda response: (
                    errors.append(f"HTTP {response.status}: {response.url}")
                    if response.status >= 400 and not response.url.endswith("/favicon.ico")
                    else None
                ),
            )
            page.goto(f"http://127.0.0.1:{port}", wait_until="networkidle")
            page.locator("#projectSelect").select_option(project_id)
            page.locator("#taskStateFilter").select_option("all")
            page.locator("#applyTaskFilters").click()
            expect(page.locator("#taskManagementQueue")).to_contain_text(ready_request_id)
            expect(page.locator("#taskManagementQueue")).to_contain_text(blocked_request_id)
            expect(page.locator("#taskWorkerStatus .task-worker-card")).to_have_count(2)
            expired_worker_card = page.locator(
                '#taskWorkerStatus [data-executor-id="expired-worker"]'
            )
            blocking_worker_card = page.locator(
                '#taskWorkerStatus [data-executor-id="blocking-worker"]'
            )
            expect(expired_worker_card).to_contain_text("オンライン")
            expect(blocking_worker_card).to_contain_text("オンライン")

            page.locator("#actor").fill("browser-reviewer")
            expired_worker_card.locator(".task-worker-capabilities").fill(
                "requirement_review, impact_review"
            )
            expired_worker_card.locator(".task-worker-concurrency").fill("2")
            expired_worker_card.get_by_role("button", name="設定を保存").click()
            expect(page.locator("#notice")).to_contain_text(
                "Worker の Capability と並行上限を更新しました"
            )
            expired_worker_card = page.locator(
                '#taskWorkerStatus [data-executor-id="expired-worker"]'
            )
            expect(expired_worker_card.locator(".task-worker-concurrency")).to_have_value("2")
            expired_worker_card.get_by_role("button", name="ドレイン開始").click()
            expect(page.locator("#notice")).to_contain_text("Worker のドレインを開始しました")
            expired_worker_card = page.locator(
                '#taskWorkerStatus [data-executor-id="expired-worker"]'
            )
            expect(expired_worker_card).to_contain_text("ドレイン中")
            expect(expired_worker_card).to_contain_text("ドレイン開始")
            expired_worker_card.get_by_role("button", name="有効化").click()
            expect(page.locator("#notice")).to_contain_text("Worker を有効化しました")
            expired_worker_card = page.locator(
                '#taskWorkerStatus [data-executor-id="expired-worker"]'
            )
            expect(expired_worker_card).to_contain_text("オンライン")

            with page.expect_response(
                f"**/api/v1/orchestration-tasks/{ready_task['orchestration_task_id']}"
            ):
                page.get_by_text(ready_request_id, exact=False).first.click()
            priority = page.locator("#taskManagementDetail .task-priority-input")
            priority.fill("800")
            page.get_by_role("button", name="優先度を保存").click()
            expect(page.locator("#notice")).to_contain_text("Queue 優先度を更新しました")
            expect(page.locator("#taskManagementDetail")).to_contain_text("優先度 800")
            page.get_by_role("button", name="人として Claim").click()
            expect(page.locator("#taskManagementDetail")).to_contain_text("browser-reviewer")
            page.locator("#taskManagementActionReason").fill("別の担当者へ引き継ぐ")
            page.get_by_role("button", name="Task を Release").click()
            expect(page.locator("#notice")).to_contain_text("OrchestrationTask を Release しました")
            expect(page.locator("#taskManagementDetail")).to_contain_text("別の担当者へ引き継ぐ")

            page.locator("#taskStateFilter").select_option("blocked")
            page.locator("#taskBlockingReasonFilter").fill("owner")
            page.locator("#applyTaskFilters").click()
            expect(page.locator("#taskManagementQueue .task-card")).to_have_count(1)
            expect(page.locator("#taskManagementQueue")).to_contain_text(blocked_request_id)
            with page.expect_response(
                f"**/api/v1/orchestration-tasks/{blocked_task['orchestration_task_id']}"
            ):
                page.locator("#taskManagementQueue .task-card").click()
            expect(page.locator("#taskManagementDetail")).to_contain_text("lease_expired")
            expect(page.locator("#taskManagementDetail")).to_contain_text("owner unavailable")
            requeue = page.get_by_role("button", name="Task を Requeue")
            expect(requeue).to_be_visible()
            requeue_reason = page.locator("#taskManagementDetail #taskManagementActionReason")
            requeue_reason.fill("Owner が確認可能になった")
            expect(requeue_reason).to_have_value("Owner が確認可能になった")
            requeue.click()

            page.locator("#taskStateFilter").select_option("all")
            page.locator("#taskBlockingReasonFilter").fill("")
            page.locator("#refreshTaskManagement").click()
            expect(page.locator("#taskMonitoringMetrics")).to_contain_text("Lease 期限切れ")
            expect(page.locator("#taskMonitoringMetrics")).to_contain_text("1 件")
            expect(page.locator("#taskManagementQueue")).to_contain_text(blocked_request_id)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
    assert errors == []
    assert str(ready_task["automation_run_id"]) != str(blocked_task["automation_run_id"])


def _create_task(
    service: WebControlPlaneService, project_id: str, request_id: str
) -> dict[str, object]:
    service.submit_change_request(
        ChangeRequestInput(
            change_request_id=request_id,
            project_id=project_id,
            analysis_case_id=None,
            input_mode="natural_language",
            requirement_text="ブラウザで Task 管理を検証する",
            source_document_ref=None,
            target_document_ref=None,
            business_rules=(BusinessRuleInput(f"rule-{request_id}", "確認が必要", ()),),
            ambiguity_status="needs_confirmation",
            ambiguities=("確認境界",),
            submitted_by="owner",
        )
    )
    run = service.start_change_automation(
        request_id=request_id,
        idempotency_key=f"start-{request_id}",
        actor="owner",
    )["run"]
    task = run["current_task"]
    assert isinstance(task, dict)
    return task
