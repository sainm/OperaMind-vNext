from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from operamind.application.orchestration_task import OrchestrationSchedulingPolicy
from operamind.web.app import create_app
from operamind.web.dependencies import get_service

ROOT = Path(__file__).parents[2]


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.command_receipts: list[dict[str, object]] = []
        self.screenshot: Path | None = None

    def execute_web_command(
        self,
        *,
        command_scope: str,
        idempotency_key: str,
        actor: str,
        payload: dict[str, object],
        operation: Callable[[], dict[str, object]],
    ) -> dict[str, object]:
        self.command_receipts.append(
            {
                "command_scope": command_scope,
                "idempotency_key": idempotency_key,
                "actor": actor,
                "payload": payload,
            }
        )
        return operation()

    def list_projects(self) -> dict[str, object]:
        return {"projects": [{"project_id": "demo", "name": "Demo"}], "count": 1}

    def code_graph_view(self, **values: object) -> dict[str, object]:
        self.calls.append(("code-graph-view", values))
        return {
            "code_graph_snapshot_id": values["snapshot_id"],
            "project_id": values["project_id"],
            "nodes": [],
            "edges": [],
            "summary": {"node_count": 0, "edge_count": 0, "truncated": False},
        }

    def profile_registry(self, *, project_id: str) -> dict[str, object]:
        self.calls.append(("profile-registry", {"project_id": project_id}))
        return {
            "project_id": project_id,
            "profile_versions": [],
            "bindings": [],
            "drift_events": [],
            "rebuild_requests": [],
            "open_drift_count": 0,
            "open_impact_count": 0,
        }

    def activate_profile(self, **values: object) -> dict[str, object]:
        self.calls.append(("profile-activate", values))
        return {
            "created": True,
            "activation_event_id": "profile-activation-1",
            "affected_artifact_count": 2,
        }

    def request_profile_rebuild(self, **values: object) -> dict[str, object]:
        self.calls.append(("profile-rebuild", values))
        return {"created": True, "rebuild_request_id": "profile-rebuild-1"}

    def requeue_profile_rebuild(self, **values: object) -> dict[str, object]:
        self.calls.append(("profile-rebuild-requeue", values))
        return {"rebuild_request_id": "profile-rebuild-1", "status": "requested"}

    def list_change_requests(self, *, project_id: str) -> dict[str, object]:
        return {"change_requests": [], "count": 0, "project_id": project_id}

    def ui_knowledge_review_queue(self, *, project_id: str) -> dict[str, object]:
        self.calls.append(("ui-knowledge-queue", {"project_id": project_id}))
        return {
            "project_id": project_id,
            "draft_count": 1,
            "drafts": [{"snapshot_id": "knowledge-draft-1", "targets": []}],
            "versions": [],
        }

    def unresolved_evidence_management(
        self, *, project_id: str, history_limit: int = 50
    ) -> dict[str, object]:
        self.calls.append(
            (
                "unresolved-evidence",
                {"project_id": project_id, "history_limit": history_limit},
            )
        )
        return {
            "project_id": project_id,
            "current_reports": [
                {
                    "artifact_type": "UnresolvedEvidenceReport",
                    "schema_version": "v1",
                    "unresolved_evidence_report_id": "report-1",
                    "project_id": project_id,
                    "repository_id": "repository-1",
                    "repository_revision": "abc123",
                    "code_graph_snapshot_id": "graph-1",
                    "report_status": "needs_evidence",
                    "trigger": {
                        "trigger_type": "static_graph",
                        "evidence_refs": ["graph-1"],
                    },
                    "open_count": 1,
                    "closed_count": 0,
                    "items": [],
                }
            ],
            "history": [],
            "current_report_count": 1,
            "history_count": 0,
            "open_count": 1,
            "closed_in_current_count": 0,
        }

    def review_ui_knowledge(self, **values: object) -> dict[str, object]:
        self.calls.append(("ui-knowledge-review", values))
        return {
            "created": True,
            "result_snapshot_id": "knowledge-approved-1",
            "result_snapshot_version": values["result_snapshot_version"],
            "decision": values["decision"],
            "active": values["activate"],
        }

    def ui_knowledge_screenshot_path(self, **values: str) -> Path:
        self.calls.append(("ui-knowledge-screenshot", values))
        if self.screenshot is None:
            raise ValueError("UI Knowledge review screenshot does not exist")
        return self.screenshot

    def submit_change_request(self, value: object) -> dict[str, object]:
        self.calls.append(("submit", value))
        return {"created": True, "change_request": {"project_id": "demo"}}

    def get_change_request(self, request_id: str) -> dict[str, object]:
        return {"change_request_id": request_id}

    def document_diff(self, request_id: str) -> dict[str, object]:
        return {"request_id": request_id, "changes": [], "total": 0}

    def change_orchestration(self, request_id: str) -> dict[str, object]:
        return {"request_id": request_id, "bundle": None}

    def change_traceability(self, request_id: str) -> dict[str, object]:
        return {
            "change_request_id": request_id,
            "nodes": [],
            "edges": [],
            "gaps": [],
            "summary": {
                "node_count": 0,
                "edge_count": 0,
                "gap_count": 0,
                "critical_gap_count": 0,
                "stage_order": [],
            },
        }

    def change_automation(self, request_id: str) -> dict[str, object]:
        return {"request_id": request_id, "run": None}

    def start_change_automation(self, **values: object) -> dict[str, object]:
        self.calls.append(("automation-start", values))
        return {
            "created": True,
            "run": {
                "automation_run_id": "automation-1",
                "current_stage": "document_confirmation",
                "current_stage_label": "設計書差分の確認",
                "status": "waiting",
                "next_action": "confirm_document_diff",
                "steps": [],
                "events": [],
            },
        }

    def bind_change_request_case(self, **values: object) -> dict[str, object]:
        self.calls.append(("case-binding", values))
        return {
            "created": True,
            "binding_event_id": "binding-1",
            "analysis_case_id": values["case_id"],
        }

    def resume_change_automation(self, **values: object) -> dict[str, object]:
        self.calls.append(("automation-resume", values))
        return {"created": False, "run": {"automation_run_id": values["run_id"]}}

    def orchestration_tasks(self, run_id: str) -> dict[str, object]:
        self.calls.append(("orchestration-task-list", {"run_id": run_id}))
        return {"tasks": [{"orchestration_task_id": "task-1", "state": "ready"}]}

    def orchestration_task(self, task_id: str) -> dict[str, object]:
        self.calls.append(("orchestration-task-get", {"task_id": task_id}))
        return {"task": {"orchestration_task_id": task_id, "state": "ready"}}

    def orchestration_task_management(self, **values: object) -> dict[str, object]:
        self.calls.append(("orchestration-task-management", values))
        return {
            "count": 1,
            "tasks": [
                {
                    "orchestration_task_id": "task-blocked-1",
                    "automation_run_id": "run-2",
                    "project_id": "demo",
                    "state": "blocked",
                    "blocking_reason": "owner unavailable",
                    "required_capabilities": ["impact_review"],
                    "claims": [],
                    "results": [],
                    "dependencies": ["task-1"],
                    "events": [],
                }
            ],
        }

    def orchestration_task_dependency_graph(self, **values: object) -> dict[str, object]:
        self.calls.append(("orchestration-task-graph", values))
        return {
            "count": 3,
            "total_count": 3,
            "truncated": False,
            "tasks": [
                {
                    "orchestration_task_id": "task-1",
                    "automation_run_id": "run-2",
                    "sequence": 1,
                    "title": "設計書を確認",
                    "state": "completed",
                    "effective_state": "completed",
                    "dependencies": [],
                },
                {
                    "orchestration_task_id": "task-2",
                    "automation_run_id": "run-2",
                    "sequence": 2,
                    "title": "影響を確認",
                    "state": "blocked",
                    "effective_state": "blocked",
                    "dependencies": ["task-1"],
                },
                {
                    "orchestration_task_id": "task-3",
                    "automation_run_id": "run-2",
                    "sequence": 3,
                    "title": "変更を編成",
                    "state": "ready",
                    "effective_state": "ready",
                    "dependencies": ["task-2"],
                },
            ],
        }

    def orchestration_workers(self, **values: object) -> dict[str, object]:
        self.calls.append(("orchestration-workers", values))
        return {"workers": [], "count": 0, "project_id": values.get("project_id")}

    def update_orchestration_worker_configuration(self, **values: object) -> dict[str, object]:
        self.calls.append(("orchestration-worker-update", values))
        return {"worker": {"executor_id": values["executor_id"], "status": "online"}}

    def operate_orchestration_worker(self, **values: object) -> dict[str, object]:
        self.calls.append(("orchestration-worker-operate", values))
        return {"worker": {"executor_id": values["executor_id"], "status": "draining"}}

    def ready_orchestration_tasks(self, **values: object) -> dict[str, object]:
        self.calls.append(("orchestration-task-ready", values))
        return {"tasks": [{"orchestration_task_id": "task-1", "state": "ready"}]}

    def claim_orchestration_task(self, **values: object) -> dict[str, object]:
        self.calls.append(("orchestration-task-claim", values))
        return {
            "task": {
                "orchestration_task_id": "task-1",
                "state": "claimed",
                "lease_token": "x" * 43,
            }
        }

    def claim_selected_orchestration_task(self, **values: object) -> dict[str, object]:
        self.calls.append(("orchestration-task-claim-selected", values))
        return {
            "task": {
                "orchestration_task_id": values["task_id"],
                "state": "claimed",
                "lease_token": "y" * 43,
            }
        }

    def heartbeat_orchestration_task(self, **values: object) -> dict[str, object]:
        self.calls.append(("orchestration-task-heartbeat", values))
        return {"task": {"orchestration_task_id": "task-1", "state": "running"}}

    def release_orchestration_task(self, **values: object) -> dict[str, object]:
        self.calls.append(("orchestration-task-release", values))
        return {"task": {"orchestration_task_id": "task-1", "state": "ready"}}

    def complete_orchestration_task(self, **values: object) -> dict[str, object]:
        self.calls.append(("orchestration-task-result", values))
        return {"task": {"orchestration_task_id": "task-1", "state": "submitted"}}

    def requeue_orchestration_task(self, **values: object) -> dict[str, object]:
        self.calls.append(("orchestration-task-requeue", values))
        return {"task": {"orchestration_task_id": "task-1", "state": "ready"}}

    def execution_management(self, request_id: str) -> dict[str, object]:
        return {
            "change_request_id": request_id,
            "test_data_plan": {"status": "ready", "generation_flows": []},
            "test_data_execution": {"status": "passed"},
            "business_coverage": {"coverage_percent": 100},
            "change_closure": {
                "status": "blocked",
                "unresolved_items": ["UI verification result is missing"],
            },
            "screenshots": [
                {
                    "origin": "test_data",
                    "evidence_id": "screen-1",
                    "content_url": (
                        f"/api/v1/change-requests/{request_id}/screenshots/test_data/screen-1"
                    ),
                }
            ],
        }

    def test_case_modification_state(self, request_id: str) -> dict[str, object]:
        return {"change_request_id": request_id, "latest": None, "history": []}

    def modify_test_case(self, **values: object) -> dict[str, object]:
        self.calls.append(("test-case-modify", values))
        return {
            "created": True,
            "state": "needs_confirmation",
            "proposal": {
                "proposal_id": "proposal-001",
                "analysis_status": "needs_confirmation",
            },
            "revision": None,
        }

    def confirm_test_case_modification(self, **values: object) -> dict[str, object]:
        self.calls.append(("test-case-confirm", values))
        return {
            "created": True,
            "state": "applied",
            "proposal": {"proposal_id": values["proposal_id"]},
            "revision": {"revision_id": "revision-001"},
        }

    def undo_test_case_revision(self, **values: object) -> dict[str, object]:
        self.calls.append(("test-case-undo", values))
        return {
            "created": True,
            "state": "applied",
            "revision": {
                "revision_id": "revision-undo-001",
                "revision_kind": "undo",
                "undo_of_revision_id": values["revision_id"],
            },
        }

    def confirm_test_case_execution_scope(self, **values: object) -> dict[str, object]:
        self.calls.append(("test-case-execution-confirm", values))
        return {
            "created": True,
            "authorization_id": "execution-authorization-001",
            "approval_grant_id": values["approval_grant_id"],
            "decision": "reconfirmed",
            "confirmed_by": values["actor"],
        }

    def screenshot_path(self, **values: str) -> Path:
        self.calls.append(("screenshot", values))
        if self.screenshot is None:
            raise ValueError("Screenshot evidence does not exist")
        return self.screenshot

    def orchestrate_change_request(self, **values: object) -> dict[str, object]:
        self.calls.append(("orchestrate", values))
        return {"created": True, "bundle": {"orchestration": {"status": "ready"}}}

    def start_test_data_run(self, **values: object) -> dict[str, object]:
        self.calls.append(("test-data-start", values))
        return {
            "created": True,
            "run_id": "run-001",
            "execution_result_id": "result-001",
            "status": "running",
            "background_required": False,
            "replay_of_run_id": values.get("replay_of_run_id"),
        }

    def recover_test_data_run(self, **values: object) -> dict[str, object]:
        self.calls.append(("test-data-recover", values))
        return {
            "created": True,
            "run_id": values["run_id"],
            "status": "interrupted",
            "closure_status": "blocked",
        }

    def review_document_diff(self, **values: object) -> dict[str, object]:
        self.calls.append(("review", values))
        return {"created": True, "decision": values["decision"]}

    def case_detail(self, *, project_id: str, case_id: str) -> dict[str, object]:
        return {"progress": {"project_id": project_id, "analysis_case_id": case_id}}

    def confirm_impact(self, **values: object) -> dict[str, object]:
        self.calls.append(("confirm", values))
        return {"created": True, "report_status": "confirmed"}

    def issue_grant(self, **values: object) -> dict[str, object]:
        self.calls.append(("grant", values))
        return {"created": True, "state": "active"}

    def copilot_task(self, request_id: str) -> dict[str, object]:
        return {"task": None, "change_request_id": request_id}

    def publish_copilot_task(self, **values: object) -> dict[str, object]:
        self.calls.append(("copilot-publish", values))
        return {"created": True, "state": "pending_confirmation"}

    def cancel_copilot_task(self, **values: object) -> dict[str, object]:
        self.calls.append(("copilot-cancel", values))
        return {"state": "cancelled"}

    def retry_copilot_task(self, **values: object) -> dict[str, object]:
        self.calls.append(("copilot-retry", values))
        return {"created": True, "state": "pending_confirmation"}

    def claim_copilot_task(self, **values: object) -> dict[str, object]:
        self.calls.append(("copilot-claim", values))
        return {"task": {"state": "pending_confirmation"}}

    def accept_copilot_task(self, **values: object) -> dict[str, object]:
        self.calls.append(("copilot-accept", values))
        return {"state": "accepted"}

    def resume_copilot_task(self, **values: object) -> dict[str, object]:
        self.calls.append(("copilot-resume", values))
        return {"state": "in_progress"}

    def cancel_copilot_task_from_bridge(self, **values: object) -> dict[str, object]:
        self.calls.append(("copilot-bridge-cancel", values))
        return {"state": "cancelled"}

    def readiness(self) -> dict[str, object]:
        return {"readiness_stage": "p6", "manifest_status": "pending", "gates": []}


