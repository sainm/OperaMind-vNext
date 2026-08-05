from __future__ import annotations

import html
import http.server
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import psycopg
import pytest

from operamind.application.existing_test_data import (
    ExistingTestDataRegistrationInput,
    ExistingTestDataRegistrationService,
    ProjectDataIdentityProfile,
)
from operamind.infrastructure.browser import LocalEvidenceStore
from operamind.infrastructure.postgres.existing_test_data_repository import (
    ExistingTestDataRepository,
)
from operamind.infrastructure.test_data import (
    PlaywrightUiTestDataExecutor,
    ProjectSqlTestDataExecutor,
    ReviewedExistingDataObservationResolver,
    SafeHttpTestDataExecutor,
    TargetDataSecretStore,
    default_data_identity_providers,
)
from tests.integration.test_target_data_identity_binding_live import (
    _drop_target_project,
    _prepare_target_project,
    _read_by_number_binding,
)

DATABASE_URL = os.getenv("OPERAMIND_TEST_DATABASE_URL")
pytestmark = pytest.mark.integration


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
@pytest.mark.skipif(
    os.getenv("OPERAMIND_PLAYWRIGHT_EXECUTOR_LIVE") != "1",
    reason="OPERAMIND_PLAYWRIGHT_EXECUTOR_LIVE is not set",
)
@pytest.mark.parametrize("provider_type", ["database", "api", "ui", "hybrid"])
def test_real_existing_data_registration_uses_reviewed_provider_and_human_confirmation(
    tmp_path: Path,
    provider_type: str,
) -> None:
    assert DATABASE_URL is not None
    project_id, schema, target_role = _prepare_target_project(
        database_url=DATABASE_URL,
        secret_root=tmp_path / "secrets",
        binding_factory=lambda target_schema: [
            _read_by_number_binding("read_expense_by_number", target_schema)
        ],
    )
    with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {schema}.expenses (expense_number, status) VALUES (%s, %s)",
            ("EXP-ADOPT-041", "RETURNED"),
        )
        cursor.execute(
            f"INSERT INTO {schema}.expenses (expense_number, status) VALUES (%s, %s)",
            ("EXP-OTHER-999", "APPROVED"),
        )
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), _existing_data_handler(DATABASE_URL, schema)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    store = LocalEvidenceStore(tmp_path / "evidence")
    browser = PlaywrightUiTestDataExecutor(evidence_store=store, browser_channel="chrome")
    executors = {
        "sql": ProjectSqlTestDataExecutor(
            control_database_url=DATABASE_URL,
            evidence_store=store,
            secret_store=TargetDataSecretStore(tmp_path / "secrets"),
        ),
        "http": SafeHttpTestDataExecutor(evidence_store=store),
        "ui": browser,
    }
    profile = _profile(project_id, provider_type)
    registration_id = f"registration-{provider_type}-{project_id}"
    try:
        with psycopg.connect(DATABASE_URL) as connection:
            repository = ExistingTestDataRepository(connection)
            repository.upsert_profile(profile, actor="admin-reviewer")
            service = ExistingTestDataRegistrationService(
                identity_providers=default_data_identity_providers(),
                observation_resolver=ReviewedExistingDataObservationResolver(
                    executors=executors,
                    base_url_by_project=lambda _project_id: (
                        f"http://127.0.0.1:{server.server_port}"
                    ),
                ),
            )
            candidate = service.register(
                ExistingTestDataRegistrationInput(
                    registration_id=registration_id,
                    project_id=project_id,
                    data_name="差戻し済み経費",
                    business_unique_value="EXP-ADOPT-041",
                    test_case_ref="case-existing-expense",
                    retain_after_test=True,
                    requested_by="qa-user",
                    requested_at=datetime.now(UTC),
                ),
                profiles=repository.profiles(project_id),
            )
            assert candidate.status == "candidate", candidate.blocking_reasons
            assert candidate.match_count == 1
            assert candidate.business_summary == {"expense_number": "EXP-ADOPT-041"}
            assert candidate.plan_data_definition is None
            repository.save(candidate)
            confirmed = service.confirm(
                candidate,
                profile=profile,
                actor="qa-user",
                confirmed_at=datetime.now(UTC),
            )
            repository.confirm(confirmed)
            reloaded = repository.get(registration_id)
            assert reloaded is not None
            assert reloaded.status == "confirmed"
            assert reloaded.plan_data_definition is not None
            assert reloaded.plan_data_definition["data_set"]["identity_binding"][
                "binding_mode"
            ] == "adopted"
            assert "password" not in repr(reloaded).casefold()
    finally:
        browser.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM existing_test_data_registrations WHERE project_id = %s",
                (project_id,),
            )
            cursor.execute(
                "DELETE FROM project_data_identity_profiles WHERE project_id = %s",
                (project_id,),
            )
        _drop_target_project(DATABASE_URL, project_id, schema, target_role)


