from __future__ import annotations

import os
import socket
import struct
import threading
import time
import zlib
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from playwright.sync_api import Page, expect, sync_playwright

from operamind.web.app import create_app
from operamind.web.dependencies import get_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("OPERAMIND_PLAYWRIGHT_LIVE") != "1",
        reason="OPERAMIND_PLAYWRIGHT_LIVE is not set",
    ),
]

ROOT = Path(__file__).parents[2]
SNAPSHOT_ROOT = ROOT / "tests" / "web" / "snapshots"


class _VisualRegressionService:
    def readiness(self) -> dict[str, object]:
        return {
            "readiness_stage": "partial_ready",
            "manifest_status": "pending",
            "gates": [
                {
                    "gate_id": "golden_dataset",
                    "status": "pending",
                    "reason": "レビュー待ち",
                }
            ],
        }

    def list_projects(self) -> dict[str, object]:
        return {
            "projects": [
                {
                    "project_id": "visiondemo",
                    "name": "VisionDemo",
                    "change_request_count": 0,
                }
            ],
            "count": 1,
        }

    def list_change_requests(self, *, project_id: str) -> dict[str, object]:
        assert project_id == "visiondemo"
        return {"project_id": project_id, "change_requests": [], "count": 0}

    def orchestration_task_management(self, **values: object) -> dict[str, object]:
        assert values["project_id"] == "visiondemo"
        return {"tasks": [], "count": 0}

    def orchestration_task_dependency_graph(self, **values: object) -> dict[str, object]:
        assert values["project_id"] == "visiondemo"
        return {"tasks": [], "count": 0, "total_count": 0, "truncated": False}

    def orchestration_task_runtime_monitoring(self, **values: object) -> dict[str, object]:
        assert values == {"project_id": "visiondemo", "window_hours": 24}
        return {
            "window_hours": 24,
            "project_id": "visiondemo",
            "task_count": 0,
            "ready_count": 0,
            "active_task_count": 0,
            "claim_count": 0,
            "average_queue_wait_seconds": None,
            "p95_queue_wait_seconds": None,
            "average_execution_seconds": None,
            "success_count": 0,
            "result_count": 0,
            "success_rate": None,
            "retry_count": 0,
            "retried_task_count": 0,
            "lease_expiry_count": 0,
            "blocker_reasons": [],
            "workers": [],
            "alerts": [],
        }

    def ui_knowledge_review_queue(self, *, project_id: str) -> dict[str, object]:
        assert project_id == "visiondemo"
        return {
            "project_id": project_id,
            "draft_count": 0,
            "drafts": [],
            "versions": [],
        }

    def profile_registry(self, *, project_id: str) -> dict[str, object]:
        assert project_id == "visiondemo"
        return {
            "project_id": project_id,
            "profile_versions": [],
            "bindings": [],
            "drift_events": [],
            "rebuild_requests": [],
            "rebuild_batches": [],
            "open_drift_count": 0,
            "open_impact_count": 0,
        }

    def unresolved_evidence_management(
        self, *, project_id: str, history_limit: int = 50
    ) -> dict[str, object]:
        assert project_id == "visiondemo"
        assert history_limit == 50
        return {
            "project_id": project_id,
            "current_reports": [],
            "history": [],
            "current_report_count": 0,
            "history_count": 0,
            "open_count": 0,
            "closed_in_current_count": 0,
        }