def client_with_fake(
    *, bridge_token: str | None = None, web_token: str | None = None
) -> tuple[TestClient, FakeService]:
    fake = FakeService()
    app = create_app(
        repository_root=ROOT,
        database_url="postgresql:///unused",
        bridge_token=bridge_token,
        web_token=web_token,
    )
    app.dependency_overrides[get_service] = lambda: fake
    return TestClient(app), fake


def test_web_token_protects_static_and_api_but_not_health_or_local_bridge_auth() -> None:
    client, _ = client_with_fake(bridge_token="bridge-secret", web_token="web-secret")

    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 401
    assert client.get("/api/v1/projects").status_code == 401
    assert client.get("/", auth=("operamind", "web-secret")).status_code == 200
    assert (
        client.get("/api/v1/projects", headers={"Authorization": "Bearer web-secret"}).status_code
        == 200
    )
    bridge = client.get(
        "/api/v1/local-bridge/tasks/next",
        params={"workspace_root": "/workspace/linked", "consumer_id": "vscode-1"},
        headers={"Authorization": "Bearer bridge-secret"},
    )
    assert bridge.status_code == 200


def test_app_keeps_orchestration_parallelism_as_deployment_state() -> None:
    app = create_app(
        repository_root=ROOT,
        database_url="postgresql:///unused",
        orchestration_scheduling_policy=OrchestrationSchedulingPolicy(max_active_tasks_per_run=4),
    )

    assert app.state.orchestration_scheduling_policy.max_active_tasks_per_run == 4


