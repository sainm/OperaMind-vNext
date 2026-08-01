"""Sanitized, short-lived diagnostics for the local Web/VS Code environment."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

import psycopg

from operamind.infrastructure.postgres.migrations import MigrationCatalog
from operamind.mcp.server import TOOLS

DiagnosticStatus = Literal["passed", "warning", "blocked"]
EXTENSION_REPORT_MAX_AGE = timedelta(minutes=5)
EXTENSION_REPORT_RETENTION = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class ExtensionDiagnosticReport:
    """A path-free and secret-free observation sent by one VS Code extension."""

    consumer_id: str
    observed_at: datetime
    workspace_fingerprint: str | None
    vsix_version: str
    bridge_url_loopback: bool
    bridge_token_configured: bool
    workspace_trusted: bool
    linked_worktree: bool
    mcp_tool_names: tuple[str, ...]
    copilot_extension_installed: bool
    copilot_extension_active: bool
    copilot_extension_version: str | None
    copilot_model_api_available: bool
    copilot_model_count: int


@dataclass(frozen=True, slots=True)
class _StoredExtensionReport:
    report: ExtensionDiagnosticReport
    received_at: datetime


class LocalEnvironmentDiagnosticStore:
    """Keep only recent sanitized observations; diagnostics are not Canonical Data."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reports: dict[str, _StoredExtensionReport] = {}

    def record(
        self,
        report: ExtensionDiagnosticReport,
        *,
        received_at: datetime | None = None,
    ) -> None:
        now = received_at or datetime.now(UTC)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("received_at must include a timezone")
        if report.observed_at.tzinfo is None or report.observed_at.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        if report.observed_at > now + timedelta(minutes=5):
            raise ValueError("VS Code diagnostic observed_at is too far in the future")
        if report.observed_at < now - timedelta(days=1):
            raise ValueError("VS Code diagnostic observation is too old")
        with self._lock:
            self._prune(now)
            self._reports[report.consumer_id] = _StoredExtensionReport(report, now)
            if len(self._reports) > 32:
                oldest = min(self._reports, key=lambda key: self._reports[key].received_at)
                del self._reports[oldest]

    def latest(self, *, now: datetime | None = None) -> _StoredExtensionReport | None:
        current = now or datetime.now(UTC)
        with self._lock:
            self._prune(current)
            if not self._reports:
                return None
            return max(self._reports.values(), key=lambda item: item.received_at)

    def _prune(self, now: datetime) -> None:
        expired = [
            key
            for key, value in self._reports.items()
            if value.received_at < now - EXTENSION_REPORT_RETENTION
        ]
        for key in expired:
            del self._reports[key]


