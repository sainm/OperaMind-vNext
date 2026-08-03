from __future__ import annotations

import json
from dataclasses import dataclass
from http.client import HTTPMessage
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request

import pytest

from operamind.application.test_data_execution import (
    TestDataExecutionRequest as DataExecutionRequest,
)
from operamind.infrastructure.browser import LocalEvidenceStore
from operamind.infrastructure.test_data import (
    BoundFixtureTestDataExecutor,
    BoundSqlTestDataExecutor,
    BoundUiTestDataExecutor,
    ComputerUseActionResult,
    HttpResponse,
    PlaywrightActionResult,
    PlaywrightCapabilityError,
    PlaywrightUiTestDataExecutor,
    SafeHttpTestDataExecutor,
    UiDataActionResult,
)


@dataclass
class FakeTransport:
    response: HttpResponse
    call: tuple[str, str, bytes | None] | None = None

    def send(
        self,
        *,
        method: str,
        url: str,
        body: bytes | None,
        headers: object,
        timeout_seconds: float,
    ) -> HttpResponse:
        del headers, timeout_seconds
        self.call = (method, url, body)
        return self.response


@dataclass
class FakePlaywrightSession:
    result: PlaywrightActionResult
    call: tuple[str, object] | None = None
    observe_call: tuple[str, object] | None = None
    closed: bool = False

    def execute(self, *, base_url: str, action: object) -> PlaywrightActionResult:
        self.call = (base_url, action)
        return self.result

    def observe(
        self, *, base_url: str, observations: object, mask_locators: object
    ) -> PlaywrightActionResult:
        self.observe_call = (base_url, (observations, mask_locators))
        return self.result

    def close(self) -> None:
        self.closed = True


@dataclass
class FailingPlaywrightSession:
    observation_result: PlaywrightActionResult | None = None
    observe_call: tuple[str, object] | None = None
    closed: bool = False

    def execute(self, *, base_url: str, action: object) -> PlaywrightActionResult:
        del base_url, action
        raise PlaywrightCapabilityError("locator unavailable")

    def observe(
        self, *, base_url: str, observations: object, mask_locators: object
    ) -> PlaywrightActionResult:
        self.observe_call = (base_url, (observations, mask_locators))
        if self.observation_result is None:
            raise AssertionError("No deterministic Playwright observation was configured")
        return self.observation_result

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeComputerUseSession:
    result: ComputerUseActionResult
    call: tuple[str, str, int, object] | None = None
    closed: bool = False

    def execute(
        self,
        *,
        base_url: str,
        objective: str,
        max_actions: int,
        observations: object,
    ) -> ComputerUseActionResult:
        self.call = (base_url, objective, max_actions, observations)
        return self.result

    def close(self) -> None:
        self.closed = True


class FakeLocator:
    def __init__(self, owner: Any | None = None) -> None:
        self.owner = owner
        self.calls: list[tuple[str, object]] = []
        self.attributes: dict[str, str] = {}
        self.count_value = 1

    def count(self) -> int:
        return self.count_value

    def _record(self, name: str, value: object = None, **options: object) -> None:
        self.calls.append((name, value if value is not None else options))

    def click(self, **options: object) -> None:
        self._record("click", **options)

    def dblclick(self, **options: object) -> None:
        self._record("dblclick", **options)

    def fill(self, value: object, **options: object) -> None:
        self._record("fill", value, **options)

    def type(self, value: object, **options: object) -> None:
        self._record("type", value, **options)

    def clear(self, **options: object) -> None:
        self._record("clear", **options)

    def select_option(self, value: object, **options: object) -> None:
        self._record("select_option", value, **options)

    def check(self, **options: object) -> None:
        self._record("check", **options)

    def uncheck(self, **options: object) -> None:
        self._record("uncheck", **options)

    def press(self, value: object, **options: object) -> None:
        self._record("press", value, **options)

    def hover(self, **options: object) -> None:
        self._record("hover", **options)

    def focus(self, **options: object) -> None:
        self._record("focus", **options)

    def blur(self, **options: object) -> None:
        self._record("blur", **options)

    def scroll_into_view_if_needed(self, **options: object) -> None:
        self._record("scroll_into_view_if_needed", **options)

    def drag_to(self, target: object, **options: object) -> None:
        self._record("drag_to", target, **options)

    def wait_for(self, **options: object) -> None:
        self._record("wait_for", **options)

    def inner_text(self) -> str:
        return "text"

    def is_visible(self) -> bool:
        return True

    def is_enabled(self) -> bool:
        return True

    def is_checked(self) -> bool:
        return False

    def input_value(self) -> str:
        return "value"

    def get_attribute(self, name: str) -> str:
        return self.attributes.get(name, f"attribute:{name}")

    def element_handle(self) -> FakeLocator:
        return self

    def content_frame(self) -> Any | None:
        return self.owner.frame_scope if self.owner is not None else None


