import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from operamind.domain import (
    BrowserExecutionManifest,
    BrowserLocator,
    LocatorStrategy,
    UiKnowledgeSnapshot,
    UiKnowledgeTarget,
    UiLocatorCandidate,
    runtime_candidate_id,
)
from operamind.infrastructure.browser import (
    LocalEvidenceStore,
    PlaywrightBrowserExecutor,
    PlaywrightBrowserPreflightProbe,
    PlaywrightUiKnowledgeRuntimeObserver,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("OPERAMIND_PLAYWRIGHT_LIVE") != "1",
        reason="OPERAMIND_PLAYWRIGHT_LIVE is not set",
    ),
]

_HTML = b"""<!doctype html>
<html lang="en">
<body>
  <label for="status">Status</label>
  <select id="status" data-testid="status-filter">
    <option value="all">All</option><option value="returned">Returned</option>
  </select>
  <div data-testid="expense-row">Expense 1</div>
  <div data-testid="expense-row">Expense 2</div>
  <div data-testid="expense-row">Expense 3</div>
  <div data-testid="expense-row">Expense 4</div>
  <div data-testid="seed-ready">Seed ready</div>
  <form action="/customers/search" method="get">
    <button type="submit" data-testid="submit-search">Search</button>
  </form>
  <div data-sensitive="true">token=should-be-masked</div>
</body>
</html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_HTML)))
        self.end_headers()
        self.wfile.write(_HTML)

    def log_message(self, format: str, *args: object) -> None:
        return


def _manifest(*, expected_count: str) -> BrowserExecutionManifest:
    return BrowserExecutionManifest.from_dict(
        {
            "manifest_id": f"browser-manifest-{expected_count}",
            "plan_id": "ui-plan-001",
            "project_id": "project-001",
            "browser": {
                "name": "chromium",
                "channel": "chrome",
                "headless": True,
                "viewport": {"width": 1024, "height": 768},
            },
            "review_status": "approved",
            "reviewed_by": "qa@example.com",
            "scenarios": [
                {
                    "scenario_id": "expense-filter-default-all",
                    "trigger_path": "/expenses",
                    "impact_item_refs": ["impact-item-001"],
                    "actions": [
                        {
                            "action_id": "select-all",
                            "kind": "select_option",
                            "locator": {"strategy": "label", "value": "Status"},
                            "value": {"source": "literal", "value": "all"},
                        },
                        {
                            "action_id": "submit-search",
                            "kind": "click",
                            "locator": {"strategy": "test_id", "value": "submit-search"},
                            "route_source_ref": "route-form-generic",
                        },
                    ],
                    "assertions": [
                        {
                            "assertion_id": "row-count",
                            "kind": "count_equals",
                            "locator": {"strategy": "test_id", "value": "expense-row"},
                            "expected": {"source": "literal", "value": expected_count},
                            "failure_category": "business_assertion",
                        }
                    ],
                    "redaction_locators": [{"strategy": "css", "value": "[data-sensitive='true']"}],
                }
            ],
        }
    )


def test_real_playwright_executor_captures_pass_and_business_failure(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        executor = PlaywrightBrowserExecutor(
            evidence_store=LocalEvidenceStore(tmp_path),
            timeout_ms=2_000,
            navigation_timeout_ms=5_000,
        )

        passed = executor.execute(
            manifest=_manifest(expected_count="4"),
            base_url=base_url,
            run_id="run-passed",
        )
        failed = executor.execute(
            manifest=_manifest(expected_count="5"),
            base_url=base_url,
            run_id="run-failed",
        )

        assert passed.scenario_results[0].status == "passed"
        assert passed.scenario_results[0].failure_category == "none"
        assert {item.evidence_type for item in passed.evidence} == {
            "screenshot",
            "assertion",
            "step_log",
            "network_summary",
        }
        network_evidence = next(
            item for item in passed.evidence if item.evidence_type == "network_summary"
        )
        network_path = (
            tmp_path / "project-001" / "run-passed" / f"{network_evidence.evidence_id}.json"
        )
        route_capture = json.loads(network_path.read_text(encoding="utf-8"))
        assert {item["event_kind"] for item in route_capture["route_observations"]} >= {
            "network_request",
            "navigation",
            "form_submission",
        }
        bound = [
            item
            for item in route_capture["route_observations"]
            if item.get("source_route_ref") == "route-form-generic"
        ]
        assert bound and all(item["path"] == "/customers/search" for item in bound)
        assert failed.scenario_results[0].status == "failed"
        assert failed.scenario_results[0].failure_category == "business_assertion"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_real_playwright_preflight_checks_trigger_data_and_locator() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        raw = _manifest(expected_count="4").to_dict()
        scenario = raw["scenarios"][0]  # type: ignore[index]
        scenario["preflight_assertions"] = [
            {
                "assertion_id": "seed-ready",
                "kind": "visible",
                "locator": {"strategy": "test_id", "value": "seed-ready"},
                "failure_category": "test_data",
            }
        ]
        manifest = BrowserExecutionManifest.from_dict(raw)
        observations = PlaywrightBrowserPreflightProbe(
            timeout_ms=2_000,
            navigation_timeout_ms=5_000,
        ).inspect(
            manifest=manifest,
            base_url=f"http://127.0.0.1:{server.server_port}",
            attempt_id="attempt-live",
        )

        assert {item.check_type for item in observations} == {
            "authentication",
            "environment",
            "locator",
            "test_data",
            "trigger_path",
        }
        assert all(item.status == "passed" for item in observations)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_real_playwright_runtime_observation_discovers_stable_test_id(
    tmp_path: Path,
) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        label_locator = BrowserLocator(
            strategy=LocatorStrategy.LABEL,
            value="Status",
        )
        source = UiKnowledgeSnapshot(
            snapshot_id="ui-knowledge-source",
            project_id="project-001",
            environment_id="local",
            deployment_revision="deploy-001",
            snapshot_version="1.0.0",
            review_status="approved",
            reviewed_by="qa@example.com",
            targets=(
                UiKnowledgeTarget(
                    target_ref="expense.status-filter",
                    business_name="Status filter",
                    screen_name="Expenses",
                    trigger_path="/expenses",
                    source_fact_refs=("fact-status",),
                    candidates=(
                        UiLocatorCandidate(
                            candidate_id=runtime_candidate_id(
                                "expense.status-filter", label_locator
                            ),
                            locator=label_locator,
                            priority=1,
                            reliability_score=0.90,
                            source="canonical_screen_element_proposal",
                        ),
                    ),
                ),
            ),
        )

        result = PlaywrightUiKnowledgeRuntimeObserver(
            timeout_ms=2_000,
            navigation_timeout_ms=5_000,
            evidence_store=LocalEvidenceStore(tmp_path),
        ).observe(
            source=source,
            base_url=f"http://127.0.0.1:{server.server_port}",
            observation_run_id="observation-run-live",
            result_snapshot_id="ui-knowledge-observed",
            result_snapshot_version="1.1.0-draft",
        )

        assert result.status == "completed"
        assert result.snapshot is not None
        assert result.snapshot.review_status == "draft"
        assert {item.locator.strategy for item in result.snapshot.targets[0].candidates} == {
            LocatorStrategy.LABEL,
            LocatorStrategy.TEST_ID,
        }
        assert any(item.discovered for item in result.observations)
        assert all(item.status.value == "unique_visible" for item in result.observations)
        assert len(result.evidence) == 1
        evidence = result.evidence[0]
        assert evidence.target_ref == "expense.status-filter"
        assert evidence.sanitized
        assert (
            tmp_path / "project-001" / "observation-run-live" / f"{evidence.evidence_id}.png"
        ).is_file()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