def test_web_console_navigation_drawer_accessibility_and_visual_snapshots() -> None:
    app = create_app(repository_root=ROOT, database_url="postgresql:///unused")
    app.dependency_overrides[get_service] = lambda: _VisualRegressionService()
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    errors: list[str] = []

    def capture_console(message: Any) -> None:
        if message.type == "error" and not message.text.startswith("Failed to load resource:"):
            errors.append(message.text)

    def capture_response(response: Any) -> None:
        if response.status >= 400 and not response.url.endswith("/favicon.ico"):
            errors.append(f"HTTP {response.status}: {response.url}")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                channel=os.getenv("OPERAMIND_PLAYWRIGHT_CHANNEL", "msedge"),
            )
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.emulate_media(reduced_motion="reduce")
            page.on("console", capture_console)
            page.on("response", capture_response)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{port}", wait_until="networkidle")

            expect(page.locator("html")).to_have_attribute("lang", "ja")
            expect(page.get_by_role("navigation", name="ワークスペース")).to_be_visible()
            expect(page.get_by_role("heading", name="変更フロー", level=1)).to_be_visible()
            expect(page.locator('[data-view="change"]')).to_have_attribute(
                "aria-current", "page"
            )
            expect(page.locator("#requestPanel")).to_be_visible()
            expect(page.locator("#orchestrationTaskManagementPanel")).to_be_hidden()
            expect(page.locator("#workbenchOverviewPanel")).to_be_visible()
            expect(page.locator("#workbenchOverviewPanel")).to_contain_text("現在の変更")
            expect(page.locator("#workbenchOverviewPanel")).to_contain_text("待ち確認")
            expect(page.locator("#workbenchOverviewPanel")).to_contain_text("ブロック")
            expect(page.locator("#workbenchOverviewPanel")).to_contain_text("全体進捗")
            expect(page.locator("#workbenchOverviewPanel")).to_contain_text("次の操作")
            _assert_no_horizontal_overflow(page)
            _assert_visual_snapshot(page, "web-console-change-desktop.png")

            page.locator("#detailDrawerToggle").click()
            expect(page.locator("#detailDrawer")).to_be_visible()
            expect(page.locator("#detailDrawer")).to_have_attribute("aria-hidden", "false")
            expect(page.locator("#detailDrawer")).to_have_attribute("aria-modal", "true")
            assert page.evaluate("document.querySelector('.shell').inert") is True
            expect(page.locator("#detailDrawerClose")).to_be_focused()
            page.keyboard.press("Escape")
            expect(page.locator("#detailDrawer")).to_be_hidden()
            assert page.evaluate("document.querySelector('.shell').inert") is False
            assert page.evaluate("document.activeElement.id") == "detailDrawerToggle"

            for view, heading, snapshot in (
                ("tests", "テスト", "web-console-tests-desktop.png"),
                ("evidence", "証跡", "web-console-evidence-desktop.png"),
                ("operations", "運用", "web-console-operations-desktop.png"),
                ("settings", "設定", "web-console-settings-desktop.png"),
            ):
                page.locator(f'[data-view="{view}"]').click()
                expect(page.get_by_role("heading", name=heading, level=1)).to_be_visible()
                _assert_no_horizontal_overflow(page)
                _assert_visual_snapshot(page, snapshot)

            page.locator('[data-view="operations"]').click()
            expect(page.locator("#orchestrationTaskManagementPanel")).to_be_visible()
            expect(page.locator("#environmentDiagnosticsPanel")).to_be_visible()
            expect(page.locator("#requestPanel")).to_be_hidden()
            assert page.evaluate("window.scrollY") == 0

            for width in (1366, 1024, 768):
                page.set_viewport_size({"width": width, "height": 900})
                for view in ("change", "tests", "evidence", "operations", "settings"):
                    page.evaluate(
                        "(view) => window.OperaMindLayout.activateView(view, "
                        "{focus: false, immediate: true})",
                        view,
                    )
                    _assert_no_horizontal_overflow(page, context=f"{view}@{width}px")

            page.set_viewport_size({"width": 390, "height": 844})
            for view in ("change", "tests", "evidence", "operations", "settings"):
                page.evaluate(
                    "(view) => window.OperaMindLayout.activateView(view, "
                    "{focus: false, immediate: true})",
                    view,
                )
                _assert_no_horizontal_overflow(page, context=f"{view}@390px")
            page.evaluate(
                "() => window.OperaMindLayout.activateView('operations', "
                "{focus: false, immediate: true})"
            )
            expect(page.locator("#mobileNavToggle")).to_be_visible()
            expect(page.locator("#primarySidebar")).to_have_attribute("aria-hidden", "true")
            page.locator("#mobileNavToggle").click()
            expect(page.locator("#primarySidebar")).to_be_visible()
            expect(page.locator("#primarySidebar")).not_to_have_attribute("aria-hidden", "true")
            expect(page.locator("#mobileNavToggle")).to_have_attribute("aria-expanded", "true")
            expect(page.locator('[data-view="operations"]')).to_be_focused()
            _assert_visual_snapshot(page, "web-console-navigation-mobile.png")
            page.keyboard.press("Escape")
            expect(page.locator("#mobileNavToggle")).to_have_attribute("aria-expanded", "false")
            expect(page.locator("#mobileNavToggle")).to_be_focused()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()

    assert errors == []