class FakeFrame:
    def __init__(self, owner: Any) -> None:
        self.owner = owner

    @property
    def url(self) -> str:
        return str(self.owner.frame_url)

    def locator(self, _value: str) -> FakeLocator:
        return self.owner.locator_value

    def get_by_placeholder(self, _value: str, *, exact: bool) -> FakeLocator:
        self.owner.calls.append(("placeholder", exact))
        return self.owner.locator_value


class FakePage:
    def __init__(self) -> None:
        self.url = "http://127.0.0.1:8080/current"
        self.frame_url = "http://127.0.0.1:8080/legacy-frame"
        self.frame_scope = FakeFrame(self)
        self.locator_value = FakeLocator(self)
        self.calls: list[tuple[str, object]] = []
        self.frame: str | None = None
        self.screenshot_options: dict[str, object] = {}

    def locator(self, value: str) -> FakeLocator:
        if "iframe" in value:
            self.frame = value
        return self.locator_value

    def get_by_placeholder(self, _value: str, *, exact: bool) -> FakeLocator:
        self.calls.append(("placeholder", exact))
        return self.locator_value

    def goto(self, value: str, **options: object) -> None:
        self.url = value
        self.calls.append(("goto", options))

    def reload(self, **options: object) -> None:
        self.calls.append(("reload", options))

    def go_back(self, **options: object) -> None:
        self.calls.append(("go_back", options))

    def go_forward(self, **options: object) -> None:
        self.calls.append(("go_forward", options))

    def wait_for_url(self, value: str, **options: object) -> None:
        self.url = value
        self.calls.append(("wait_for_url", options))

    def wait_for_load_state(self, **options: object) -> None:
        self.calls.append(("wait_for_load_state", options))

    def title(self) -> str:
        return "Fake page"

    def screenshot(self, **options: object) -> bytes:
        self.screenshot_options = options
        return b"screenshot"


def test_http_executor_uses_bound_origin_and_records_sanitized_evidence(
    tmp_path: Path,
) -> None:
    transport = FakeTransport(
        HttpResponse(
            201,
            {"Content-Type": "application/json"},
            b'{"id":91,"expenseNo":"EXP-001"}',
            "http://127.0.0.1:8080/expense/api/save?trace=test",
        )
    )
    executor = SafeHttpTestDataExecutor(
        evidence_store=LocalEvidenceStore(tmp_path), transport=transport
    )

    result = executor.execute(
        request=_request(),
        flow_id="expense-flow",
        step={"step_id": "create", "target": "POST /expense/api/save"},
        resolved_inputs={
            "method": "POST",
            "path": "/expense/api/save",
            "query": {"trace": "test"},
            "json": {"expenseNo": "EXP-001", "token": "top-secret"},
        },
        variables={},
        phase="setup",
    )

    assert transport.call == (
        "POST",
        "http://127.0.0.1:8080/expense/api/save?trace=test",
        b'{"expenseNo":"EXP-001","token":"top-secret"}',
    )
    assert result.source_values["response"] == {"id": 91, "expenseNo": "EXP-001"}
    assert result.failure_reason is None
    assert [value.evidence_type for value in result.evidence] == ["request", "response"]
    request_file = next(tmp_path.rglob("td-request-*.json"))
    assert "top-secret" not in request_file.read_text(encoding="utf-8")


def test_http_executor_preserves_project_context_path(tmp_path: Path) -> None:
    transport = FakeTransport(
        HttpResponse(
            200,
            {"Content-Type": "application/json"},
            b"{}",
            "http://127.0.0.1:8080/expense-app/api/status",
        )
    )
    executor = SafeHttpTestDataExecutor(
        evidence_store=LocalEvidenceStore(tmp_path), transport=transport
    )
    request = DataExecutionRequest(
        execution_result_id="result-context",
        run_id="run-context",
        project_id="visiondemo",
        base_url="http://127.0.0.1:8080/expense-app",
    )

    executor.execute(
        request=request,
        flow_id="expense-flow",
        step={"step_id": "status", "target": "GET /api/status"},
        resolved_inputs={"method": "GET", "path": "/api/status"},
        variables={},
        phase="setup",
    )

    assert transport.call is not None
    assert transport.call[1] == "http://127.0.0.1:8080/expense-app/api/status"


