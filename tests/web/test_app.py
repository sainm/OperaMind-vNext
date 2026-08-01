from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import Any

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from operamind.web import app as web_app_module
from operamind.web.app import create_app
from operamind.web.dependencies import get_service

ROOT = Path(__file__).parents[2]


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
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
        self.calls.append(
            (
                "command",
                {
                    "command_scope": command_scope,
                    "idempotency_key": idempotency_key,
                    "actor": actor,
                    "payload": payload,
                },
            )
        )
        return operation()

    def list_projects(self) -> dict[str, object]:
        return {
            "projects": [
                {
                    "project_id": "demo",
                    "name": "Demo",
                    "case_count": 3,
                    "change_request_count": 4,
                    "workspace_root": "/workspace/demo",
                    "document_roots": ["/documents/demo"],
                    "source_control_kind": "local_files",
                }
            ],
            "count": 1,
        }

    def initialize_project(self, value: Any) -> dict[str, object]:
        self.calls.append(("project-initialize", value))
        return {
            "created": True,
            "project": {
                "project_id": value.project_id,
                "name": value.name,
                "workspace_root": str(value.workspace_root),
                "document_roots": [str(root) for root in value.document_roots],
                "source_control_kind": "local_files",
            },
        }

    def list_change_requests(self, *, project_id: str) -> dict[str, object]:
        return {
            "project_id": project_id,
            "change_requests": [
                {
                    "change_request_id": "change-1",
                    "requirement_text": "差戻し状態を検索可能にする",
                    "analysis_case_id": "internal-case-1",
                    "document_review_status": "confirmed",
                }
            ],
            "count": 1,
        }

    def submit_change_request(self, value: object) -> dict[str, object]:
        self.calls.append(("submit", value))
        return {
            "created": True,
            "change_request": {
                "change_request_id": "change-1",
                "analysis_case_id": "internal-case-1",
            },
            "copilot_task": {"coding_task_id": "internal-task-1"},
        }

    def start_change_automation(self, **values: object) -> dict[str, object]:
        self.calls.append(("flow-start", values))
        return {
            "run": {
                "current_stage": "document_generation",
                "status": "waiting",
            }
        }

    def get_change_request(self, request_id: str) -> dict[str, object]:
        return {"change_request_id": request_id, "project_id": "demo"}

    def main_change_flow(self, request_id: str) -> dict[str, object]:
        stage_ids = (
            "requirement",
            "document_change",
            "code_scope",
            "compile_test",
            "ui_validation",
            "final_report",
        )
        return {
            "change_request_id": request_id,
            "project_id": "demo",
            "status": "in_progress",
            "current_stage": "document_change",
            "progress_percent": 17,
            "blocking_reasons": [],
            "stages": [
                {
                    "stage_id": stage_id,
                    "label": stage_id,
                    "status": "completed" if index == 0 else "waiting",
                    "summary": stage_id,
                    "executor": "user" if index == 0 else "operamind",
                    "blocking_reasons": [],
                    "details": {},
                }
                for index, stage_id in enumerate(stage_ids)
            ],
        }

    def propose_test_case_revision(self, **values: object) -> dict[str, object]:
        self.calls.append(("revision-propose", values))
        return {
            "state": "ready_for_confirmation",
            "proposal": {
                "proposal_id": "proposal-1",
                "instruction": values["instruction"],
                "analysis_status": "deterministic",
                "operations": [
                    {
                        "case_title": "差戻し状態で検索する",
                        "field": "expected_results",
                        "action": "replace",
                        "summary_before": "期待結果: 1 件を表示する",
                        "summary_after": "期待結果: 2 件を表示する",
                    }
                ],
                "ambiguities": [],
                "blocking_reasons": [],
            },
        }

    def confirm_test_case_revision(self, **values: object) -> dict[str, object]:
        self.calls.append(("revision-confirm", values))
        return {"state": "applied", "flow": self.main_change_flow(str(values["request_id"]))}

    def screenshot_path(self, **values: str) -> Path:
        self.calls.append(("screenshot", values))
        if self.screenshot is None:
            raise ValueError("Screenshot evidence does not exist")
        return self.screenshot

    def claim_copilot_task(self, **values: object) -> dict[str, object]:
        self.calls.append(("bridge-next", values))
        return {"task": None}

    def accept_copilot_task(self, **values: object) -> dict[str, object]:
        self.calls.append(("bridge-accept", values))
        return {"state": "accepted"}

    def resume_copilot_task(self, **values: object) -> dict[str, object]:
        self.calls.append(("bridge-resume", values))
        return {"state": "in_progress"}

    def cancel_copilot_task_from_bridge(self, **values: object) -> dict[str, object]:
        self.calls.append(("bridge-cancel", values))
        return {"state": "cancelled"}