def test_health_static_page_and_read_routes() -> None:
    client, _ = client_with_fake()

    assert client.get("/health").json() == {"status": "ok"}
    assert "要件から証跡まで" in client.get("/").text
    assert "テストデータ管理" in client.get("/").text
    assert "テストデータ実行を開始" in client.get("/").text
    assert "新しい実行として再試行" in client.get("/").text
    assert "変更完了判定の管理" in client.get("/").text
    assert "生成テストケースの自然言語一括修正" in client.get("/").text
    assert "testCaseExecutionAuthorization" in client.get("/").text
    assert "versionResultComparison" in client.get("/").text
    assert "画面操作情報をレビュー" in client.get("/").text
    assert "標準設定プロファイルと設定差異の管理" in client.get("/").text
    assert "未解決証跡の管理" in client.get("/").text
    assert "候補、欠けている証拠" in client.get("/").text
    assert "自然言語から UI 検証まで一括編成" in client.get("/").text
    assert "人による作業実行" in client.get("/").text
    assert "担当を開始しただけでは承認になりません" in client.get("/").text
    assert "自動編成作業の管理" in client.get("/").text
    assert "複数の実行にまたがる待機作業" in client.get("/").text
    assert "ブロック理由" in client.get("/").text
    assert "作業依存関係図" in client.get("/").text
    assert "重要経路" in client.get("/").text
    assert 'aria-label="ワークスペース"' in client.get("/").text
    assert 'id="detailDrawer"' in client.get("/").text
    assert "変更フロー" in client.get("/").text
    assert "データ・結果・完了判定" in client.get("/").text
    assert client.get("/ui-copy.js").status_code == 200
    assert client.get("/graph-canvas.js").status_code == 200
    assert client.get("/code-graph.js").status_code == 200
    assert client.get("/layout.js").status_code == 200
    assert client.get("/task-graph.js").status_code == 200
    assert client.get("/change-management.js").status_code == 200
    assert client.get("/case-editor.js").status_code == 200
    assert client.get("/test-data-management.js").status_code == 200
    assert client.get("/verification-results.js").status_code == 200
    assert client.get("/traceability-view.js").status_code == 200
    assert client.get("/profile-registry.js").status_code == 200
    assert "VS Code GitHub Copilot へ送信" in client.get("/").text
    assert "ローカル環境診断" in client.get("/").text
    assert "ワークスペースの信頼、認証情報、データベース構造を自動変更しません" in (
        client.get("/").text
    )
    assert client.get("/api/v1/projects").json()["count"] == 1
    assert client.get("/api/v1/projects/demo/profiles").json()["project_id"] == "demo"
    assert client.get("/api/v1/readiness").json()["readiness_stage"] == "p6"
    assert client.get("/api/v1/change-requests?project_id=demo").status_code == 200
    assert client.get("/api/v1/projects/demo/cases/case-1").status_code == 200
    graph = client.get(
        "/api/v1/projects/demo/code-graphs/graph-1?max_nodes=25&max_edges=50"
    )
    assert graph.status_code == 200
    assert graph.json()["code_graph_snapshot_id"] == "graph-1"
    assert client.get("/api/v1/projects/demo/ui-knowledge/reviews").json()["draft_count"] == 1
    unresolved = client.get("/api/v1/projects/demo/unresolved-evidence?history_limit=25").json()
    assert unresolved["open_count"] == 1
    management = client.get("/api/v1/change-requests/change-1/execution-management").json()
    assert management["change_closure"]["status"] == "blocked"
    assert (
        client.get("/api/v1/change-requests/change-1/test-case-modifications").json()["latest"]
        is None
    )
    assert client.get("/api/v1/change-requests/change-1/automation").json()["run"] is None
    assert client.get("/api/v1/change-requests/change-1/copilot-task").json()["task"] is None
    traceability = client.get("/api/v1/change-requests/change-1/traceability").json()
    assert traceability["change_request_id"] == "change-1"
    assert traceability["summary"]["gap_count"] == 0