@pytest.mark.parametrize(
    "path",
    ["/../admin", "/%2e%2e/admin", "/%252e%252e/admin", "/safe\\..\\admin"],
)
def test_http_executor_rejects_paths_that_escape_project_context(tmp_path: Path, path: str) -> None:
    executor = SafeHttpTestDataExecutor(
        evidence_store=LocalEvidenceStore(tmp_path),
        transport=FakeTransport(HttpResponse(200, {}, b"{}", "")),
    )

    with pytest.raises(ValueError, match="dot segments or backslashes"):
        executor.execute(
            request=DataExecutionRequest(
                execution_result_id="result-escape",
                run_id="run-escape",
                project_id="visiondemo",
                base_url="http://127.0.0.1:8080/expense-app",
            ),
            flow_id="expense-flow",
            step={"step_id": "escape", "target": f"GET {path}"},
            resolved_inputs={"method": "GET", "path": path},
            variables={},
            phase="setup",
        )


def test_http_executor_scopes_evidence_identity_to_each_run(tmp_path: Path) -> None:
    transport = FakeTransport(
        HttpResponse(200, {"Content-Type": "application/json"}, b"{}", "http://127.0.0.1:8080/api")
    )
    executor = SafeHttpTestDataExecutor(
        evidence_store=LocalEvidenceStore(tmp_path), transport=transport
    )
    evidence_ids: list[str] = []

    for run_id in ("run-original", "run-replay"):
        result = executor.execute(
            request=DataExecutionRequest(
                execution_result_id=f"result-{run_id}",
                run_id=run_id,
                project_id="visiondemo",
                base_url="http://127.0.0.1:8080",
            ),
            flow_id="expense-flow",
            step={"step_id": "lookup", "target": "GET /api"},
            resolved_inputs={"method": "GET", "path": "/api"},
            variables={},
            phase="setup",
        )
        evidence_ids.extend(value.evidence_id for value in result.evidence)

    assert len(evidence_ids) == len(set(evidence_ids)) == 4


def test_http_executor_rejects_a_cross_origin_final_url(tmp_path: Path) -> None:
    executor = SafeHttpTestDataExecutor(
        evidence_store=LocalEvidenceStore(tmp_path),
        transport=FakeTransport(
            HttpResponse(
                200,
                {},
                b"{}",
                final_url="http://127.0.0.1:8081/redirected",
            )
        ),
    )

    with pytest.raises(OSError, match="redirect escaped the approved origin"):
        executor.execute(
            request=_request(),
            flow_id="expense-flow",
            step={"step_id": "create", "target": "POST /expense/api/save"},
            resolved_inputs={"method": "POST", "path": "/expense/api/save"},
            variables={},
            phase="setup",
        )


def test_same_origin_redirect_handler_rejects_cross_origin_redirect() -> None:
    from operamind.infrastructure.test_data.executors import _SameOriginRedirectHandler

    handler = _SameOriginRedirectHandler()
    with pytest.raises(URLError, match="redirect escaped the approved origin"):
        handler.redirect_request(
            Request("http://127.0.0.1:8080/start"),
            BytesIO(),
            302,
            "Found",
            HTTPMessage(),
            "http://127.0.0.1:8081/redirected",
        )

    redirected = handler.redirect_request(
        Request("http://127.0.0.1:8080/start"),
        BytesIO(),
        302,
        "Found",
        HTTPMessage(),
        "http://127.0.0.1:8080/redirected",
    )
    assert redirected is not None
    assert redirected.full_url == "http://127.0.0.1:8080/redirected"


