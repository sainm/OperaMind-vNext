from __future__ import annotations

import os
from pathlib import Path

import pytest

from operamind.commands import local


def test_local_cli_loads_env_before_dispatching_migrate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".env").write_text(
        "OPERAMIND_DATABASE_URL=postgresql:///operamind_vnext\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPERAMIND_DATABASE_URL", raising=False)
    received: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        local.migrate,
        "main",
        lambda argv: received.append(tuple(argv or ())) or 0,
    )

    assert local.main(("--root", str(tmp_path), "migrate")) == 0
    assert os.environ["OPERAMIND_DATABASE_URL"] == "postgresql:///operamind_vnext"
    assert received == [("--root", str(tmp_path.resolve()))]


def test_local_cli_reports_a_missing_env_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert local.main(("--root", str(tmp_path), "web")) == 2
    assert "failed to load environment file" in capsys.readouterr().err