def test_local_environment_diagnostics_are_token_protected_and_secret_free() -> None:
    client, _ = client_with_fake(bridge_token="bridge-secret")
    body = {
        "consumer_id": "vscode-test",
        "observed_at": datetime.now(UTC).isoformat(),
        "workspace_fingerprint": "a" * 64,
        "vsix_version": "0.3.1",
        "bridge_url_loopback": True,
        "bridge_token_configured": True,
        "workspace_trusted": True,
        "linked_worktree": True,
        "mcp_tool_names": [
            "analysis_list_ready_cases",
            "impact_get_report",
            "copilot_get_edit_packet",
            "copilot_get_approval_grant",
            "copilot_run_approved_command",
            "copilot_validate_worktree",
            "copilot_record_edit_result",
            "copilot_get_coding_task",
            "copilot_run_task_command",
            "copilot_validate_task_diff",
            "copilot_record_task_result",
            "verification_get_ui_plan",
            "validation_get_result",
        ],
        "copilot_extension_installed": True,
        "copilot_extension_active": True,
        "copilot_extension_version": "1.300.0",
        "copilot_model_api_available": True,
        "copilot_model_count": 1,
    }

    unauthorized = client.post("/api/v1/local-bridge/diagnostics", json=body)
    accepted = client.post(
        "/api/v1/local-bridge/diagnostics",
        headers={"Authorization": "Bearer bridge-secret"},
        json=body,
    )
    public = client.get("/api/v1/local-environment/diagnostics")
    invalid = client.post(
        "/api/v1/local-bridge/diagnostics",
        headers={"Authorization": "Bearer bridge-secret"},
        json={**body, "mcp_tool_names": ["unsafe.tool/name"]},
    )

    assert unauthorized.status_code == 401
    assert accepted.status_code == 200
    assert public.status_code == 200
    assert public.json()["extension_report"]["fresh"] is True
    assert public.json()["safe_to_share"] is True
    assert "bridge-secret" not in public.text
    assert "postgresql:///unused" not in public.text
    assert invalid.status_code == 422