def test_http_executor_rejects_target_drift_and_retains_non_success_observation(
    tmp_path: Path,
) -> None:
    executor = SafeHttpTestDataExecutor(
        evidence_store=LocalEvidenceStore(tmp_path),
        transport=FakeTransport(
            HttpResponse(
                409,
                {},
                b'{"error":"duplicate"}',
                "http://127.0.0.1:8080/expense/api/save",
            )
        ),
    )
    with pytest.raises(ValueError, match="differ from the reviewed target"):
        executor.execute(
            request=_request(),
            flow_id="expense-flow",
            step={"step_id": "create", "target": "POST /expense/api/save"},
            resolved_inputs={"method": "POST", "path": "/admin/delete"},
            variables={},
            phase="setup",
        )

    result = executor.execute(
        request=_request(),
        flow_id="expense-flow",
        step={"step_id": "create", "target": "POST /expense/api/save"},
        resolved_inputs={"method": "POST", "path": "/expense/api/save"},
        variables={},
        phase="setup",
    )
    assert result.failure_reason == "HTTP Test data request returned status 409"
    assert len(result.evidence) == 2


def test_bound_fixture_sql_and_ui_executors_use_only_registered_bindings(
    tmp_path: Path,
) -> None:
    store = LocalEvidenceStore(tmp_path)
    fixture = BoundFixtureTestDataExecutor(
        evidence_store=store,
        bindings={"default-seed": lambda inputs: {"count": inputs["expected_count"]}},
    )
    sql = BoundSqlTestDataExecutor(
        evidence_store=store,
        bindings={"expense-by-id": lambda inputs: {"expense": {"id": inputs["id"]}}},
    )
    ui = BoundUiTestDataExecutor(
        evidence_store=store,
        bindings={
            ("expense-list", "search-expense"): lambda request, inputs, variables: (
                UiDataActionResult(
                    observations={"visible_id": inputs["id"]},
                    screenshot=b"\x89PNG\r\n\x1a\nfixture",
                )
            )
        },
    )

    fixture_result = fixture.execute(
        request=_request(),
        flow_id="flow-fixture",
        step={"step_id": "load", "target": "default-seed"},
        resolved_inputs={"expected_count": 4},
        variables={},
        phase="setup",
    )
    sql_result = sql.execute(
        request=_request(),
        flow_id="flow-sql",
        step={"step_id": "query", "target": "expense-by-id"},
        resolved_inputs={"id": 91},
        variables={},
        phase="setup",
    )
    ui_result = ui.execute(
        request=_request(),
        flow_id="flow-ui",
        step={
            "step_id": "search",
            "screen_ref": "expense-list",
            "ui_action_ref": "search-expense",
        },
        resolved_inputs={"id": 91},
        variables={"expense_id": 91},
        phase="setup",
    )

    assert fixture_result.source_values == {"fixture": {"count": 4}}
    assert sql_result.source_values == {"database": {"expense": {"id": 91}}}
    assert ui_result.source_values == {"ui": {"visible_id": 91}}
    assert [value.evidence_type for value in ui_result.evidence] == [
        "step_log",
        "screenshot",
    ]
    with pytest.raises(ValueError, match="no approved query binding"):
        sql.execute(
            request=_request(),
            flow_id="flow-sql",
            step={"step_id": "raw", "target": "DELETE FROM expenses"},
            resolved_inputs={},
            variables={},
            phase="setup",
        )


def test_playwright_executor_runs_reviewed_action_and_records_real_ui_evidence(
    tmp_path: Path,
) -> None:
    action = {
        "action": "goto",
        "path": "/expense",
        "mask_locators": [],
        "observations": [{"key": "heading", "kind": "text"}],
    }
    session = FakePlaywrightSession(
        PlaywrightActionResult(
            observations={"url": "http://127.0.0.1:8080/expense", "heading": "経費一覧"},
            screenshot=b"\x89PNG\r\n\x1a\nplaywright",
        )
    )
    executor = PlaywrightUiTestDataExecutor(
        evidence_store=LocalEvidenceStore(tmp_path),
        session_factory=lambda: session,
    )

    result = executor.execute(
        request=_request(),
        flow_id="flow-ui",
        step={
            "step_id": "open-expense",
            "screen_ref": "expense-list",
            "ui_action_ref": "open",
            "playwright": action,
        },
        resolved_inputs={},
        variables={},
        phase="setup",
    )
    executor.close()

    assert session.call == ("http://127.0.0.1:8080", action)
    assert session.closed is True
    assert result.source_values["ui"] == {
        "url": "http://127.0.0.1:8080/expense",
        "heading": "経費一覧",
    }
    assert [item.evidence_type for item in result.evidence] == ["step_log", "screenshot"]
    assert next(tmp_path.rglob("td-screenshot-*.png")).read_bytes().startswith(b"\x89PNG")