def _assert_no_horizontal_overflow(page: Page, *, context: str = "") -> None:
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    offenders = page.evaluate(
        """() => [...document.querySelectorAll('body *')]
          .map(node => ({tag: node.tagName, id: node.id, className: String(node.className),
            right: Math.round(node.getBoundingClientRect().right),
            width: Math.round(node.getBoundingClientRect().width)}))
          .filter(item => item.right > document.documentElement.clientWidth + 1)
          .slice(0, 8)"""
    )
    assert overflow == 0, (
        f"horizontal overflow {overflow}px {context}; offenders={offenders}"
    ).strip()


def _assert_visual_snapshot(page: Page, filename: str) -> None:
    actual = page.screenshot(animations="disabled")
    target = SNAPSHOT_ROOT / filename
    if os.getenv("OPERAMIND_UPDATE_VISUAL_BASELINE") == "1":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(actual)
    assert target.is_file(), (
        f"Visual baseline is missing: {target}. "
        "Set OPERAMIND_UPDATE_VISUAL_BASELINE=1 to create an intentional baseline."
    )
    changed_ratio = _png_changed_pixel_ratio(target.read_bytes(), actual)
    assert changed_ratio <= 0.01, (
        f"{filename} changed {changed_ratio:.2%} of pixels; "
        "review the UI and update the baseline only when the change is intentional."
    )


def _png_changed_pixel_ratio(expected: bytes, actual: bytes) -> float:
    expected_width, expected_height, expected_pixels = _decode_png(expected)
    actual_width, actual_height, actual_pixels = _decode_png(actual)
    assert (actual_width, actual_height) == (expected_width, expected_height)
    changed = 0
    pixel_count = expected_width * expected_height
    for offset in range(0, len(expected_pixels), 4):
        if any(
            abs(expected_pixels[offset + channel] - actual_pixels[offset + channel]) > 24
            for channel in range(3)
        ):
            changed += 1
    return changed / pixel_count


def _decode_png(value: bytes) -> tuple[int, int, bytes]:
    assert value.startswith(b"\x89PNG\r\n\x1a\n")
    position = 8
    compressed = bytearray()
    width = height = color_type = 0
    while position < len(value):
        length = struct.unpack(">I", value[position : position + 4])[0]
        chunk_type = value[position + 4 : position + 8]
        data = value[position + 8 : position + 8 + length]
        position += 12 + length
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(
                ">IIBBBBB", data
            )
            assert bit_depth == 8
            assert color_type in {2, 6}
            assert interlace == 0
        elif chunk_type == b"IDAT":
            compressed.extend(data)
        elif chunk_type == b"IEND":
            break
    source = zlib.decompress(bytes(compressed))
    source_channels = 4 if color_type == 6 else 3
    stride = width * source_channels
    previous = bytearray(stride)
    output = bytearray()
    position = 0
    for _ in range(height):
        filter_type = source[position]
        position += 1
        encoded = source[position : position + stride]
        position += stride
        decoded = bytearray(stride)
        for index, byte in enumerate(encoded):
            left = decoded[index - source_channels] if index >= source_channels else 0
            up = previous[index]
            up_left = previous[index - source_channels] if index >= source_channels else 0
            predictor = _png_predictor(filter_type, left, up, up_left)
            decoded[index] = (byte + predictor) & 0xFF
        if source_channels == 4:
            output.extend(decoded)
        else:
            for index in range(0, stride, 3):
                output.extend(decoded[index : index + 3])
                output.append(255)
        previous = decoded
    return width, height, bytes(output)


def _png_predictor(filter_type: int, left: int, up: int, up_left: int) -> int:
    if filter_type == 0:
        return 0
    if filter_type == 1:
        return left
    if filter_type == 2:
        return up
    if filter_type == 3:
        return (left + up) // 2
    assert filter_type == 4
    candidate = left + up - up_left
    distances = (
        abs(candidate - left),
        abs(candidate - up),
        abs(candidate - up_left),
    )
    return (left, up, up_left)[distances.index(min(distances))]
