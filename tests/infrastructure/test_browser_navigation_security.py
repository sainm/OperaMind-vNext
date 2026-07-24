from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Error as PlaywrightError

from operamind.domain import BrowserExecutionManifest
from operamind.infrastructure.browser.playwright import (
    LocalEvidenceStore,
    PlaywrightBrowserExecutor,
    _enforce_approved_navigation,
    _require_approved_page_origin,
)


def test_browser_navigation_must_remain_on_approved_origin() -> None:
    _require_approved_page_origin(
        "http://127.0.0.1:8080/expense/list",
        "http://127.0.0.1:8080",
    )

    with pytest.raises(PlaywrightError, match="escaped the approved origin"):
        _require_approved_page_origin(
            "http://127.0.0.1:8081/redirected",
            "http://127.0.0.1:8080",
        )


class _RouteRequest:
    def __init__(self, url: str, *, navigation: bool = True) -> None:
        self.url = url
        self._navigation = navigation

    def is_navigation_request(self) -> bool:
        return self._navigation


class _Route:
    def __init__(self, url: str, *, navigation: bool = True) -> None:
        self.request = _RouteRequest(url, navigation=navigation)
        self.aborted_with: str | None = None
        self.continued = False

    def abort(self, reason: str) -> None:
        self.aborted_with = reason

    def continue_(self) -> None:
        self.continued = True


def test_browser_routing_aborts_only_cross_origin_navigation() -> None:
    approved = "http://127.0.0.1:8080"
    escaped = _Route("https://outside.example/redirected")
    same_origin = _Route(f"{approved}/expenses")
    cross_origin_asset = _Route("https://cdn.example/app.css", navigation=False)

    _enforce_approved_navigation(escaped, approved)
    _enforce_approved_navigation(same_origin, approved)
    _enforce_approved_navigation(cross_origin_asset, approved)

    assert escaped.aborted_with == "blockedbyclient"
    assert not escaped.continued
    assert same_origin.continued
    assert cross_origin_asset.continued


class _RedirectedPage:
    def __init__(self) -> None:
        self.url = "http://127.0.0.1:8080/"
        self.screenshot_calls = 0

    def set_default_navigation_timeout(self, _timeout: int) -> None:
        return

    def on(self, _event: str, _callback: Any) -> None:
        return

    def expose_binding(self, _name: str, _callback: Any) -> None:
        return

    def add_init_script(self, _script: str) -> None:
        return

    def goto(self, _target: str, *, wait_until: str) -> None:
        assert wait_until == "domcontentloaded"
        self.url = "https://outside.example/private"

    def screenshot(self, **_kwargs: object) -> bytes:
        self.screenshot_calls += 1
        return b"\x89PNG\r\n\x1a\nnot-allowed"


class _RedirectedContext:
    def __init__(self, page: _RedirectedPage) -> None:
        self._page = page
        self.route_handler: Any = None

    def set_default_timeout(self, _timeout: int) -> None:
        return

    def route(self, _pattern: str, handler: Any) -> None:
        self.route_handler = handler

    def new_page(self) -> _RedirectedPage:
        return self._page

    def close(self) -> None:
        return


class _RedirectedBrowser:
    def __init__(self, context: _RedirectedContext) -> None:
        self._context = context

    def new_context(self, **_kwargs: object) -> _RedirectedContext:
        return self._context


def _manifest() -> BrowserExecutionManifest:
    return BrowserExecutionManifest.from_dict(
        {
            "manifest_id": "browser-manifest-cross-origin",
            "plan_id": "ui-plan-cross-origin",
            "project_id": "project-cross-origin",
            "browser": {
                "name": "chromium",
                "channel": "msedge",
                "headless": True,
                "viewport": {"width": 1024, "height": 768},
            },
            "review_status": "approved",
            "reviewed_by": "qa@example.invalid",
            "scenarios": [
                {
                    "scenario_id": "redirected-scenario",
                    "trigger_path": "/expenses",
                    "impact_item_refs": ["impact-cross-origin"],
                    "actions": [],
                    "assertions": [
                        {
                            "assertion_id": "never-executed",
                            "kind": "visible",
                            "locator": {"strategy": "test_id", "value": "private"},
                            "failure_category": "business_assertion",
                        }
                    ],
                    "redaction_locators": [],
                }
            ],
        }
    )


def test_cross_origin_redirect_never_captures_screenshot_evidence(tmp_path: Path) -> None:
    page = _RedirectedPage()
    context = _RedirectedContext(page)
    executor = PlaywrightBrowserExecutor(evidence_store=LocalEvidenceStore(tmp_path))

    outcome, evidence = executor._run_scenario(
        browser=_RedirectedBrowser(context),  # type: ignore[arg-type]
        manifest=_manifest(),
        scenario=_manifest().scenarios[0],
        origin="http://127.0.0.1:8080",
        run_id="run-cross-origin",
        storage_state=None,
    )

    assert outcome.status == "blocked"
    assert outcome.failure_category == "environment"
    assert page.screenshot_calls == 0
    assert {item.evidence_type for item in evidence} == {
        "assertion",
        "step_log",
        "network_summary",
    }