def test_playwright_executor_uses_reviewed_computer_use_only_for_capability_gap(
    tmp_path: Path,
) -> None:
    playwright = FailingPlaywrightSession(
        observation_result=PlaywrightActionResult(
            observations={
                "url": "http://127.0.0.1:8080/expense",
                "result": "差戻し申請",
            },
            screenshot=b"\x89PNG\r\n\x1a\nplaywright-readback",
        )
    )
    computer_use = FakeComputerUseSession(
        ComputerUseActionResult(
            action_kinds=("observe", "click"),
        )
    )
    executor = PlaywrightUiTestDataExecutor(
        evidence_store=LocalEvidenceStore(tmp_path),
        session_factory=lambda: playwright,
        computer_use_session_factory=lambda session: (
            computer_use if session is playwright else pytest.fail("wrong Playwright session")
        ),
    )

    result = executor.execute(
        request=_request(),
        flow_id="flow-ui",
        step={
            "step_id": "canvas-filter",
            "screen_ref": "expense-list",
            "ui_action_ref": "canvas-filter",
            "playwright": {
                "action": "click",
                "locator": {"by": "css", "value": "canvas"},
                "mask_locators": [],
                "observations": [],
            },
            "computer_use_fallback": {
                "reason": "canvas",
                "objective": "画面上で差戻し状態を選択する",
                "max_actions": 4,
                "requires_confirmation": True,
                "observations": [
                    {"key": "result", "kind": "text", "locator": {"by": "css", "value": "#result"}}
                ],
            },
        },
        resolved_inputs={},
        variables={},
        phase="setup",
    )
    executor.close()

    assert result.source_values["ui"]["result"] == "差戻し申請"  # type: ignore[index]
    assert computer_use.call is not None
    assert computer_use.call[:3] == (
        "http://127.0.0.1:8080",
        "画面上で差戻し状態を選択する",
        4,
    )
    assert playwright.observe_call is not None
    assert playwright.observe_call[0] == "http://127.0.0.1:8080"
    assert playwright.closed is True
    assert computer_use.closed is True
    log = json.loads(next(tmp_path.rglob("td-step_log-*.json")).read_text(encoding="utf-8"))
    assert log["driver"] == "computer_use"
    assert log["fallback_reason"] == "canvas"
    assert log["computer_use_action_kinds"] == ["observe", "click"]


def test_playwright_executor_blocks_reviewed_fallback_without_ai_provider(
    tmp_path: Path,
) -> None:
    executor = PlaywrightUiTestDataExecutor(
        evidence_store=LocalEvidenceStore(tmp_path),
        session_factory=FailingPlaywrightSession,
    )

    with pytest.raises(ValueError, match="no provider is configured"):
        executor.execute(
            request=_request(),
            flow_id="flow-ui",
            step={
                "step_id": "canvas-filter",
                "screen_ref": "expense-list",
                "ui_action_ref": "canvas-filter",
                "playwright": {
                    "action": "click",
                    "mask_locators": [],
                    "observations": [],
                },
                "computer_use_fallback": {
                    "reason": "canvas",
                    "objective": "画面上で差戻し状態を選択する",
                    "max_actions": 4,
                    "requires_confirmation": True,
                    "observations": [],
                },
            },
            resolved_inputs={},
            variables={},
            phase="setup",
        )


@pytest.mark.parametrize(
    ("action", "expected_method"),
    [
        ({"action": "double_click"}, "dblclick"),
        ({"action": "type", "value": "追加"}, "type"),
        ({"action": "clear"}, "clear"),
        ({"action": "hover"}, "hover"),
        ({"action": "focus"}, "focus"),
        ({"action": "blur"}, "blur"),
        ({"action": "scroll_into_view"}, "scroll_into_view_if_needed"),
        (
            {
                "action": "drag_to",
                "target_locator": {"by": "css", "value": "#drop-target"},
            },
            "drag_to",
        ),
    ],
)
def test_sync_playwright_session_supports_extended_locator_actions(
    action: dict[str, object], expected_method: str
) -> None:
    from operamind.infrastructure.test_data.executors import _SyncPlaywrightSession

    page = FakePage()
    session: Any = object.__new__(_SyncPlaywrightSession)
    session._page = page
    result = session.execute(
        base_url="http://127.0.0.1:8080",
        action={
            **action,
            "locator": {"by": "css", "value": "#target"},
            "mask_locators": [],
            "observations": [],
        },
    )

    assert page.locator_value.calls[0][0] == expected_method
    assert result.screenshot == b"screenshot"