def test_copilot_task_publish_and_token_protected_bridge_confirmation() -> None:
    client, fake = client_with_fake(bridge_token="bridge-secret")
    published = client.post(
        "/api/v1/change-requests/change-1/copilot-task",
        headers={
            "X-OperaMind-Actor": "reviewer",
            "Idempotency-Key": "copilot-task-1",
        },
        json={
            "project_id": "demo",
            "edit_packet_id": "packet-1",
            "approval_grant_id": "grant-1",
            "workspace_root": "/workspace/linked",
            "task_summary": "差戻しを追加する",
        },
    )
    unauthorized = client.get(
        "/api/v1/local-bridge/tasks/next",
        params={"workspace_root": "/workspace/linked", "consumer_id": "vscode-1"},
    )
    claimed = client.get(
        "/api/v1/local-bridge/tasks/next",
        params={"workspace_root": "/workspace/linked", "consumer_id": "vscode-1"},
        headers={"Authorization": "Bearer bridge-secret"},
    )
    accepted = client.post(
        "/api/v1/local-bridge/tasks/task-1/accept",
        headers={"Authorization": "Bearer bridge-secret"},
        json={
            "workspace_root": "/workspace/linked",
            "consumer_id": "vscode-1",
            "accepted_by": "developer",
        },
    )
    resumed = client.get(
        "/api/v1/local-bridge/tasks/task-1/resume",
        params={"workspace_root": "/workspace/linked", "consumer_id": "vscode-1"},
        headers={"Authorization": "Bearer bridge-secret"},
    )
    bridge_cancelled = client.post(
        "/api/v1/local-bridge/tasks/task-1/cancel",
        headers={"Authorization": "Bearer bridge-secret"},
        json={
            "workspace_root": "/workspace/linked",
            "consumer_id": "vscode-1",
            "cancelled_by": "developer",
            "reason": "VS Code を再起動するため",
        },
    )
    web_cancelled = client.post(
        "/api/v1/change-requests/change-1/copilot-task/task-1/cancel",
        headers={
            "X-OperaMind-Actor": "reviewer",
            "Idempotency-Key": "cancel-1",
        },
        json={"reason": "範囲を見直すため"},
    )
    retried = client.post(
        "/api/v1/change-requests/change-1/copilot-task/task-1/retry",
        headers={
            "X-OperaMind-Actor": "reviewer",
            "Idempotency-Key": "retry-1",
        },
        json={
            "edit_packet_id": "packet-2",
            "approval_grant_id": "grant-2",
            "workspace_root": "/workspace/linked",
        },
    )

    assert published.status_code == 201
    assert unauthorized.status_code == 401
    assert claimed.status_code == 200
    assert accepted.json()["state"] == "accepted"
    assert resumed.json()["state"] == "in_progress"
    assert bridge_cancelled.json()["state"] == "cancelled"
    assert web_cancelled.json()["state"] == "cancelled"
    assert retried.status_code == 201
    assert fake.calls[-7][0] == "copilot-publish"
    assert fake.calls[-6] == (
        "copilot-claim",
        {"workspace_root": Path("/workspace/linked"), "consumer_id": "vscode-1"},
    )
    assert fake.calls[-5] == (
        "copilot-accept",
        {
            "coding_task_id": "task-1",
            "workspace_root": Path("/workspace/linked"),
            "consumer_id": "vscode-1",
            "actor": "developer",
        },
    )
    assert fake.calls[-4][0] == "copilot-resume"
    assert fake.calls[-3][0] == "copilot-bridge-cancel"
    assert fake.calls[-2][0] == "copilot-cancel"
    assert fake.calls[-1][0] == "copilot-retry"


def test_change_automation_start_and_resume_use_trusted_headers() -> None:
    client, fake = client_with_fake()

    started = client.post(
        "/api/v1/change-requests/change-1/automation",
        headers={
            "X-OperaMind-Actor": "product-owner",
            "Idempotency-Key": "one-click-1",
        },
    )
    resumed = client.post(
        "/api/v1/change-requests/change-1/automation/automation-1/resume",
        headers={"X-OperaMind-Actor": "product-owner", "Idempotency-Key": "resume-1"},
    )

    assert started.status_code == 201
    assert started.json()["run"]["current_stage"] == "document_confirmation"
    assert resumed.status_code == 200
    assert fake.calls[-2] == (
        "automation-start",
        {
            "request_id": "change-1",
            "idempotency_key": "one-click-1",
            "actor": "product-owner",
        },
    )
    assert fake.calls[-1] == (
        "automation-resume",
        {
            "request_id": "change-1",
            "run_id": "automation-1",
            "actor": "product-owner",
        },
    )


def test_agent_neutral_task_api_uses_trusted_actor_and_common_lease_protocol() -> None:
    client, fake = client_with_fake()
    headers = {
        "X-OperaMind-Actor": "worker-1",
        "X-OperaMind-Worker-Token": "worker-secret-token",
    }

    ready = client.get(
        "/api/v1/orchestration-tasks/ready",
        params={
            "executor_kind": "subagent",
            "capability": "document_review",
            "project_id": "demo",
        },
    )
    claimed = client.post(
        "/api/v1/orchestration-tasks/claim",
        headers=headers,
        json={
            "executor_kind": "subagent",
            "capabilities": ["document_review"],
            "project_id": "demo",
        },
    )
    heartbeat = client.post(
        "/api/v1/orchestration-tasks/task-1/heartbeat",
        headers=headers,
        json={"lease_token": "x" * 43},
    )
    result = client.post(
        "/api/v1/orchestration-tasks/task-1/result",
        headers=headers,
        json={
            "lease_token": "x" * 43,
            "outcome": "completed",
            "summary": "reviewed",
            "artifact_refs": ["review-1"],
            "evidence": {"human_confirmation": True},
        },
    )

    assert ready.status_code == 200
    assert claimed.status_code == 200
    assert heartbeat.json()["task"]["state"] == "running"
    assert result.json()["task"]["state"] == "submitted"
    assert fake.calls[-3] == (
        "orchestration-task-claim",
        {
            "executor_kind": "subagent",
            "executor_id": "worker-1",
            "capabilities": ("document_review",),
            "project_id": "demo",
            "worker_token": "worker-secret-token",
        },
    )
    assert fake.calls[-1][1]["executor_id"] == "worker-1"


def test_agent_neutral_task_history_is_readable_by_task_id() -> None:
    client, fake = client_with_fake()

    response = client.get("/api/v1/orchestration-tasks/task-1")

    assert response.status_code == 200
    assert response.json()["task"]["orchestration_task_id"] == "task-1"
    assert fake.calls[-1] == ("orchestration-task-get", {"task_id": "task-1"})


def test_task_management_filters_across_runs_and_blocking_reasons() -> None:
    client, fake = client_with_fake()

    response = client.get(
        "/api/v1/orchestration-tasks/management",
        params=[
            ("project_id", "demo"),
            ("state", "blocked"),
            ("state", "failed"),
            ("capability", "impact_review"),
            ("blocking_reason", "owner"),
            ("limit", "50"),
        ],
    )

    assert response.status_code == 200
    assert response.json()["tasks"][0]["automation_run_id"] == "run-2"
    assert fake.calls[-1] == (
        "orchestration-task-management",
        {
            "project_id": "demo",
            "states": ("blocked", "failed"),
            "capability": "impact_review",
            "blocking_reason": "owner",
            "limit": 50,
        },
    )


def test_task_dependency_graph_is_bounded_by_project_and_run() -> None:
    client, fake = client_with_fake()

    response = client.get(
        "/api/v1/orchestration-tasks/graph",
        params={"project_id": "demo", "run_id": "run-2", "limit": 300},
    )

    assert response.status_code == 200
    assert response.json()["tasks"][1]["dependencies"] == ["task-1"]
    assert fake.calls[-1] == (
        "orchestration-task-graph",
        {"project_id": "demo", "automation_run_id": "run-2", "limit": 300},
    )


