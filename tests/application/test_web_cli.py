from __future__ import annotations

import pytest

from operamind.commands.web import main


def test_web_cli_rejects_blank_bridge_token(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPERAMIND_DATABASE_URL", "postgresql:///unused")
    monkeypatch.setenv("OPERAMIND_BRIDGE_TOKEN", "   ")
    monkeypatch.setenv("OPERAMIND_WEB_TOKEN", "w" * 32)

    assert main([]) == 2
    assert "OPERAMIND_BRIDGE_TOKEN must not be blank" in capsys.readouterr().err


def test_web_cli_rejects_non_loopback_host_when_bridge_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPERAMIND_DATABASE_URL", "postgresql:///unused")
    monkeypatch.setenv("OPERAMIND_BRIDGE_TOKEN", "local-secret")
    monkeypatch.setenv("OPERAMIND_WEB_TOKEN", "w" * 32)

    assert main(["--host", "0.0.0.0"]) == 2
    assert "OperaMind Web requires a loopback host" in capsys.readouterr().err


def test_web_cli_rejects_non_loopback_host_when_bridge_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPERAMIND_DATABASE_URL", "postgresql:///unused")
    monkeypatch.delenv("OPERAMIND_BRIDGE_TOKEN", raising=False)
    monkeypatch.setenv("OPERAMIND_WEB_TOKEN", "w" * 32)

    assert main(["--host", "0.0.0.0"]) == 2
    assert "OperaMind Web requires a loopback host" in capsys.readouterr().err


def test_web_cli_requires_web_token(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPERAMIND_DATABASE_URL", "postgresql:///unused")
    monkeypatch.delenv("OPERAMIND_BRIDGE_TOKEN", raising=False)
    monkeypatch.delenv("OPERAMIND_WEB_TOKEN", raising=False)

    assert main([]) == 2
    assert "OPERAMIND_WEB_TOKEN is required" in capsys.readouterr().err


def test_web_cli_rejects_weak_web_token(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPERAMIND_DATABASE_URL", "postgresql:///unused")
    monkeypatch.delenv("OPERAMIND_BRIDGE_TOKEN", raising=False)
    monkeypatch.setenv("OPERAMIND_WEB_TOKEN", "too-short")

    assert main([]) == 2
    assert "32-256 character opaque token" in capsys.readouterr().err


def test_web_cli_rejects_invalid_orchestration_parallelism(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPERAMIND_DATABASE_URL", "postgresql:///unused")
    monkeypatch.delenv("OPERAMIND_BRIDGE_TOKEN", raising=False)
    monkeypatch.setenv("OPERAMIND_WEB_TOKEN", "w" * 32)
    monkeypatch.setenv("OPERAMIND_MAX_ACTIVE_TASKS_PER_RUN", "101")

    assert main([]) == 2
    assert "between 1 and 100" in capsys.readouterr().err
