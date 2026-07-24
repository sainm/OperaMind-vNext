from typing import Any

from operamind.domain import BrowserExecutionManifest
from operamind.infrastructure.browser.playwright import (
    _capture_form_route,
    _capture_request_routes,
    _capture_response_summary,
)


class _Request:
    resource_type = "xhr"
    url = "https://example.invalid/api/customers/42?token=not-recorded"
    method = "GET"

    @staticmethod
    def is_navigation_request() -> bool:
        return True


def test_browser_capture_distinguishes_network_navigation_and_form_without_query() -> None:
    observations: list[dict[str, object]] = []
    active = {"action_id": "open-customer", "route_source_ref": "route-dynamic"}

    _capture_request_routes(
        request=_Request(),
        run_id="run-1",
        scenario_id="customer-detail",
        active_action=active,
        observations=observations,
    )
    _capture_form_route(
        payload={"method": "POST", "path": "/customers/search?secret=not-recorded"},
        run_id="run-1",
        scenario_id="customer-detail",
        active_action=active,
        observations=observations,
    )

    assert [item["event_kind"] for item in observations] == [
        "network_request",
        "navigation",
        "form_submission",
    ]
    assert [item["path"] for item in observations] == [
        "/api/customers/42",
        "/api/customers/42",
        "/customers/search",
    ]
    assert all(item["source_route_ref"] == "route-dynamic" for item in observations)


class _ResponseRequest:
    method = "GET"


class _Response:
    request = _ResponseRequest()
    status = 200

    def __init__(self, url: str) -> None:
        self.url = url


def test_browser_evidence_ignores_cross_origin_network_metadata() -> None:
    observations: list[dict[str, object]] = []
    summaries: list[dict[str, object]] = []
    approved = "https://approved.example"

    _capture_request_routes(
        request=_Request(),
        run_id="run-1",
        scenario_id="customer-detail",
        active_action={},
        observations=observations,
        approved_origin=approved,
    )
    _capture_response_summary(
        response=_Response("https://outside.example/private?token=secret"),
        approved_origin=approved,
        summaries=summaries,
    )

    assert observations == []
    assert summaries == []


def test_browser_manifest_round_trips_internal_static_route_binding() -> None:
    raw: dict[str, Any] = {
        "manifest_id": "manifest-generic",
        "plan_id": "plan-generic",
        "project_id": "project-generic",
        "browser": {
            "name": "chromium",
            "channel": None,
            "headless": True,
            "viewport": {"width": 1024, "height": 768},
        },
        "review_status": "approved",
        "reviewed_by": "qa@example.invalid",
        "scenarios": [
            {
                "scenario_id": "customer-detail",
                "trigger_path": "/customers",
                "impact_item_refs": ["impact-customer"],
                "actions": [
                    {
                        "action_id": "open-customer",
                        "kind": "click",
                        "locator": {"strategy": "test_id", "value": "open-customer"},
                        "route_source_ref": "route-dynamic",
                    }
                ],
                "assertions": [
                    {
                        "assertion_id": "detail-visible",
                        "kind": "visible",
                        "locator": {"strategy": "test_id", "value": "customer-detail"},
                        "failure_category": "business_assertion",
                    }
                ],
                "redaction_locators": [],
            }
        ],
    }

    manifest = BrowserExecutionManifest.from_dict(raw)

    assert manifest.scenarios[0].actions[0].route_source_ref == "route-dynamic"
    assert BrowserExecutionManifest.from_dict(manifest.to_dict()) == manifest