def test_worker_operations_use_trusted_actor_and_bounded_configuration() -> None:
    client, fake = client_with_fake()
    headers = {
        "X-OperaMind-Actor": "operations-owner",
        "Idempotency-Key": "worker-config-1",
    }

    listed = client.get("/api/v1/orchestration-tasks/workers", params={"project_id": "demo"})
    updated = client.patch(
        "/api/v1/orchestration-tasks/workers/agent/worker-1",
        headers={**headers, "Idempotency-Key": "worker-drain-1"},
        json={
            "capabilities": ["document_review", "impact_review"],
            "max_concurrent_tasks": 2,
        },
    )
    drained = client.post(
        "/api/v1/orchestration-tasks/workers/agent/worker-1/drain",
        headers=headers,
    )

    assert listed.status_code == 200
    assert updated.status_code == 200
    assert drained.status_code == 200
    assert fake.calls[-3:] == [
        ("orchestration-workers", {"project_id": "demo"}),
        (
            "orchestration-worker-update",
            {
                "executor_kind": "agent",
                "executor_id": "worker-1",
                "capabilities": ("document_review", "impact_review"),
                "max_concurrent_tasks": 2,
                "actor": "operations-owner",
            },
        ),
        (
            "orchestration-worker-operate",
            {
                "executor_kind": "agent",
                "executor_id": "worker-1",
                "operation": "drain",
                "actor": "operations-owner",
            },
        ),
    ]


def test_agent_or_human_can_claim_the_selected_ready_step() -> None:
    client, fake = client_with_fake()

    response = client.post(
        "/api/v1/orchestration-tasks/task-2/claim",
        headers={"X-OperaMind-Actor": "reviewer-1"},
        json={
            "executor_kind": "human",
            "capabilities": ["impact_review"],
            "project_id": "demo",
        },
    )

    assert response.status_code == 200
    assert response.json()["task"]["orchestration_task_id"] == "task-2"
    assert fake.calls[-1] == (
        "orchestration-task-claim-selected",
        {
            "task_id": "task-2",
            "executor_kind": "human",
            "executor_id": "reviewer-1",
            "capabilities": ("impact_review",),
            "project_id": "demo",
            "worker_token": None,
        },
    )


def test_agent_neutral_task_requeue_requires_trusted_actor_and_reason() -> None:
    client, fake = client_with_fake()

    response = client.post(
        "/api/v1/orchestration-tasks/task-1/requeue",
        headers={"X-OperaMind-Actor": "operator-1", "Idempotency-Key": "requeue-1"},
        json={"reason": "外部阻断を解消したため"},
    )

    assert response.status_code == 200
    assert response.json()["task"]["state"] == "ready"
    assert fake.calls[-1] == (
        "orchestration-task-requeue",
        {"task_id": "task-1", "actor": "operator-1", "reason": "外部阻断を解消したため"},
    )


def test_change_request_case_binding_is_scoped_and_idempotent() -> None:
    client, fake = client_with_fake()

    response = client.post(
        "/api/v1/change-requests/change-1/case-binding",
        headers={
            "X-OperaMind-Actor": "product-owner",
            "Idempotency-Key": "binding-1",
        },
        json={"project_id": "demo", "analysis_case_id": "case-1"},
    )

    assert response.status_code == 200
    assert response.json()["analysis_case_id"] == "case-1"
    assert fake.calls[-1] == (
        "case-binding",
        {
            "request_id": "change-1",
            "project_id": "demo",
            "case_id": "case-1",
            "idempotency_key": "binding-1",
            "actor": "product-owner",
        },
    )


def test_screenshot_content_is_resolved_through_scoped_service(tmp_path: Path) -> None:
    screenshot = tmp_path / "evidence.png"
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\n")
    client, fake = client_with_fake()
    fake.screenshot = screenshot

    response = client.get("/api/v1/change-requests/change-1/screenshots/test_data/screen-1")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"\x89PNG\r\n\x1a\n"
    assert fake.calls[-1] == (
        "screenshot",
        {
            "request_id": "change-1",
            "origin": "test_data",
            "evidence_id": "screen-1",
        },
    )