class LocalEnvironmentDiagnosticsService:
    """Inspect server state and merge the latest sanitized VS Code observation."""

    def __init__(
        self,
        *,
        repository_root: Path,
        database_url: str,
        bridge_enabled: bool,
        store: LocalEnvironmentDiagnosticStore | None = None,
    ) -> None:
        self._root = repository_root.resolve()
        self._database_url = database_url
        self._bridge_enabled = bridge_enabled
        self._store = store or LocalEnvironmentDiagnosticStore()
        self._catalog = MigrationCatalog.load(self._root / "migrations")
        self._expected_vsix_version = self._load_vsix_version()
        self._expected_mcp_tools = tuple(str(tool["name"]) for tool in TOOLS)

    def record_extension_report(self, report: ExtensionDiagnosticReport) -> dict[str, object]:
        self._store.record(report)
        return self.inspect()

    def inspect(self, *, now: datetime | None = None) -> dict[str, object]:
        current = now or datetime.now(UTC)
        stored = self._store.latest(now=current)
        extension = stored.report if stored is not None else None
        extension_fresh = bool(
            stored is not None
            and stored.received_at >= current - EXTENSION_REPORT_MAX_AGE
            and stored.report.observed_at >= current - EXTENSION_REPORT_MAX_AGE
            and stored.report.observed_at <= current + timedelta(minutes=5)
        )
        checks = [
            self._vsix_check(extension, extension_fresh),
            self._bridge_check(extension, extension_fresh),
            *self._database_checks(),
            self._mcp_check(extension, extension_fresh),
            self._trust_check(extension, extension_fresh),
            self._linked_worktree_check(extension, extension_fresh),
            self._copilot_check(extension, extension_fresh),
        ]
        blocked = sum(check["status"] == "blocked" for check in checks)
        warnings = sum(check["status"] == "warning" for check in checks)
        overall = "blocked" if blocked else "warning" if warnings else "ready"
        return {
            "schema_version": "v1",
            "generated_at": current.isoformat().replace("+00:00", "Z"),
            "overall_status": overall,
            "safe_to_share": True,
            "extension_report": {
                "available": extension is not None,
                "fresh": extension_fresh,
                "observed_at": (
                    extension.observed_at.isoformat().replace("+00:00", "Z")
                    if extension is not None
                    else None
                ),
                "workspace_fingerprint": (
                    extension.workspace_fingerprint if extension is not None else None
                ),
            },
            "expected": {
                "vsix_version": self._expected_vsix_version,
                "migration_version": self._catalog.migrations[-1].version,
                "mcp_tool_count": len(self._expected_mcp_tools),
                "mcp_tool_names": list(self._expected_mcp_tools),
            },
            "summary": {
                "passed": sum(check["status"] == "passed" for check in checks),
                "warnings": warnings,
                "blocked": blocked,
            },
            "checks": checks,
        }

    def _load_vsix_version(self) -> str:
        path = self._root / "vscode-extension/package.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        version = payload.get("version") if isinstance(payload, dict) else None
        if not isinstance(version, str) or not version.strip():
            raise ValueError("VS Code extension package version is missing")
        return version

    def _database_checks(self) -> tuple[dict[str, object], dict[str, object]]:
        try:
            with (
                psycopg.connect(self._database_url, connect_timeout=3) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute("SELECT current_setting('server_version')")
                version_row = cursor.fetchone()
                cursor.execute("SELECT to_regclass('schema_migrations')")
                table_row = cursor.fetchone()
                applied_rows: list[tuple[object, ...]] = []
                if table_row is not None and table_row[0] is not None:
                    cursor.execute(
                        "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
                    )
                    applied_rows = list(cursor.fetchall())
        except (psycopg.Error, OSError) as error:
            return (
                _check(
                    "postgresql_connection",
                    "PostgreSQL 接続",
                    "blocked",
                    "PostgreSQL に接続できません。接続情報は表示・保存していません。",
                    "Web を起動した Terminal で OPERAMIND_DATABASE_URL を確認し、"
                    "PostgreSQL を起動してください。",
                    code=f"connection_failed:{type(error).__name__}",
                ),
                _check(
                    "migration",
                    "Migration",
                    "blocked",
                    "PostgreSQL 接続がないため Migration を確認できません。",
                    "接続を復旧した後、仮想環境の operamind-migrate を実行してください。",
                    code="database_unavailable",
                ),
            )
        version = str(version_row[0]) if version_row else "unknown"
        postgres = _check(
            "postgresql_connection",
            "PostgreSQL 接続",
            "passed",
            f"PostgreSQL {version} に接続できました。",
            "修復は不要です。",
            code="connected",
        )
        if table_row is None or table_row[0] is None:
            migration = _check(
                "migration",
                "Migration",
                "blocked",
                "schema_migrations が存在しません。",
                "仮想環境の operamind-migrate を一度実行してください。",
                code="migration_table_missing",
            )
            return postgres, migration
        migration = self._migration_check(applied_rows)
        return postgres, migration

    def _migration_check(self, rows: list[tuple[object, ...]]) -> dict[str, object]:
        applied = {str(version): (str(name), str(checksum)) for version, name, checksum in rows}
        expected = {item.version: (item.name, item.checksum) for item in self._catalog.migrations}
        unknown = sorted(set(applied) - set(expected))
        mismatched = sorted(
            version
            for version in set(applied) & set(expected)
            if applied[version] != expected[version]
        )
        pending = [item.version for item in self._catalog.migrations if item.version not in applied]
        latest = max(applied, default="未適用")
        if unknown or mismatched:
            return _check(
                "migration",
                "Migration",
                "blocked",
                "Migration 履歴とリポジトリの不可変 catalog が一致しません。",
                "DB を書き換えず、unknown/checksum mismatch を確認してから復旧してください。",
                code="migration_integrity_mismatch",
                details={"unknown": unknown, "checksum_mismatch": mismatched},
            )
        if pending:
            return _check(
                "migration",
                "Migration",
                "blocked",
                f"最新適用は {latest}、未適用 Migration は {len(pending)} 件です。",
                "仮想環境の operamind-migrate を実行し、再診断してください。",
                code="migration_pending",
                details={"pending": pending},
            )
        return _check(
            "migration",
            "Migration",
            "passed",
            f"Migration {self._catalog.migrations[-1].version} まで整合しています。",
            "修復は不要です。",
            code="migration_current",
        )

    def _vsix_check(
        self, report: ExtensionDiagnosticReport | None, fresh: bool
    ) -> dict[str, object]:
        missing = self._missing_extension_check(
            "vsix_version",
            "VSIX バージョン",
            "VS Code で「OperaMind: ローカル環境を診断」を実行してください。",
            report,
            fresh,
        )
        if missing is not None:
            return missing
        assert report is not None
        status: DiagnosticStatus = (
            "passed" if report.vsix_version == self._expected_vsix_version else "blocked"
        )
        return _check(
            "vsix_version",
            "VSIX バージョン",
            status,
            f"インストール済み {report.vsix_version} / 期待 {self._expected_vsix_version}",
            (
                "修復は不要です。"
                if status == "passed"
                else "vscode-extension/dist の最新 VSIX を Install from VSIX で"
                "再インストールしてください。"
            ),
            code="version_match" if status == "passed" else "version_mismatch",
        )

    def _bridge_check(
        self, report: ExtensionDiagnosticReport | None, fresh: bool
    ) -> dict[str, object]:
        if not self._bridge_enabled:
            return _check(
                "bridge",
                "Local Bridge",
                "blocked",
                "Web の Local Bridge Token が設定されていません。",
                "Web 起動前に OPERAMIND_BRIDGE_TOKEN を設定してください。"
                "Token は画面に表示しません。",
                code="bridge_disabled",
            )
        if report is None or not fresh:
            return _check(
                "bridge",
                "Local Bridge",
                "warning",
                "Web 側 Bridge は有効ですが、VS Code の現在状態を受信していません。",
                "VS Code で Bridge Token を SecretStorage に登録し、"
                "ローカル環境診断を実行してください。",
                code="extension_not_observed",
            )
        passed = report.bridge_url_loopback and report.bridge_token_configured
        return _check(
            "bridge",
            "Local Bridge",
            "passed" if passed else "blocked",
            (
                "loopback Bridge へ認証付きで接続できました。"
                if passed
                else "VS Code の Bridge URL または Token 設定が不完全です。"
            ),
            (
                "修復は不要です。"
                if passed
                else "Bridge URL を loopback に戻し、Bridge Token を SecretStorage へ"
                "再登録してください。"
            ),
            code="bridge_ready" if passed else "bridge_client_incomplete",
        )

    def _mcp_check(
        self, report: ExtensionDiagnosticReport | None, fresh: bool
    ) -> dict[str, object]:
        missing = self._missing_extension_check(
            "mcp_tools",
            "MCP ツール",
            "VS Code の MCP: List Servers から operaMind を起動し、再診断してください。",
            report,
            fresh,
        )
        if missing is not None:
            return missing
        assert report is not None
        actual = set(report.mcp_tool_names)
        expected = set(self._expected_mcp_tools)
        missing_names = sorted(expected - actual)
        extra_names = sorted(actual - expected)
        passed = not missing_names and not extra_names and len(actual) == len(expected)
        return _check(
            "mcp_tools",
            "MCP ツール",
            "passed" if passed else "blocked",
            (
                f"必要な {len(expected)} 個の MCP ツールを確認しました。"
                if passed
                else f"MCP ツールは {len(actual)} / {len(expected)} 個です。"
            ),
            (
                "修復は不要です。"
                if passed
                else "operaMind MCP の PostgreSQL 入力、起動ログ、Migration を確認して"
                "再起動してください。"
            ),
            code="tool_set_match" if passed else "tool_set_mismatch",
            details={"missing": missing_names, "unexpected": extra_names},
        )

    def _trust_check(
        self, report: ExtensionDiagnosticReport | None, fresh: bool
    ) -> dict[str, object]:
        missing = self._missing_extension_check(
            "workspace_trust",
            "Workspace Trust",
            "対象 Workspace でローカル環境診断を実行してください。",
            report,
            fresh,
        )
        if missing is not None:
            return missing
        assert report is not None
        return _boolean_check(
            "workspace_trust",
            "Workspace Trust",
            report.workspace_trusted,
            "Workspace は Trusted です。",
            "Workspace Trust をユーザー自身で確認してください。"
            "OperaMind は Trust を自動変更しません。",
            "workspace_trusted",
            "workspace_untrusted",
        )

    def _linked_worktree_check(
        self, report: ExtensionDiagnosticReport | None, fresh: bool
    ) -> dict[str, object]:
        missing = self._missing_extension_check(
            "linked_worktree",
            "linked worktree",
            "対象の隔離 linked worktree を VS Code で開いて診断してください。",
            report,
            fresh,
        )
        if missing is not None:
            return missing
        assert report is not None
        return _boolean_check(
            "linked_worktree",
            "linked worktree",
            report.linked_worktree,
            "現在の Workspace は隔離 linked worktree です。",
            "登録 Repository から git worktree add で隔離 Workspace を作成し、"
            "そのフォルダーを開いてください。",
            "linked_worktree_verified",
            "linked_worktree_required",
        )

    def _copilot_check(
        self, report: ExtensionDiagnosticReport | None, fresh: bool
    ) -> dict[str, object]:
        missing = self._missing_extension_check(
            "copilot",
            "GitHub Copilot",
            "VS Code で GitHub Copilot Chat を有効化し、ローカル環境診断を実行してください。",
            report,
            fresh,
        )
        if missing is not None:
            return missing
        assert report is not None
        available = (
            report.copilot_extension_installed
            and report.copilot_extension_active
            and report.copilot_model_api_available
            and report.copilot_model_count > 0
        )
        version = report.copilot_extension_version or "不明"
        return _check(
            "copilot",
            "GitHub Copilot",
            "warning" if available else "blocked",
            (
                f"GitHub Copilot Chat {version} と "
                f"{report.copilot_model_count} モデルを確認しました。Credit/Quota は未検証です。"
                if available
                else "GitHub Copilot Chat または Copilot モデルを利用できません。"
            ),
            (
                "モデル要求は送信していません。Chat の Credit/Quota 表示は"
                "実会話前に確認してください。"
                if available
                else "Copilot Chat のインストール、サインイン、組織 Policy、"
                "Credit/Quota を確認してください。"
            ),
            code=(
                "copilot_model_available_quota_unverified" if available else "copilot_unavailable"
            ),
        )

    @staticmethod
    def _missing_extension_check(
        check_id: str,
        label: str,
        guidance: str,
        report: ExtensionDiagnosticReport | None,
        fresh: bool,
    ) -> dict[str, object] | None:
        if report is None:
            return _check(
                check_id,
                label,
                "blocked",
                "VS Code から診断結果を受信していません。",
                guidance,
                code="extension_report_missing",
            )
        if not fresh:
            return _check(
                check_id,
                label,
                "blocked",
                "VS Code の診断結果が 5 分より古いため使用できません。",
                guidance,
                code="extension_report_stale",
            )
        return None


def _boolean_check(
    check_id: str,
    label: str,
    passed: bool,
    passed_summary: str,
    repair: str,
    passed_code: str,
    blocked_code: str,
) -> dict[str, object]:
    return _check(
        check_id,
        label,
        "passed" if passed else "blocked",
        passed_summary if passed else repair,
        "修復は不要です。" if passed else repair,
        code=passed_code if passed else blocked_code,
    )


def _check(
    check_id: str,
    label: str,
    status: DiagnosticStatus,
    summary: str,
    remediation: str,
    *,
    code: str,
    details: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "check_id": check_id,
        "label": label,
        "status": status,
        "code": code,
        "summary": summary,
        "remediation": remediation,
        "details": cast(dict[str, object], details or {}),
    }
