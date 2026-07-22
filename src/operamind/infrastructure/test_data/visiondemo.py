"""Restricted TestDataPlan bindings for a deployed VisionDemo revision."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from operamind.application.test_data_execution import (
    TestDataChannelExecutor,
    TestDataExecutionRequest,
    TestDataStepExecution,
)
from operamind.infrastructure.browser import LocalEvidenceStore
from operamind.infrastructure.test_data.executors import (
    BoundFixtureTestDataExecutor,
    BoundSqlTestDataExecutor,
    BoundUiTestDataExecutor,
    HttpResponse,
    HttpTransport,
    SafeHttpTestDataExecutor,
    UiDataActionResult,
    UrllibHttpTransport,
)

_PROFILE = "visiondemo-local"
_SEED_FIXTURE = "VisionDemo/src/main/resources/data.sql"
_RUNTIME_IDENTITIES = "visiondemo.runtime-identities"
_EXPENSE_QUERY = "visiondemo.expense-by-id"
_CLEANUP_QUERY = "visiondemo.cleanup-by-ids"


@dataclass(frozen=True, slots=True)
class VisionDemoDeploymentConfig:
    """Explicit local target configuration; no raw SQL or arbitrary browser action is accepted."""

    base_url: str
    jdbc_url: str
    h2_jar: Path
    java_executable: Path

    @classmethod
    def from_environment(cls) -> VisionDemoDeploymentConfig:
        base_url = os.getenv("OPERAMIND_VISIONDEMO_BASE_URL", "").rstrip("/")
        jdbc_url = os.getenv("OPERAMIND_VISIONDEMO_JDBC_URL", "")
        h2_jar = Path(os.getenv("OPERAMIND_VISIONDEMO_H2_JAR", ""))
        java_executable = Path(os.getenv("OPERAMIND_VISIONDEMO_JAVA", ""))
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("VisionDemo base URL must be an origin")
        jdbc_prefix = "jdbc:h2:file:"
        jdbc_file = jdbc_url.removeprefix(jdbc_prefix).split(";", 1)[0]
        tmp_root = Path("/tmp").resolve()
        if (
            not jdbc_url.startswith(jdbc_prefix)
            or not Path(jdbc_file).resolve().is_relative_to(tmp_root)
            or Path(jdbc_file).resolve() == tmp_root
            or "AUTO_SERVER=TRUE" not in jdbc_url.split(";")[1:]
        ):
            raise ValueError("VisionDemo JDBC URL must be an AUTO_SERVER local /tmp H2 database")
        if not h2_jar.is_file() or not java_executable.is_file():
            raise ValueError("VisionDemo H2 jar and Java executable must exist")
        return cls(base_url, jdbc_url, h2_jar.resolve(), java_executable.resolve())


def visiondemo_test_data_executor_factory(
    repository_root: Path,
) -> Mapping[str, TestDataChannelExecutor]:
    """Build the complete named binding set for one configured VisionDemo deployment."""

    config = VisionDemoDeploymentConfig.from_environment()
    store = LocalEvidenceStore(repository_root / "readiness" / "evidence" / "test-data")
    transport = VisionDemoReviewedHttpTransport(UrllibHttpTransport())
    http = SafeHttpTestDataExecutor(evidence_store=store, transport=transport)
    return {
        "http": VisionDemoCanonicalHttpExecutor(http),
        "fixture": BoundFixtureTestDataExecutor(
            evidence_store=store,
            bindings={
                _SEED_FIXTURE: _fixture_binding(config),
                _RUNTIME_IDENTITIES: _runtime_identity_binding,
            },
        ),
        "sql": BoundSqlTestDataExecutor(
            evidence_store=store,
            bindings={
                _EXPENSE_QUERY: _expense_query_binding(config),
                _CLEANUP_QUERY: _cleanup_query_binding(config),
            },
        ),
        "ui": BoundUiTestDataExecutor(
            evidence_store=store,
            bindings={
                ("employee-list", "search-created-employee"): _employee_ui_binding,
                ("expense-list", "search-created-expense"): _expense_ui_binding,
            },
        ),
    }


def configured_visiondemo_profile() -> str | None:
    value = os.getenv("OPERAMIND_TEST_DATA_BINDING_PROFILE")
    if value is None or not value.strip():
        return None
    if value != _PROFILE:
        raise ValueError(f"Unsupported Test data binding profile: {value}")
    return value


class VisionDemoReviewedHttpTransport:
    """Adapt only the reviewed logical returned-expense recipe to VisionDemo's API envelope."""

    def __init__(self, delegate: HttpTransport) -> None:
        self._delegate = delegate

    def send(
        self,
        *,
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HttpResponse:
        parsed = urlsplit(url)
        if method == "POST" and parsed.path == "/expense/api/save":
            body = _adapt_returned_expense(body)
        return self._delegate.send(
            method=method,
            url=url,
            body=body,
            headers=headers,
            timeout_seconds=timeout_seconds,
        )


class VisionDemoCanonicalHttpExecutor:
    """Normalize the one legacy reviewed recipe before the safe HTTP executor."""

    def __init__(self, delegate: SafeHttpTestDataExecutor) -> None:
        self._delegate = delegate

    def execute(
        self,
        *,
        request: TestDataExecutionRequest,
        flow_id: str,
        step: Mapping[str, object],
        resolved_inputs: Mapping[str, object],
        variables: Mapping[str, object],
        phase: str,
    ) -> TestDataStepExecution:
        normalized = dict(resolved_inputs)
        if (
            step.get("target") == "POST /expense/api/save"
            and "json" not in normalized
            and set(normalized) == {"expenseNo", "status"}
        ):
            normalized = {
                "method": "POST",
                "path": "/expense/api/save",
                "json": normalized,
            }
        return self._delegate.execute(
            request=request,
            flow_id=flow_id,
            step=step,
            resolved_inputs=normalized,
            variables=variables,
            phase=phase,
        )


def _adapt_returned_expense(body: bytes | None) -> bytes:
    if body is None:
        raise ValueError("VisionDemo expense creation requires JSON")
    raw: object = json.loads(body)
    if not isinstance(raw, dict):
        raise ValueError("VisionDemo expense creation JSON must be an object")
    if "expense" in raw:
        return body
    if set(raw) != {"expenseNo", "status"}:
        raise ValueError("VisionDemo logical expense recipe has unapproved fields")
    expense_no = raw.get("expenseNo")
    status = raw.get("status")
    if (
        not isinstance(expense_no, str)
        or not expense_no.startswith("EXP-OM-")
        or status != "差戻し"
    ):
        raise ValueError("VisionDemo logical expense recipe is outside the reviewed dataset")
    adapted = {
        "expense": {
            "expenseNo": expense_no,
            "employee": {"id": 2},
            "totalAmount": 4321,
            "status": status,
            "applyDate": "2026-07-19",
            "description": "OperaMind target deployment E2E",
        },
        "details": [
            {
                "lineNo": 1,
                "accountItem": "交通費",
                "amount": 4321,
                "expenseDate": "2026-07-19",
                "description": "自動検証データ",
            }
        ],
    }
    return json.dumps(adapted, ensure_ascii=False, separators=(",", ":")).encode()


def _fixture_binding(
    config: VisionDemoDeploymentConfig,
) -> Callable[[Mapping[str, object]], Mapping[str, object]]:
    def load(inputs: Mapping[str, object]) -> Mapping[str, object]:
        expected = _positive_int(inputs, "expected_expense_count")
        payload = _get_json(f"{config.base_url}/expense/api/search?status=&page=0&size=100")
        actual = payload.get("totalElements")
        if actual != expected:
            raise AssertionError("VisionDemo default seed count differs from the reviewed fixture")
        return {"expected_expense_count": actual}

    return load


def _runtime_identity_binding(inputs: Mapping[str, object]) -> Mapping[str, object]:
    if inputs:
        raise ValueError("VisionDemo runtime identity fixture accepts no inputs")
    suffix = uuid.uuid4().hex[:8].upper()
    return {
        "employee_no": f"OM-{suffix}",
        "employee_name": f"連携試験 {suffix}",
        "employee_email": f"operamind-{suffix.lower()}@example.invalid",
        "expense_no": f"EXP-OM-{suffix}",
    }


def _expense_query_binding(
    config: VisionDemoDeploymentConfig,
) -> Callable[[Mapping[str, object]], Mapping[str, object]]:
    def query(inputs: Mapping[str, object]) -> Mapping[str, object]:
        expense_id = _positive_int(inputs, "expense_id")
        employee_id = _positive_int(inputs, "employee_id")
        count = _h2_integer(
            config,
            "SELECT COUNT(*) AS RESULT FROM expenses "
            f"WHERE id = {expense_id} AND employee_id = {employee_id}",
        )
        return {"expense_count": count}

    return query


def _cleanup_query_binding(
    config: VisionDemoDeploymentConfig,
) -> Callable[[Mapping[str, object]], Mapping[str, object]]:
    def query(inputs: Mapping[str, object]) -> Mapping[str, object]:
        expense_id = _positive_int(inputs, "expense_id")
        employee_id = _positive_int(inputs, "employee_id")
        expense_count = _h2_integer(
            config, f"SELECT COUNT(*) AS RESULT FROM expenses WHERE id = {expense_id}"
        )
        employee_count = _h2_integer(
            config, f"SELECT COUNT(*) AS RESULT FROM employees WHERE id = {employee_id}"
        )
        return {"expense_count": expense_count, "employee_count": employee_count}

    return query


def _employee_ui_binding(
    request: object,
    inputs: Mapping[str, object],
    variables: Mapping[str, object],
) -> UiDataActionResult:
    del variables
    base_url = _request_base_url(request)
    employee_name = _required_text(inputs, "employee_name")
    employee_no = _required_text(inputs, "employee_no")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel="chrome")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.goto(
                f"{base_url}/popup?fragment=%2Femployee%2Fpage%3Fview%3Dlist",
                wait_until="domcontentloaded",
            )
            page.locator("#emp-search-name").fill(employee_name)
            page.locator("#emp-search-btn").click()
            page.get_by_text(employee_no, exact=True).wait_for(state="visible")
            screenshot = page.screenshot(full_page=True)
            return UiDataActionResult(
                observations={
                    "employee": {"employeeNo": employee_no, "name": employee_name}
                },
                screenshot=screenshot,
            )
        finally:
            browser.close()