def test_ui_knowledge_review_uses_actor_reason_and_idempotency_key() -> None:
    client, fake = client_with_fake()

    response = client.post(
        "/api/v1/projects/demo/ui-knowledge/reviews/knowledge-draft-1",
        headers={
            "X-OperaMind-Actor": "qa-user",
            "Idempotency-Key": "knowledge-review-key",
        },
        json={
            "result_snapshot_version": "1.1.0",
            "decision": "approved",
            "reason": "一意性と証跡を確認しました",
            "activate": True,
        },
    )
    invalid_rejection = client.post(
        "/api/v1/projects/demo/ui-knowledge/reviews/knowledge-draft-1",
        headers={
            "X-OperaMind-Actor": "qa-user",
            "Idempotency-Key": "invalid-review-key",
        },
        json={
            "result_snapshot_version": "1.1.0-rejected",
            "decision": "rejected",
            "reason": "候補が複数一致します",
            "activate": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["result_snapshot_id"] == "knowledge-approved-1"
    assert invalid_rejection.status_code == 422
    assert fake.calls[-1] == (
        "ui-knowledge-review",
        {
            "project_id": "demo",
            "source_snapshot_id": "knowledge-draft-1",
            "result_snapshot_version": "1.1.0",
            "decision": "approved",
            "reason": "一意性と証跡を確認しました",
            "activate": True,
            "idempotency_key": "knowledge-review-key",
            "actor": "qa-user",
        },
    )


def test_profile_activation_and_rebuild_use_actor_and_idempotency_key() -> None:
    client, fake = client_with_fake()
    headers = {
        "X-OperaMind-Actor": "platform-owner",
        "Idempotency-Key": "profile-change-key",
    }

    activation = client.post(
        "/api/v1/projects/demo/profiles/activate",
        headers=headers,
        json={
            "binding_key": "embedding:documents",
            "profile_version_id": "embedding-v2",
            "reason": "Embedding model update",
        },
    )
    rebuild = client.post(
        "/api/v1/projects/demo/profiles/rebuild-requests",
        headers={**headers, "Idempotency-Key": "profile-rebuild-key"},
        json={
            "drift_event_id": "drift-1",
            "artifact_type": "SearchIndexBuild",
            "artifact_id": "index-1",
        },
    )
    requeue = client.post(
        "/api/v1/projects/demo/profiles/rebuild-requests/profile-rebuild-1/requeue",
        headers={**headers, "Idempotency-Key": "profile-requeue-key"},
        json={"reason": "Canonical validation issue was corrected"},
    )

    assert activation.status_code == 200
    assert activation.json()["affected_artifact_count"] == 2
    assert rebuild.status_code == 201
    assert requeue.status_code == 200
    assert fake.calls[-3:] == [
        (
            "profile-activate",
            {
                "project_id": "demo",
                "binding_key": "embedding:documents",
                "profile_version_id": "embedding-v2",
                "reason": "Embedding model update",
                "idempotency_key": "profile-change-key",
                "actor": "platform-owner",
            },
        ),
        (
            "profile-rebuild",
            {
                "project_id": "demo",
                "drift_event_id": "drift-1",
                "artifact_type": "SearchIndexBuild",
                "artifact_id": "index-1",
                "idempotency_key": "profile-rebuild-key",
                "actor": "platform-owner",
            },
        ),
        (
            "profile-rebuild-requeue",
            {
                "project_id": "demo",
                "rebuild_request_id": "profile-rebuild-1",
                "reason": "Canonical validation issue was corrected",
                "idempotency_key": "profile-requeue-key",
                "actor": "platform-owner",
            },
        ),
    ]


def test_ui_knowledge_review_screenshot_is_project_and_snapshot_scoped(
    tmp_path: Path,
) -> None:
    screenshot = tmp_path / "knowledge.png"
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\n")
    client, fake = client_with_fake()
    fake.screenshot = screenshot

    response = client.get(
        "/api/v1/projects/demo/ui-knowledge/reviews/"
        "knowledge-draft-1/screenshots/knowledge-evidence-1"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert fake.calls[-1] == (
        "ui-knowledge-screenshot",
        {
            "project_id": "demo",
            "snapshot_id": "knowledge-draft-1",
            "evidence_id": "knowledge-evidence-1",
        },
    )


def test_change_request_requires_actor_and_rejects_unknown_fields() -> None:
    client, _ = client_with_fake()
    body = {
        "change_request_id": "change-1",
        "project_id": "demo",
        "input_mode": "natural_language",
        "requirement_text": "Add an approved filter",
        "business_rules": [{"business_rule_id": "rule-1", "text": "Filter is visible"}],
    }

    missing_actor = client.post("/api/v1/change-requests", json=body)
    unknown = client.post(
        "/api/v1/change-requests",
        json={**body, "unexpected": True},
        headers={"X-OperaMind-Actor": "reviewer"},
    )

    assert missing_actor.status_code == 422
    assert missing_actor.json()["code"] == "request_validation_failed"
    assert missing_actor.json()["trace_id"]
    assert unknown.status_code == 422
    assert unknown.json()["details"]


def test_change_request_and_review_forward_trusted_headers() -> None:
    client, fake = client_with_fake()
    body = {
        "change_request_id": "change-1",
        "project_id": "demo",
        "analysis_case_id": "case-1",
        "input_mode": "natural_language",
        "requirement_text": "Add a status filter",
        "business_rules": [{"business_rule_id": "rule-1", "text": "Show returned status"}],
    }

    created = client.post(
        "/api/v1/change-requests",
        json=body,
        headers={"X-OperaMind-Actor": "reviewer", "Idempotency-Key": "create-request-1"},
    )
    reviewed = client.post(
        "/api/v1/change-requests/change-1/document-review",
        json={"project_id": "demo", "decision": "confirmed"},
        headers={"X-OperaMind-Actor": "reviewer", "Idempotency-Key": "review-1"},
    )

    assert created.status_code == 201
    assert reviewed.status_code == 200
    assert fake.calls[0][0] == "submit"
    assert fake.calls[1] == (
        "review",
        {
            "idempotency_key": "review-1",
            "request_id": "change-1",
            "project_id": "demo",
            "decision": "confirmed",
            "actor": "reviewer",
            "note": None,
        },
    )


def test_confirmation_and_grant_require_idempotency_key() -> None:
    client, fake = client_with_fake()
    headers = {"X-OperaMind-Actor": "approver", "Idempotency-Key": "key-1"}
    confirmation = client.post(
        "/api/v1/projects/demo/cases/case-1/impact-confirmation",
        json={
            "change_request_id": "change-1",
            "report_id": "report-1",
            "approved_item_ids": ["item-1"],
        },
        headers=headers,
    )
    missing_key = client.post(
        "/api/v1/projects/demo/cases/case-1/approval-grants",
        json={
            "change_request_id": "change-1",
            "edit_packet_id": "packet-1",
            "expires_at": "2030-01-01T00:00:00Z",
            "command_profile_binding_key": "profile@v1",
            "test_command_refs": ["test:unit"],
        },
        headers={"X-OperaMind-Actor": "approver"},
    )
    grant = client.post(
        "/api/v1/projects/demo/cases/case-1/approval-grants",
        json={
            "change_request_id": "change-1",
            "edit_packet_id": "packet-1",
            "expires_at": "2030-01-01T00:00:00Z",
            "command_profile_binding_key": "profile@v1",
            "test_command_refs": ["test:unit"],
        },
        headers=headers,
    )

    assert confirmation.status_code == 200
    assert missing_key.status_code == 422
    assert grant.status_code == 201
    assert [call[0] for call in fake.calls] == ["confirm", "grant"]


def test_human_write_routes_require_idempotency_key() -> None:
    client, _ = client_with_fake()
    response = client.post(
        "/api/v1/change-requests/change-1/orchestration",
        headers={"X-OperaMind-Actor": "reviewer"},
    )

    assert response.status_code == 422


def test_change_request_orchestration_uses_the_authenticated_actor() -> None:
    client, fake = client_with_fake()

    response = client.post(
        "/api/v1/change-requests/change-1/orchestration",
        headers={"X-OperaMind-Actor": "reviewer", "Idempotency-Key": "orchestration-1"},
    )

    assert response.status_code == 201
    assert response.json()["bundle"]["orchestration"]["status"] == "ready"
    assert fake.command_receipts[-1] == {
        "command_scope": "change-orchestration:create:change-1",
        "idempotency_key": "orchestration-1",
        "actor": "reviewer",
        "payload": {},
    }
    assert fake.calls == [
        (
            "orchestrate",
            {"request_id": "change-1", "actor": "reviewer"},
        )
    ]


def test_natural_language_case_change_and_confirmation_use_trusted_actor() -> None:
    client, fake = client_with_fake()

    proposed = client.post(
        "/api/v1/change-requests/change-1/test-case-modifications",
        headers={"X-OperaMind-Actor": "qa-user", "Idempotency-Key": "proposal-1"},
        json={"instruction": "期待結果を変更"},
    )
    confirmed = client.post(
        "/api/v1/change-requests/change-1/test-case-modifications/proposal-001/confirm",
        headers={"X-OperaMind-Actor": "qa-user", "Idempotency-Key": "confirm-1"},
        json={"selections": {"ambiguity-1": "option-2"}},
    )
    missing_actor = client.post(
        "/api/v1/change-requests/change-1/test-case-modifications",
        json={"instruction": "期待結果を変更"},
    )

    assert proposed.status_code == 201
    assert proposed.json()["state"] == "needs_confirmation"
    assert confirmed.status_code == 200
    assert confirmed.json()["state"] == "applied"
    assert missing_actor.status_code == 422
    assert fake.calls[-2:] == [
        (
            "test-case-modify",
            {
                "request_id": "change-1",
                "instruction": "期待結果を変更",
                "actor": "qa-user",
            },
        ),
        (
            "test-case-confirm",
            {
                "request_id": "change-1",
                "proposal_id": "proposal-001",
                "selections": {"ambiguity-1": "option-2"},
                "actor": "qa-user",
            },
        ),
    ]


def test_deterministic_case_confirmation_and_undo_are_explicit() -> None:
    client, fake = client_with_fake()

    confirmed = client.post(
        "/api/v1/change-requests/change-1/test-case-modifications/proposal-001/confirm",
        headers={"X-OperaMind-Actor": "qa-user", "Idempotency-Key": "confirm-2"},
        json={"selections": {}},
    )
    undone = client.post(
        "/api/v1/change-requests/change-1/test-case-revisions/revision-001/undo",
        headers={
            "X-OperaMind-Actor": "qa-user",
            "Idempotency-Key": "undo-key",
        },
    )

    assert confirmed.status_code == 200
    assert undone.status_code == 200
    assert undone.json()["revision"]["revision_kind"] == "undo"
    assert fake.calls[-2:] == [
        (
            "test-case-confirm",
            {
                "request_id": "change-1",
                "proposal_id": "proposal-001",
                "selections": {},
                "actor": "qa-user",
            },
        ),
        (
            "test-case-undo",
            {
                "request_id": "change-1",
                "revision_id": "revision-001",
                "idempotency_key": "undo-key",
                "actor": "qa-user",
            },
        ),
    ]


def test_revised_case_execution_scope_confirmation_uses_trusted_actor() -> None:
    client, fake = client_with_fake()
    digest = "a" * 64

    response = client.post(
        "/api/v1/change-requests/change-1/test-case-execution-authorization",
        headers={"X-OperaMind-Actor": "qa-user", "Idempotency-Key": "scope-1"},
        json={"approval_grant_id": "grant-001", "target_scope_digest": digest},
    )

    assert response.status_code == 200
    assert response.json()["decision"] == "reconfirmed"
    assert fake.calls[-1] == (
        "test-case-execution-confirm",
        {
            "request_id": "change-1",
            "approval_grant_id": "grant-001",
            "target_scope_digest": digest,
            "actor": "qa-user",
        },
    )


def test_test_data_start_rerun_and_recovery_require_trusted_headers() -> None:
    client, fake = client_with_fake()
    headers = {"X-OperaMind-Actor": "tester", "Idempotency-Key": "run-key"}

    started = client.post("/api/v1/change-requests/change-1/test-data-runs", headers=headers)
    rerun = client.post(
        "/api/v1/change-requests/change-1/test-data-runs/run-001/rerun",
        headers={**headers, "Idempotency-Key": "rerun-key"},
    )
    recovered = client.post(
        "/api/v1/change-requests/change-1/test-data-runs/run-001/recover",
        headers={**headers, "Idempotency-Key": "recover-key"},
        json={
            "reason": "worker heartbeat stopped",
            "stale_before": "2026-07-19T12:00:00Z",
        },
    )
    missing_key = client.post(
        "/api/v1/change-requests/change-1/test-data-runs",
        headers={"X-OperaMind-Actor": "tester"},
    )

    assert started.status_code == 202
    assert rerun.status_code == 202
    assert recovered.status_code == 200
    assert missing_key.status_code == 422
    assert fake.calls[-3:] == [
        (
            "test-data-start",
            {
                "request_id": "change-1",
                "idempotency_key": "run-key",
                "actor": "tester",
            },
        ),
        (
            "test-data-start",
            {
                "request_id": "change-1",
                "idempotency_key": "rerun-key",
                "actor": "tester",
                "replay_of_run_id": "run-001",
            },
        ),
        (
            "test-data-recover",
            {
                "request_id": "change-1",
                "run_id": "run-001",
                "idempotency_key": "recover-key",
                "actor": "tester",
                "reason": "worker heartbeat stopped",
                "stale_before": datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
            },
        ),
    ]


def test_validation_response_propagates_caller_trace_id() -> None:
    client, _ = client_with_fake()
    response = client.get("/api/v1/change-requests", headers={"X-Trace-ID": "trace-123"})

    assert response.status_code == 422
    assert response.headers["X-Trace-ID"] == "trace-123"
    assert response.json()["trace_id"] == "trace-123"
