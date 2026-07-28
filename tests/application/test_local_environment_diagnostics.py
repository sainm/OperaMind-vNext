from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from operamind.application.local_environment_diagnostics import (
    ExtensionDiagnosticReport,
    LocalEnvironmentDiagnosticsService,
    LocalEnvironmentDiagnosticStore,
)
from operamind.mcp.server import TOOLS

ROOT = Path(__file__).parents[2]


def report(
    *, observed_at: datetime, tools: tuple[str, ...] | None = None
) -> ExtensionDiagnosticReport:
    return ExtensionDiagnosticReport(
        consumer_id="vscode-test",
        observed_at=observed_at,
        workspace_fingerprint="a" * 64,
        vsix_version="0.3.1",
        bridge_url_loopback=True,
        bridge_token_configured=True,
        workspace_trusted=True,
        linked_worktree=True,
        mcp_tool_names=tools or tuple(str(tool["name"]) for tool in TOOLS),
        copilot_extension_installed=True,
        copilot_extension_active=True,
        copilot_extension_version="1.300.0",
        copilot_model_api_available=True,
        copilot_model_count=1,
    )


def test_store_rejects_naive_and_expired_observations() -> None:
    now = datetime(2026, 7, 20, tzinfo=UTC)
    store = LocalEnvironmentDiagnosticStore()

    with pytest.raises(ValueError, match="timezone"):
        store.record(report(observed_at=datetime(2026, 7, 20)), received_at=now)
    with pytest.raises(ValueError, match="too old"):
        store.record(report(observed_at=now - timedelta(days=2)), received_at=now)

    store.record(report(observed_at=now), received_at=now)
    assert store.latest(now=now + timedelta(minutes=14)) is not None
    assert store.latest(now=now + timedelta(minutes=16)) is None


class StubDiagnosticsService(LocalEnvironmentDiagnosticsService):
    def _database_checks(self) -> tuple[dict[str, object], dict[str, object]]:
        return (
            {
                "check_id": "postgresql_connection",
                "label": "PostgreSQL 接続",
                "status": "passed",
                "code": "connected",
                "summary": "接続できました。",
                "remediation": "修復は不要です。",
                "details": {},
            },
            {
                "check_id": "migration",
                "label": "Migration",
                "status": "passed",
                "code": "migration_current",
                "summary": "整合しています。",
                "remediation": "修復は不要です。",
                "details": {},
            },
        )


def test_complete_extension_report_makes_all_checks_ready_without_paths_or_secrets() -> None:
    now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    store = LocalEnvironmentDiagnosticStore()
    service = StubDiagnosticsService(
        repository_root=ROOT,
        database_url="postgresql://secret-user:secret-password@localhost/private",
        bridge_enabled=True,
        store=store,
    )
    store.record(report(observed_at=now), received_at=now)

    result = service.inspect(now=now)
    serialized = str(result)

    assert result["overall_status"] == "warning"
    assert result["summary"] == {"passed": 7, "warnings": 1, "blocked": 0}
    assert result["expected"]["mcp_tool_count"] == 5  # type: ignore[index]
    assert "secret-password" not in serialized
    assert str(ROOT) not in serialized


def test_stale_extension_report_blocks_extension_owned_checks() -> None:
    now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    store = LocalEnvironmentDiagnosticStore()
    service = StubDiagnosticsService(
        repository_root=ROOT,
        database_url="postgresql:///unused",
        bridge_enabled=True,
        store=store,
    )
    store.record(report(observed_at=now), received_at=now)

    result = service.inspect(now=now + timedelta(minutes=6))
    checks = {item["check_id"]: item for item in result["checks"]}  # type: ignore[union-attr]

    assert result["overall_status"] == "blocked"
    assert checks["mcp_tools"]["code"] == "extension_report_stale"
    assert checks["workspace_trust"]["status"] == "blocked"


def test_delayed_observation_is_stale_even_when_just_received() -> None:
    now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    store = LocalEnvironmentDiagnosticStore()
    service = StubDiagnosticsService(
        repository_root=ROOT,
        database_url="postgresql:///unused",
        bridge_enabled=True,
        store=store,
    )
    store.record(report(observed_at=now - timedelta(minutes=6)), received_at=now)

    result = service.inspect(now=now)
    checks = {item["check_id"]: item for item in result["checks"]}  # type: ignore[union-attr]

    assert result["extension_report"]["fresh"] is False  # type: ignore[index]
    assert checks["vsix_version"]["code"] == "extension_report_stale"


def test_mcp_tool_set_reports_missing_and_unexpected_names() -> None:
    now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    expected = tuple(str(tool["name"]) for tool in TOOLS)
    store = LocalEnvironmentDiagnosticStore()
    service = StubDiagnosticsService(
        repository_root=ROOT,
        database_url="postgresql:///unused",
        bridge_enabled=True,
        store=store,
    )
    store.record(
        report(observed_at=now, tools=(*expected[:-1], "unexpected_tool")),
        received_at=now,
    )

    result = service.inspect(now=now)
    checks = {item["check_id"]: item for item in result["checks"]}  # type: ignore[union-attr]
    details = checks["mcp_tools"]["details"]

    assert checks["mcp_tools"]["status"] == "blocked"
    assert details == {"missing": [expected[-1]], "unexpected": ["unexpected_tool"]}