def client_with_fake(*, bridge_token: str | None = None) -> tuple[TestClient, FakeService]:
    fake = FakeService()
    app = create_app(
        repository_root=ROOT,
        database_url="postgresql:///unused",
        bridge_token=bridge_token,
    )
    app.dependency_overrides[get_service] = lambda: fake
    return TestClient(app), fake


def test_web_lifespan_starts_and_stops_internal_coordinator(
    monkeypatch: MonkeyPatch,
) -> None:
    started = Event()
    stopped = Event()
    constructor_values: dict[str, object] = {}

    class Coordinator:
        def __init__(self, **values: object) -> None:
            constructor_values.update(values)

        def run_forever(self, *, stop_event: Event, poll_seconds: float) -> None:
            assert poll_seconds == 0.1
            started.set()
            stop_event.wait(2)
            stopped.set()

    monkeypatch.setattr(web_app_module, "MainFlowCoordinator", Coordinator)
    app = create_app(
        repository_root=ROOT,
        database_url="postgresql:///unused",
        coordinator_poll_seconds=0.1,
    )

    with TestClient(app):
        assert started.wait(1)
        assert stopped.is_set() is False

    assert stopped.wait(1)
    assert constructor_values["database_url"] == "postgresql:///unused"
    assert constructor_values["repository_root"] == ROOT


def test_local_web_exposes_only_project_selection_and_six_stage_flow() -> None:
    client, _ = client_with_fake()

    assert client.get("/health").json() == {
        "status": "ok",
        "product": "operamind",
        "version": "0.1.0.dev0",
    }
    assert client.get("/").status_code == 200
    assert client.get("/api/v1/projects").json() == {
        "projects": [
            {
                "project_id": "demo",
                "name": "Demo",
                "workspace_root": "/workspace/demo",
                "document_roots": ["/documents/demo"],
                "source_control_kind": "local_files",
            }
        ],
        "count": 1,
    }
    requests = client.get("/api/v1/change-requests?project_id=demo")
    assert requests.status_code == 200
    assert requests.json() == {
        "change_requests": [
            {
                "change_request_id": "change-1",
                "requirement_text": "差戻し状態を検索可能にする",
            }
        ],
        "count": 1,
    }
    flow = client.get("/api/v1/change-requests/change-1/flow").json()
    assert [stage["stage_id"] for stage in flow["stages"]] == [
        "requirement",
        "document_change",
        "code_scope",
        "compile_test",
        "ui_validation",
        "final_report",
    ]
    assert set(flow) == {
        "change_request_id",
        "project_id",
        "status",
        "current_stage",
        "progress_percent",
        "blocking_reasons",
        "stages",
    }


def test_project_is_initialized_from_local_paths_without_git_or_sql_steps() -> None:
    client, fake = client_with_fake()

    response = client.post(
        "/api/v1/projects",
        json={
            "project_id": "local-demo",
            "name": "ローカル資料プロジェクト",
            "workspace_root": "/local/code",
            "document_roots": ["/local/design", "/shared/api"],
        },
        headers={
            "X-OperaMind-Actor": "local-user",
            "Idempotency-Key": "project-init-1",
        },
    )

    assert response.status_code == 201
    assert response.json() == {
        "created": True,
        "project": {
            "project_id": "local-demo",
            "name": "ローカル資料プロジェクト",
            "workspace_root": "/local/code",
            "document_roots": ["/local/design", "/shared/api"],
            "source_control_kind": "local_files",
        },
    }
    assert [name for name, _value in fake.calls] == ["command", "project-initialize"]
    initialized = fake.calls[1][1]
    assert initialized.configured_by == "local-user"
    assert initialized.workspace_root == Path("/local/code")


