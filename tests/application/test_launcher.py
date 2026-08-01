from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from operamind.commands import launcher


def test_packaged_document_runtime_requires_extractor_metadata(
    monkeypatch: MonkeyPatch,
) -> None:
    def distribution_version(distribution: str) -> str:
        if distribution == "python-docx":
            raise PackageNotFoundError(distribution)
        return "1.0"

    monkeypatch.setattr(launcher, "distribution_version", distribution_version)

    with pytest.raises(ValueError) as raised:
        launcher._verify_packaged_document_runtime()

    assert str(raised.value) == (
        "Document extractor package metadata is missing: python-docx"
    )


def test_launcher_prepares_runtime_and_starts_web(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setenv("OPERAMIND_DATABASE_URL", "postgresql:///launcher-test")
    called: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(launcher, "_is_operamind_running", lambda _url: False)
    monkeypatch.setattr(
        launcher.web,
        "main",
        lambda args: called.append(("web", tuple(args))) or 0,
    )

    result = launcher.main(
        ("--root", str(root), "--data-directory", str(tmp_path / "data"), "--no-browser")
    )

    assert result == 0
    assert called == [
        (
            "web",
            ("--root", str(root), "--host", "127.0.0.1", "--port", "8765"),
        )
    ]
    assert (tmp_path / "data" / "bridge-token").is_file()
    assert (tmp_path / "data" / "runtime.json").is_file()


def test_launcher_internal_mcp_uses_same_runtime_root(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setenv("OPERAMIND_DATABASE_URL", "postgresql:///launcher-test")
    received: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        launcher.mcp_server,
        "main",
        lambda args: received.append(tuple(args)) or 0,
    )

    result = launcher.main(
        (
            "--mcp",
            "--root",
            str(root),
            "--data-directory",
            str(tmp_path / "data"),
        )
    )

    assert result == 0
    assert received == [("--root", str(root))]


def test_launcher_reports_missing_database_before_starting_web(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    reported: list[str] = []
    monkeypatch.delenv("OPERAMIND_DATABASE_URL", raising=False)
    monkeypatch.setattr(launcher, "_report_error", reported.append)

    result = launcher.main(
        ("--root", str(root), "--data-directory", str(tmp_path / "data"), "--no-browser")
    )

    assert result == 2
    assert reported and "config.env" in reported[0]