def _profile(project_id: str, provider_type: str) -> ProjectDataIdentityProfile:
    sql_step = _lookup_step(
        "lookup-database",
        1,
        "sql",
        "read_expense_by_number",
        {"expense_number": "{{business_unique_value}}", "status": "RETURNED"},
    )
    http_step = _lookup_step(
        "lookup-api",
        2 if provider_type == "hybrid" else 1,
        "http",
        "GET /api/expense",
        {"query": {"expense_number": "{{business_unique_value}}"}},
        depends_on=["lookup-database"] if provider_type == "hybrid" else [],
    )
    ui_step = _lookup_step(
        "lookup-ui",
        3 if provider_type == "hybrid" else 1,
        "ui",
        None,
        {},
        depends_on=["lookup-api"] if provider_type == "hybrid" else [],
    )
    ui_step.update(
        {
            "screen_ref": "expense-list",
            "ui_action_ref": "observe-existing",
            "operation_scope": "screen",
            "playwright": {
                "action": "goto",
                "path": "/",
                "mask_locators": [],
                "observations": [
                    {
                        "key": "match_count",
                        "kind": "count",
                        "locator": {
                            "by": "css",
                            "value": "[data-expense-number='{{business_unique_value}}']",
                            "exact": True,
                        },
                    },
                    {
                        "key": "expense_number",
                        "kind": "attribute",
                        "locator": {
                            "by": "css",
                            "value": "[data-expense-number='{{business_unique_value}}']",
                            "exact": True,
                        },
                        "attribute_name": "data-observed-expense-number",
                    },
                ],
            },
        }
    )
    steps = {
        "database": (sql_step,),
        "api": (http_step,),
        "ui": (ui_step,),
        "hybrid": (sql_step, http_step, ui_step),
    }[provider_type]
    primary_source = "database" if provider_type in {"database", "hybrid"} else (
        "response" if provider_type == "api" else "ui"
    )
    business_source = "response" if provider_type == "hybrid" else primary_source
    screen_source = "ui" if provider_type == "hybrid" else primary_source
    return ProjectDataIdentityProfile(
        project_id=project_id,
        provider_ref=f"{provider_type}.v1",
        provider_type=provider_type,
        lookup_steps=steps,
        cleanup_steps=(),
        identity_definition={
            "source_step_id": steps[-1]["step_id"],
            "primary_key": _identity_value(primary_source),
            "business_unique_keys": [_identity_value(business_source, dom=True)],
            "screen_key": {
                **_identity_value(screen_source, dom=True),
                "locator_template": {
                    "by": "css",
                    "value": "[data-expense-number='{{value}}']",
                    "exact": True,
                },
            },
            "match_count": {
                "source": primary_source,
                "path": _path(primary_source, count=True),
            },
        },
        business_summary_fields=("expense_number",),
    )


def _lookup_step(
    step_id: str,
    sequence: int,
    channel: str,
    target: str | None,
    inputs: dict[str, object],
    *,
    depends_on: list[str] | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "step_id": step_id,
        "sequence": sequence,
        "channel": channel,
        "business_action": "既存データを確認する",
        "inputs": inputs,
        "depends_on": depends_on or [],
        "output_bindings": [],
        "postconditions": [
            {
                "assertion_id": f"{step_id}-unique",
                "observe_via": {"sql": "database", "http": "response", "ui": "ui"}[
                    channel
                ],
                "subject": {
                    "sql": "row_count",
                    "http": "count",
                    "ui": "match_count",
                }[channel],
                "operator": "count_equals",
                "expected": 1,
            }
        ],
    }
    if target is not None:
        value["target"] = target
    return value


def _identity_value(source: str, *, dom: bool = False) -> dict[str, object]:
    value: dict[str, object] = {
        "name": "expense_number",
        "source": source,
        "path": _path(source, count=False),
    }
    if dom:
        value["dom_observation"] = {
            "kind": "attribute",
            "attribute_name": "data-observed-expense-number",
        }
    return value


def _path(source: str, *, count: bool) -> str:
    return {
        "database": "row_count" if count else "rows[0].expense_number",
        "response": "count" if count else "record.expense_number",
        "ui": "match_count" if count else "expense_number",
    }[source]


def _existing_data_handler(
    database_url: str, schema: str
) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            value = parse_qs(parsed.query).get("expense_number", [""])[0]
            with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
                if parsed.path == "/api/expense":
                    cursor.execute(
                        f"SELECT expense_number, status FROM {schema}.expenses "
                        "WHERE expense_number = %s",
                        (value,),
                    )
                    rows = cursor.fetchall()
                    payload = {
                        "count": len(rows),
                        "record": (
                            {"expense_number": rows[0][0], "status": rows[0][1]}
                            if len(rows) == 1
                            else None
                        ),
                    }
                    body = json.dumps(payload).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                else:
                    cursor.execute(
                        f"SELECT expense_number, status FROM {schema}.expenses ORDER BY id"
                    )
                    rows = cursor.fetchall()
                    body = (
                        "<!doctype html><html><head><title>Expense list</title></head><body>"
                        + "".join(
                            f"<div data-expense-number='{html.escape(str(number))}' "
                            f"data-observed-expense-number='{html.escape(str(number))}'>"
                            f"{html.escape(str(number))} {html.escape(str(status))}</div>"
                            for number, status in rows
                        )
                        + "</body></html>"
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    return Handler