def test_change_request_starts_internal_flow_without_user_authentication() -> None:
    client, fake = client_with_fake()
    body = {
        "change_request_id": "change-1",
        "project_id": "demo",
        "requirement_text": "差戻し状態を検索可能にする",
    }

    response = client.post(
        "/api/v1/change-requests",
        json=body,
        headers={
            "X-OperaMind-Actor": "local-user",
            "Idempotency-Key": "request-1",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["created"] is True
    assert payload["flow"]["current_stage"] == "document_change"
    assert "change_request" not in payload
    assert "copilot_task" not in payload
    assert "analysis_case_id" not in repr(payload)
    assert "automation_run_id" not in repr(payload)
    assert [name for name, _value in fake.calls] == ["command", "submit", "flow-start"]
    submitted = fake.calls[1][1]
    assert submitted.analysis_case_id is None
    assert submitted.input_mode == "natural_language"
    assert submitted.business_rules[0].text == body["requirement_text"]


def test_old_management_and_manual_approval_routes_are_absent() -> None:
    client, _ = client_with_fake()
    paths = [
        "/api/v1/projects/demo/profiles",
        "/api/v1/projects/demo/ui-knowledge/reviews",
        "/api/v1/change-requests/change-1/automation",
        "/api/v1/change-requests/change-1/orchestration",
        "/api/v1/change-requests/change-1/document-review",
        "/api/v1/change-requests/change-1/test-data-runs",
        "/api/v1/change-requests/change-1/copilot-task",
        "/api/v1/change-requests/change-1",
    ]

    assert all(client.get(path).status_code == 404 for path in paths)


def test_test_case_revision_is_previewed_then_confirmed_in_the_same_change_flow() -> None:
    client, fake = client_with_fake()
    instruction = (
        "ケース「差戻し状態で検索する」の期待結果「1 件を表示する」を「2 件を表示する」に変更"
    )

    preview = client.post(
        "/api/v1/change-requests/change-1/test-case-revisions",
        json={"instruction": instruction},
        headers={"X-OperaMind-Actor": "local-user", "Idempotency-Key": "preview-1"},
    )
    applied = client.post(
        "/api/v1/change-requests/change-1/test-case-revisions/proposal-1/confirm",
        json={"selections": {}},
        headers={"X-OperaMind-Actor": "local-user", "Idempotency-Key": "confirm-1"},
    )

    assert preview.status_code == 200
    assert preview.json()["proposal"]["operations"][0] == {
        "case_title": "差戻し状態で検索する",
        "field": "expected_results",
        "action": "replace",
        "summary_before": "期待結果: 1 件を表示する",
        "summary_after": "期待結果: 2 件を表示する",
    }
    assert "test_case_id" not in repr(preview.json())
    assert applied.status_code == 200
    assert applied.json()["state"] == "applied"
    assert [name for name, _value in fake.calls] == [
        "command",
        "revision-propose",
        "command",
        "revision-confirm",
    ]


def test_openapi_contains_only_six_stage_web_and_token_protected_bridge_routes() -> None:
    client, _ = client_with_fake(bridge_token="bridge-secret")

    paths = set(client.get("/openapi.json").json()["paths"])

    assert paths == {
        "/api/v1/projects",
        "/api/v1/change-requests",
        "/api/v1/change-requests/{request_id}/flow",
        "/api/v1/change-requests/{request_id}/test-case-revisions",
        ("/api/v1/change-requests/{request_id}/test-case-revisions/{proposal_id}/confirm"),
        "/api/v1/change-requests/{request_id}/screenshots/{evidence_id}",
        "/api/v1/local-bridge/tasks/next",
        "/api/v1/local-bridge/tasks/{coding_task_id}/accept",
        "/api/v1/local-bridge/tasks/{coding_task_id}/resume",
        "/api/v1/local-bridge/tasks/{coding_task_id}/cancel",
        "/api/v1/local-bridge/diagnostics",
    }


def test_loopback_bridge_remains_token_protected() -> None:
    client, fake = client_with_fake(bridge_token="bridge-secret")
    params = {"workspace_root": "/workspace/linked", "consumer_id": "vscode-1"}

    assert client.get("/api/v1/local-bridge/tasks/next", params=params).status_code == 401
    response = client.get(
        "/api/v1/local-bridge/tasks/next",
        params=params,
        headers={"Authorization": "Bearer bridge-secret"},
    )

    assert response.status_code == 200
    assert fake.calls[-1][0] == "bridge-next"


def test_screenshot_content_is_scoped_by_change_request(tmp_path: Path) -> None:
    client, fake = client_with_fake()
    screenshot = tmp_path / "ui.png"
    screenshot.write_bytes(b"png")
    fake.screenshot = screenshot

    response = client.get("/api/v1/change-requests/change-1/screenshots/screen-1")

    assert response.status_code == 200
    assert response.content == b"png"
    assert fake.calls[-1] == (
        "screenshot",
        {
            "request_id": "change-1",
            "evidence_id": "screen-1",
        },
    )


def test_validation_response_propagates_trace_id() -> None:
    client, _ = client_with_fake()

    response = client.get(
        "/api/v1/change-requests",
        headers={"X-Trace-ID": "trace-123"},
    )

    assert response.status_code == 422
    assert response.headers["X-Trace-ID"] == "trace-123"
    assert response.json()["trace_id"] == "trace-123"


def test_single_flow_css_keeps_long_japanese_content_responsive() -> None:
    stylesheet = (ROOT / "src/operamind/web/static/app.css").read_text(encoding="utf-8")
    page = (ROOT / "src/operamind/web/static/index.html").read_text(encoding="utf-8")

    assert "minmax(0, 1fr)" in stylesheet
    assert "overflow-wrap: anywhere" in stylesheet
    assert "内部の承認、キュー、担当割当は自動処理されます" in page


def test_change_request_dialog_guides_submission_without_changing_the_api_flow() -> None:
    stylesheet = (ROOT / "src/operamind/web/static/app.css").read_text(encoding="utf-8")
    page = (ROOT / "src/operamind/web/static/index.html").read_text(encoding="utf-8")
    script = (ROOT / "src/operamind/web/static/app.js").read_text(encoding="utf-8")

    assert "変更要求を送信" in page
    assert 'id="requestProjectName"' in page
    assert 'id="requestWorkspaceSummary"' in page
    assert 'id="requestDocumentSummary"' in page
    assert 'id="requirementCount"' in page
    assert "関連する設計書を特定" in page
    assert "データ生成、UI 検証、レポート" in page

    assert 'elements.requirementText.addEventListener("input", updateRequirementCount)' in script
    assert 'setRequestSubmitting(true)' in script
    assert 'setRequestFormStatus(error.message, "error")' in script
    assert 'api("/api/v1/change-requests"' in script

    assert ".change-request-dialog" in stylesheet
    assert ".request-context-grid" in stylesheet
    assert ".request-writing-guide" in stylesheet
    assert ".request-form-status.error" in stylesheet
    assert "grid-template-columns: 1fr" in stylesheet


def test_project_dialog_keeps_long_rag_initialization_status_visible() -> None:
    page = (ROOT / "src/operamind/web/static/index.html").read_text(encoding="utf-8")
    script = (ROOT / "src/operamind/web/static/app.js").read_text(encoding="utf-8")

    assert 'id="projectFormStatus"' in page
    assert 'id="submitProjectButton"' in page
    assert 'setProjectSubmitting(true)' in script
    assert "RAG 基線を準備しています" in script
    assert 'setProjectFormStatus(error.message, "error")' in script