@pytest.mark.parametrize(
    "action",
    [
        {"action": "reload"},
        {"action": "go_back"},
        {"action": "go_forward"},
        {"action": "wait_for_url", "path": "/finished"},
        {"action": "wait_for_load_state", "state": "networkidle"},
    ],
)
def test_sync_playwright_session_supports_navigation_and_state_waits(
    action: dict[str, object],
) -> None:
    from operamind.infrastructure.test_data.executors import _SyncPlaywrightSession

    page = FakePage()
    session: Any = object.__new__(_SyncPlaywrightSession)
    session._page = page

    session.execute(
        base_url="http://127.0.0.1:8080",
        action={**action, "mask_locators": [], "observations": []},
    )

    assert page.calls[0][0] == action["action"]
    if action["action"] in {"reload", "go_back", "go_forward"}:
        assert page.calls[0][1] == {"wait_until": "domcontentloaded", "timeout": None}


def test_sync_playwright_masks_built_in_and_reviewed_sensitive_elements() -> None:
    from operamind.infrastructure.test_data.executors import _SyncPlaywrightSession

    page = FakePage()
    session: Any = object.__new__(_SyncPlaywrightSession)
    session._page = page

    session.execute(
        base_url="http://127.0.0.1:8080",
        action={
            "action": "goto",
            "path": "/expense",
            "mask_locators": [{"by": "css", "value": ".private-account"}],
            "observations": [],
        },
    )

    assert page.calls[0] == (
        "goto",
        {"wait_until": "domcontentloaded", "timeout": None},
    )
    masks = page.screenshot_options["mask"]
    assert isinstance(masks, list)
    assert len(masks) == 5


def test_sync_playwright_wait_for_allows_an_element_to_appear_after_the_action_starts() -> None:
    from operamind.infrastructure.test_data.executors import _SyncPlaywrightSession

    page = FakePage()
    page.locator_value.count_value = 0
    session: Any = object.__new__(_SyncPlaywrightSession)
    session._page = page

    session.execute(
        base_url="http://127.0.0.1:8080",
        action={
            "action": "wait_for",
            "state": "visible",
            "locator": {"by": "css", "value": "#async-result"},
            "mask_locators": [],
            "observations": [],
        },
    )

    assert page.locator_value.calls[0][0] == "wait_for"


def test_sync_playwright_session_supports_iframe_locator_and_richer_observations() -> None:
    from operamind.infrastructure.test_data.executors import _SyncPlaywrightSession

    page = FakePage()
    page.locator_value.attributes["src"] = "/legacy-frame"
    session: Any = object.__new__(_SyncPlaywrightSession)
    session._page = page

    result = session.execute(
        base_url="http://127.0.0.1:8080",
        action={
            "action": "hover",
            "locator": {
                "by": "placeholder",
                "value": "Search",
                "frame": "iframe#legacy",
            },
            "mask_locators": [],
            "observations": [
                {
                    "key": "enabled",
                    "kind": "enabled",
                    "locator": {"by": "css", "value": "#target"},
                },
                {
                    "key": "checked",
                    "kind": "checked",
                    "locator": {"by": "css", "value": "#target"},
                },
                {
                    "key": "status",
                    "kind": "attribute",
                    "attribute_name": "data-status",
                    "locator": {"by": "css", "value": "#target"},
                },
            ],
        },
    )

    assert page.frame == "iframe#legacy"
    assert result.observations == {
        "url": "http://127.0.0.1:8080/current",
        "title": "Fake page",
        "enabled": True,
        "checked": False,
        "status": "attribute:data-status",
    }


def test_sync_playwright_session_rejects_cross_origin_iframe() -> None:
    from operamind.infrastructure.test_data.executors import _SyncPlaywrightSession

    page = FakePage()
    page.frame_url = "https://outside.example/frame"
    session: Any = object.__new__(_SyncPlaywrightSession)
    session._page = page

    with pytest.raises(OSError, match="frame escaped the approved origin"):
        session.execute(
            base_url="http://127.0.0.1:8080",
            action={
                "action": "hover",
                "locator": {
                    "by": "css",
                    "value": "#target",
                    "frame": "iframe#legacy",
                },
                "mask_locators": [],
                "observations": [],
            },
        )


def _request() -> DataExecutionRequest:
    return DataExecutionRequest(
        execution_result_id="result-001",
        run_id="run-001",
        project_id="visiondemo",
        base_url="http://127.0.0.1:8080",
    )