def _expense_ui_binding(
    request: object,
    inputs: Mapping[str, object],
    variables: Mapping[str, object],
) -> UiDataActionResult:
    del variables
    base_url = _request_base_url(request)
    expense_no = _required_text(inputs, "expense_no")
    employee_name = _required_text(inputs, "employee_name")
    status = _required_text(inputs, "status")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel="chrome")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.goto(
                f"{base_url}/popup?fragment=%2Fexpense%2Fpage%3Fview%3Dlist",
                wait_until="domcontentloaded",
            )
            page.locator("#expense-search-status").select_option(status)
            page.locator("#expense-search-btn").click()
            page.get_by_text(expense_no, exact=True).wait_for(state="visible")
            page.get_by_text(employee_name, exact=True).wait_for(state="visible")
            screenshot = page.screenshot(full_page=True)
            return UiDataActionResult(
                observations={
                    "matching_row": {
                        "expenseNo": expense_no,
                        "employee_name": employee_name,
                        "status": status,
                    }
                },
                screenshot=screenshot,
            )
        finally:
            browser.close()


def _get_json(url: str) -> dict[str, object]:
    response = UrllibHttpTransport().send(
        method="GET",
        url=url,
        body=None,
        headers={"Accept": "application/json"},
        timeout_seconds=20,
    )
    if response.status_code != 200:
        raise OSError("VisionDemo fixture probe failed")
    raw: object = json.loads(response.body)
    if not isinstance(raw, dict):
        raise ValueError("VisionDemo fixture probe returned non-object JSON")
    return raw


