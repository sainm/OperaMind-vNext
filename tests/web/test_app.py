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

    def update_project_settings(self, value: Any) -> dict[str, object]:
        self.calls.append(("project-update", value))
        return {
            "project": {
                "project_id": value.project_id,
                "name": value.name,
                "workspace_root": "/workspace/demo",
                "document_roots": [str(root) for root in value.document_roots],
                "source_control_kind": "local_files",
                "test_base_url": value.test_base_url,
                "settings_revision": value.expected_revision + 1,
            },
            "onboarding": {"status": "queued", "current_stage": "discover"},
        }

    def project_onboarding(self, project_id: str) -> dict[str, object]:
        return {
            "project_id": project_id,
            "onboarding": {"status": "running", "current_stage": "documents"},
        }

    def project_document_learning(self, project_id: str) -> dict[str, object]:
        return {"project_id": project_id, "learning": None}

    def project_target_data_profile(
        self, project_id: str, *, include_statements: bool = False
    ) -> dict[str, object]:
        return {
            "project_id": project_id,
            "profile": {
                "connection_alias": "expense_test_db",
                "secret_configured": True,
                "include_statements": include_statements,
                "bindings": [],
            },
        }

    def configure_project_target_data_profile(self, **values: object) -> dict[str, object]:
        self.calls.append(("project-target-data", values))
        return {
            "project_id": values["project_id"],
            "_obsolete_secret_alias": "previous_expense_test_db",
            "profile": {
                "connection_alias": values["connection_alias"],
                "secret_configured": True,
                "bindings": list(values["bindings"]),  # type: ignore[arg-type]
            },
        }

    def cleanup_obsolete_target_data_secret(self, **values: object) -> None:
        self.calls.append(("target-data-secret-cleanup", values))

    def existing_test_data(
        self,
        project_id: str,
        *,
        change_request_id: str | None = None,
    ) -> dict[str, object]:
        return {"project_id": project_id, "registrations": [], "count": 0}

    def project_data_identity_profiles(self, project_id: str) -> dict[str, object]:
        return {"project_id": project_id, "profiles": [], "count": 0}

    def configure_project_data_identity_profiles(
        self, **values: object
    ) -> dict[str, object]:
        self.calls.append(("project-data-identity-profiles", values))
        return {
            "project_id": values["project_id"],
            "profiles": list(values["profiles"]),  # type: ignore[arg-type]
        }

    def register_existing_test_data(self, **values: object) -> dict[str, object]:
        self.calls.append(("existing-test-data-register", values))
        return {
            "registration": {
                "data_name": values["data_name"],
                "business_unique_value": values["business_unique_value"],
                "test_case_ref": values["test_case_ref"],
                "retain_after_test": values["retain_after_test"],
                "status": "candidate",
                "business_summary": {"expense_number": values["business_unique_value"]},
            }
        }

    def confirm_existing_test_data(self, **values: object) -> dict[str, object]:
        self.calls.append(("existing-test-data-confirm", values))
        return {"registration": {"status": "confirmed"}}

    def fixed_data_identifiers(self, project_id: str) -> dict[str, object]:
        return {
            "project_id": project_id,
            "pending": [],
            "planned": [],
            "frozen": [],
            "pending_count": 0,
            "planned_count": 0,
            "frozen_count": 0,
        }

    def confirm_project_document_learning(self, **values: object) -> dict[str, object]:
        self.calls.append(("project-document-learning-confirm", values))
        return {"project_id": values["project_id"], "learning": {"status": "confirmed"}}

    def project_preflight(self, project_id: str) -> dict[str, object]:
        return {
            "project_id": project_id,
            "status": "ready",
            "blocking_capabilities": [],
            "capabilities": [],
            "document_discovery": {"status": "ready", "document_count": 2},
        }

    def request_project_onboarding(self, **values: object) -> dict[str, object]:
        self.calls.append(("project-onboarding", values))
        return {
            "project_id": values["project_id"],
            "onboarding": {"status": "queued", "current_stage": "discover"},
        }

    def retry_project_onboarding(self, **values: object) -> dict[str, object]:
        self.calls.append(("project-onboarding-retry", values))
        return {
            "project_id": values["project_id"],
            "onboarding": {"status": "queued", "current_stage": "index"},
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

    def start_test_data_run(self, **values: object) -> dict[str, object]:
        self.calls.append(("test-data-rerun", values))
        return {
            "created": True,
            "run_id": "run-replayed-001",
            "execution_result_id": "result-replayed-001",
            "status": "running",
            "replay_of_run_id": values["replay_of_run_id"],
            "background_required": True,
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
        return {
            "state": "awaiting_copilot",
            "flow": self.main_change_flow(str(values["request_id"])),
        }

    def decide_change_checkpoint(self, **values: object) -> dict[str, object]:
        self.calls.append(("checkpoint-decision", values))
        return {"confirmation": values, "created": False, "run": {"status": "waiting"}}

    def next_change_confirmation(self, **values: object) -> dict[str, object]:
        self.calls.append(("bridge-confirmation-next", values))
        return {
            "confirmation": {
                "change_request_id": "change-1",
                "checkpoint": "requirement",
                "subject_digest": "a" * 64,
            }
        }

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
                "source_git_baselines": [],
                "test_base_url": None,
                "settings_revision": None,
                "onboarding": None,
                    "document_learning": None,
                    "target_data_profile": None,
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


def test_project_document_learning_is_readable_and_confirmed_from_web() -> None:
    client, fake = client_with_fake()

    current = client.get("/api/v1/projects/demo/document-learning")
    confirmed = client.post(
        "/api/v1/projects/demo/document-learning/confirm",
        headers={
            "X-OperaMind-Actor": "operator",
            "Idempotency-Key": "confirm-document-profile-1",
        },
        json={"learning_run_id": "document-learning-001"},
    )

    assert current.status_code == 200
    assert current.json() == {"project_id": "demo", "learning": None}
    assert confirmed.status_code == 200
    assert confirmed.json()["learning"]["status"] == "confirmed"
    assert ("project-document-learning-confirm", {
        "project_id": "demo",
        "learning_run_id": "document-learning-001",
        "actor": "operator",
    }) in fake.calls
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


def test_project_rejects_test_base_url_query_before_initialization() -> None:
    client, fake = client_with_fake()

    response = client.post(
        "/api/v1/projects",
        json={
            "project_id": "local-demo",
            "name": "ローカル資料プロジェクト",
            "workspace_root": "/local/code",
            "document_roots": ["/local/design"],
            "test_base_url": "http://127.0.0.1:8080/app?tenant=demo",
        },
        headers={
            "X-OperaMind-Actor": "local-user",
            "Idempotency-Key": "project-init-query",
        },
    )

    assert response.status_code == 422
    assert fake.calls == []


def test_project_settings_update_queues_a_rescan_with_optimistic_revision() -> None:
    client, fake = client_with_fake()

    response = client.patch(
        "/api/v1/projects/demo",
        json={
            "name": "Demo Updated",
            "document_roots": ["/documents/demo", "/documents/shared"],
            "test_base_url": "http://127.0.0.1:8080/app",
            "expected_revision": 3,
        },
        headers={
            "X-OperaMind-Actor": "local-user",
            "Idempotency-Key": "project-update-1",
        },
    )

    assert response.status_code == 200
    assert response.json()["project"]["settings_revision"] == 4
    assert response.json()["onboarding"]["status"] == "queued"
    assert [name for name, _value in fake.calls] == ["command", "project-update"]


def test_target_data_profile_route_never_puts_connection_secret_in_command_receipt() -> None:
    client, fake = client_with_fake()
    connection_secret = "postgresql://tester:local-password@127.0.0.1:5432/expense"
    binding = {
        "query_binding_id": "cleanup_expense",
        "operation": "cleanup",
        "statement_text": "DELETE FROM expenses WHERE id = %(id)s",
        "target_schema": "public",
        "target_table": "expenses",
        "parameter_columns": {"id": "id"},
        "input_constraints": {
            "id": {"type": "integer", "required": True},
        },
        "read_after_write_statement": "SELECT id FROM expenses WHERE id = %(id)s",
        "read_assertion": {"mode": "rows_absent"},
        "cleanup_binding_id": None,
        "idempotency_policy": "natural_key",
    }

    response = client.put(
        "/api/v1/projects/demo/target-data-profile",
        json={
            "connection_alias": "expense_test_db",
            "connection_dsn": connection_secret,
            "transaction_policy": "per_binding_transaction",
            "bindings": [binding],
        },
        headers={
            "X-OperaMind-Actor": "local-user",
            "Idempotency-Key": "target-data-1",
        },
    )

    assert response.status_code == 200
    assert response.json()["profile"]["secret_configured"] is True
    command = next(value for name, value in fake.calls if name == "command")
    assert connection_secret not in str(command)
    assert "local-password" not in str(command)
    configured = next(value for name, value in fake.calls if name == "project-target-data")
    assert configured["connection_dsn"] == connection_secret
    assert configured["dialect"] == "postgresql"
    cleanup = next(value for name, value in fake.calls if name == "target-data-secret-cleanup")
    assert cleanup == {
        "project_id": "demo",
        "connection_alias": "previous_expense_test_db",
    }
    assert "_obsolete_secret_alias" not in response.json()


def test_existing_data_route_accepts_only_business_readable_input() -> None:
    client, fake = client_with_fake()
    headers = {
        "X-OperaMind-Actor": "test-operator",
        "Idempotency-Key": "existing-data-1",
    }
    payload = {
        "change_request_id": "change-expense-status",
        "data_name": "差戻し済み経費",
        "business_unique_value": "EXP-20260805-0012",
        "test_case_ref": "TC-EXPENSE-01",
        "retain_after_test": True,
    }

    response = client.post(
        "/api/v1/projects/demo/existing-test-data",
        json=payload,
        headers=headers,
    )

    assert response.status_code == 201
    assert response.json()["registration"]["status"] == "candidate"
    call = next(value for name, value in fake.calls if name == "existing-test-data-register")
    assert call == {"project_id": "demo", **payload, "actor": "test-operator"}

    rejected = client.post(
        "/api/v1/projects/demo/existing-test-data",
        json={**payload, "sql": "SELECT * FROM expenses"},
        headers={**headers, "Idempotency-Key": "existing-data-2"},
    )
    assert rejected.status_code == 422
    assert len(
        [value for name, value in fake.calls if name == "existing-test-data-register"]
    ) == 1


def test_fixed_data_identifiers_are_read_only_business_projection() -> None:
    client, _ = client_with_fake()

    response = client.get("/api/v1/projects/demo/fixed-data-identifiers")

    assert response.status_code == 200
    assert response.json() == {
        "project_id": "demo",
        "pending": [],
        "planned": [],
        "frozen": [],
        "pending_count": 0,
        "planned_count": 0,
        "frozen_count": 0,
    }


def test_project_onboarding_routes_expose_preflight_rebuild_and_retry() -> None:
    client, fake = client_with_fake()

    assert client.get("/api/v1/projects/demo/onboarding").json()["onboarding"] == {
        "status": "running",
        "current_stage": "documents",
    }
    assert client.get("/api/v1/projects/demo/preflight").json()["status"] == "ready"
    rescan = client.post(
        "/api/v1/projects/demo/onboarding",
        json={"action": "rescan"},
        headers={
            "X-OperaMind-Actor": "local-user",
            "Idempotency-Key": "project-rescan-1",
        },
    )
    retry = client.post(
        "/api/v1/projects/demo/onboarding/retry",
        headers={
            "X-OperaMind-Actor": "local-user",
            "Idempotency-Key": "project-retry-1",
        },
    )

    assert rescan.status_code == 202
    assert retry.status_code == 202
    assert [name for name, _value in fake.calls] == [
        "command",
        "project-onboarding",
        "command",
        "project-onboarding-retry",
    ]


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


def test_change_request_preserves_each_atomic_business_change_point() -> None:
    client, fake = client_with_fake()
    body = {
        "change_request_id": "change-multi-point",
        "project_id": "demo",
        "requirement_text": (
            "初期状態を「すべて」に変更し、差戻し状態でも検索できるようにする。"
            "検索後は対象件数を表示する。并且选中的状态必须保持。"
        ),
    }

    response = client.post(
        "/api/v1/change-requests",
        json=body,
        headers={
            "X-OperaMind-Actor": "local-user",
            "Idempotency-Key": "request-multi-point",
        },
    )

    assert response.status_code == 201
    submitted = next(value for name, value in fake.calls if name == "submit")
    assert [rule.text for rule in submitted.business_rules] == [
        "初期状態を「すべて」に変更",
        "差戻し状態でも検索できるようにする",
        "検索後は対象件数を表示する",
        "选中的状态必须保持",
    ]
    assert [rule.business_rule_id for rule in submitted.business_rules] == [
        f"change-multi-point-change-point-{position}" for position in range(1, 5)
    ]


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
    assert applied.json()["state"] == "awaiting_copilot"
    assert [name for name, _value in fake.calls] == [
        "command",
        "revision-propose",
        "command",
        "revision-confirm",
    ]


def test_failed_test_data_run_can_be_replayed_from_web(
    monkeypatch: MonkeyPatch,
) -> None:
    client, fake = client_with_fake()
    background_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "operamind.web.routers.change_requests.execute_reserved_test_data_run",
        lambda **values: background_calls.append(values),
    )

    response = client.post(
        "/api/v1/change-requests/change-1/test-data-runs/run-failed-001/rerun",
        json={},
        headers={
            "X-OperaMind-Actor": "local-user",
            "Idempotency-Key": "rerun-1",
        },
    )

    assert response.status_code == 200
    assert response.json()["replay_of_run_id"] == "run-failed-001"
    assert fake.calls[-1] == (
        "test-data-rerun",
        {
            "request_id": "change-1",
            "idempotency_key": "rerun-1",
            "actor": "local-user",
            "replay_of_run_id": "run-failed-001",
        },
    )
    assert background_calls[0]["run_id"] == "run-replayed-001"


def test_openapi_contains_only_six_stage_web_and_token_protected_bridge_routes() -> None:
    client, _ = client_with_fake(bridge_token="bridge-secret")

    paths = set(client.get("/openapi.json").json()["paths"])

    assert paths == {
        "/api/v1/projects",
        "/api/v1/projects/{project_id}",
        "/api/v1/projects/{project_id}/onboarding",
        "/api/v1/projects/{project_id}/document-learning",
        "/api/v1/projects/{project_id}/document-learning/confirm",
        "/api/v1/projects/{project_id}/target-data-profile",
        "/api/v1/projects/{project_id}/data-identity-profiles",
        "/api/v1/projects/{project_id}/existing-test-data",
        "/api/v1/projects/{project_id}/existing-test-data/{registration_id}/confirm",
        "/api/v1/projects/{project_id}/fixed-data-identifiers",
        "/api/v1/projects/{project_id}/onboarding/retry",
        "/api/v1/projects/{project_id}/preflight",
        "/api/v1/change-requests",
        "/api/v1/change-requests/{request_id}/flow",
        "/api/v1/change-requests/{request_id}/confirmations/{checkpoint}",
        "/api/v1/change-requests/{request_id}/test-case-revisions",
        ("/api/v1/change-requests/{request_id}/test-case-revisions/{proposal_id}/confirm"),
        "/api/v1/change-requests/{request_id}/test-data-runs/{run_id}/rerun",
        "/api/v1/change-requests/{request_id}/screenshots/{evidence_id}",
        "/api/v1/local-bridge/tasks/next",
        "/api/v1/local-bridge/confirmations/next",
        "/api/v1/local-bridge/change-requests/{request_id}/confirmations/{checkpoint}",
        "/api/v1/local-bridge/tasks/{coding_task_id}/accept",
        "/api/v1/local-bridge/tasks/{coding_task_id}/resume",
        "/api/v1/local-bridge/tasks/{coding_task_id}/cancel",
        "/api/v1/local-bridge/diagnostics",
    }


def test_web_and_vscode_use_the_same_checkpoint_command() -> None:
    client, fake = client_with_fake(bridge_token="bridge-secret")
    web = client.post(
        "/api/v1/change-requests/change-1/confirmations/requirement",
        headers={
            "X-OperaMind-Actor": "web-user",
            "Idempotency-Key": "web-confirm-1",
        },
        json={"decision": "confirmed"},
    )
    bridge = client.post(
        "/api/v1/local-bridge/change-requests/change-1/confirmations/requirement",
        headers={"Authorization": "Bearer bridge-secret"},
        json={
            "decision": "confirmed",
            "actor": "vscode-user",
            "idempotency_key": "vscode-confirm-1",
        },
    )

    assert web.status_code == 200
    assert bridge.status_code == 200
    decisions = [value for name, value in fake.calls if name == "checkpoint-decision"]
    assert [value["surface"] for value in decisions] == ["web", "vscode_copilot"]
    commands = [value for name, value in fake.calls if name == "command"]
    assert len(commands) == 2
    assert commands[1]["command_scope"] == "change-confirmation:change-1:requirement"
    assert commands[1]["idempotency_key"] == "vscode-confirm-1"
    assert commands[1]["actor"] == "vscode-user"


def test_loopback_bridge_remains_token_protected() -> None:
    client, fake = client_with_fake(bridge_token="bridge-secret")
    params = {
        "workspace_root": "/workspace/linked",
        "consumer_id": "vscode-1",
        "change_request_id": "change-1",
    }

    assert client.get("/api/v1/local-bridge/tasks/next", params=params).status_code == 401
    response = client.get(
        "/api/v1/local-bridge/tasks/next",
        params=params,
        headers={"Authorization": "Bearer bridge-secret"},
    )

    assert response.status_code == 200
    assert fake.calls[-1] == (
        "bridge-next",
        {
            "workspace_root": Path("/workspace/linked"),
            "consumer_id": "vscode-1",
            "change_request_id": "change-1",
        },
    )

    accepted = client.post(
        "/api/v1/local-bridge/tasks/document-learning-1/accept",
        headers={"Authorization": "Bearer bridge-secret"},
        json={
            "workspace_root": "/workspace/linked",
            "consumer_id": "vscode-1",
            "claim_token": "learning-claim-1",
            "accepted_by": "github-copilot",
        },
    )
    assert accepted.status_code == 200
    assert fake.calls[-1] == (
        "bridge-accept",
        {
            "coding_task_id": "document-learning-1",
            "workspace_root": Path("/workspace/linked"),
            "consumer_id": "vscode-1",
            "claim_token": "learning-claim-1",
            "actor": "github-copilot",
        },
    )

    resumed = client.get(
        "/api/v1/local-bridge/tasks/document-learning-1/resume",
        params={
            "workspace_root": "/workspace/linked",
            "consumer_id": "vscode-1",
            "claim_token": "learning-claim-1",
        },
        headers={"Authorization": "Bearer bridge-secret"},
    )
    assert resumed.status_code == 200
    assert fake.calls[-1][1]["claim_token"] == "learning-claim-1"

    confirmation = client.get(
        "/api/v1/local-bridge/confirmations/next",
        params={
            "workspace_root": "/workspace/linked",
            "change_request_id": "change-1",
        },
        headers={"Authorization": "Bearer bridge-secret"},
    )

    assert confirmation.status_code == 200
    assert fake.calls[-1] == (
        "bridge-confirmation-next",
        {
            "workspace_root": Path("/workspace/linked"),
            "change_request_id": "change-1",
        },
    )


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
    assert "重要工程は利用者が確認し、確認後の内部実行範囲" in page


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
    assert "setRequestSubmitting(true)" in script
    assert 'setRequestFormStatus(error.message, "error")' in script
    assert 'api("/api/v1/change-requests"' in script

    assert ".change-request-dialog" in stylesheet
    assert ".request-context-grid" in stylesheet
    assert ".request-writing-guide" in stylesheet
    assert ".request-form-status.error" in stylesheet
    assert "grid-template-columns: 1fr" in stylesheet


def test_project_dialog_reports_background_onboarding_without_blocking_the_page() -> None:
    page = (ROOT / "src/operamind/web/static/index.html").read_text(encoding="utf-8")
    script = (ROOT / "src/operamind/web/static/app.js").read_text(encoding="utf-8")

    assert 'id="projectFormStatus"' in page
    assert 'id="submitProjectButton"' in page
    assert "setProjectSubmitting(true)" in script
    assert "バックグラウンド Onboarding を開始します" in script
    assert "設計書と RAG の準備をバックグラウンドで開始します" in script
    assert 'setProjectFormStatus(error.message, "error")' in script
