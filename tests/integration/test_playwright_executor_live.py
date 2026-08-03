from __future__ import annotations

import http.server
import os
import threading
from pathlib import Path

import pytest

from operamind.application.test_data_execution import (
    TestDataExecutionRequest as ExecutionRequest,
)
from operamind.infrastructure.browser import LocalEvidenceStore
from operamind.infrastructure.test_data import PlaywrightUiTestDataExecutor

pytestmark = pytest.mark.skipif(
    os.getenv("OPERAMIND_PLAYWRIGHT_EXECUTOR_LIVE") != "1",
    reason="OPERAMIND_PLAYWRIGHT_EXECUTOR_LIVE is not set",
)


class _UiHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"""<!doctype html>
<html><head><title>Expense UI</title></head><body>
<h1>Expense list</h1>
<button type='button' aria-label='Show returned' id='show'>Show returned</button>
<p id='result'>No filter</p>
<input type='password' value='secret' aria-label='Password'>
<script>document.querySelector('#show').onclick = () => {
  document.querySelector('#result').textContent = 'Returned expenses';
}</script>
</body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


def test_real_chrome_executor_runs_actions_and_persists_screenshots(tmp_path: Path) -> None:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _UiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    executor = PlaywrightUiTestDataExecutor(
        evidence_store=LocalEvidenceStore(tmp_path),
        browser_channel=os.getenv("OPERAMIND_PLAYWRIGHT_CHANNEL", "chrome"),
    )
    request = ExecutionRequest(
        execution_result_id="result-live",
        run_id="run-live",
        project_id="live-project",
        base_url=f"http://127.0.0.1:{server.server_port}",
    )
    try:
        opened = executor.execute(
            request=request,
            flow_id="expense-flow",
            step={
                "step_id": "open",
                "screen_ref": "expense-list",
                "ui_action_ref": "open",
                "playwright": {
                    "action": "goto",
                    "path": "/",
                    "mask_locators": [],
                    "observations": [
                        {"key": "title", "kind": "title"},
                        {
                            "key": "heading",
                            "kind": "text",
                            "locator": {"by": "role", "value": "heading", "name": "Expense list"},
                        },
                    ],
                },
            },
            resolved_inputs={},
            variables={},
            phase="setup",
        )
        clicked = executor.execute(
            request=request,
            flow_id="expense-flow",
            step={
                "step_id": "filter",
                "screen_ref": "expense-list",
                "ui_action_ref": "show-returned",
                "playwright": {
                    "action": "click",
                    "locator": {"by": "role", "value": "button", "name": "Show returned"},
                    "mask_locators": [],
                    "observations": [
                        {
                            "key": "result",
                            "kind": "text",
                            "locator": {"by": "css", "value": "#result"},
                        }
                    ],
                },
            },
            resolved_inputs={},
            variables={},
            phase="setup",
        )
        assert opened.source_values["ui"]["heading"] == "Expense list"  # type: ignore[index]
        assert clicked.source_values["ui"]["result"] == "Returned expenses"  # type: ignore[index]
        screenshots = list(tmp_path.rglob("td-screenshot-*.png"))
        assert len(screenshots) == 2
        assert all(item.stat().st_size > 100 for item in screenshots)
        assert b"secret" not in screenshots[0].read_bytes()
    finally:
        executor.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