def _h2_integer(config: VisionDemoDeploymentConfig, sql: str) -> int:
    completed = subprocess.run(
        [
            str(config.java_executable),
            "-cp",
            str(config.h2_jar),
            "org.h2.tools.Shell",
            "-url",
            config.jdbc_url,
            "-user",
            "sa",
            "-password",
            "",
            "-sql",
            sql,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if completed.returncode != 0:
        raise OSError("VisionDemo named SQL query failed")
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if len(lines) < 2 or lines[0] != "RESULT" or not lines[1].isdigit():
        raise OSError("VisionDemo named SQL query returned an unexpected shape")
    return int(lines[1])


def _positive_int(values: Mapping[str, object], key: str) -> int:
    value = values.get(key)
    if isinstance(value, bool):
        raise ValueError(f"VisionDemo {key} must be a positive integer")
    try:
        result = int(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"VisionDemo {key} must be a positive integer") from error
    if result < 1:
        raise ValueError(f"VisionDemo {key} must be a positive integer")
    return result


def _required_text(values: Mapping[str, object], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"VisionDemo {key} must be non-blank")
    return value


def _request_base_url(request: object) -> str:
    value = getattr(request, "base_url", None)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("VisionDemo UI binding requires the approved base URL")
    return value.rstrip("/")
