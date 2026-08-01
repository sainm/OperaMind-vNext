from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from operamind.local_installation import (
    ensure_bridge_token,
    installation_paths,
    launcher_invocation,
    local_data_directory,
    prepare_runtime_root,
    write_runtime_manifest,
)


def test_local_data_directory_is_platform_specific() -> None:
    home = Path("/Users/tester")
    assert local_data_directory(platform_name="darwin", environ={}, home=home) == (
        home / "Library" / "Application Support" / "OperaMind"
    )
    assert local_data_directory(
        platform_name="win32",
        environ={"LOCALAPPDATA": "C:/Users/tester/AppData/Local"},
        home=home,
    ) == Path("C:/Users/tester/AppData/Local/OperaMind")
    assert local_data_directory(
        platform_name="linux",
        environ={"XDG_DATA_HOME": "/data/tester"},
        home=home,
    ) == Path("/data/tester/operamind")


def test_bridge_token_is_created_once_with_restricted_permissions(tmp_path: Path) -> None:
    path = tmp_path / "state" / "bridge-token"

    created = ensure_bridge_token(path)

    assert len(created) >= 48
    assert ensure_bridge_token(path) == created
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_launcher_invocation_uses_packaged_executable_without_python_module(
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "OperaMind.exe"
    launcher.touch()
    mcp = tmp_path / "OperaMindMcp.exe"
    mcp.touch()
    command, args = launcher_invocation(
        resource_root=tmp_path,
        executable=launcher,
        frozen=True,
        platform_name="win32",
    )

    assert command == str(mcp)
    assert args == ("--mcp",)


def test_windows_packaged_launcher_requires_mcp_companion(tmp_path: Path) -> None:
    launcher = tmp_path / "OperaMind.exe"
    launcher.touch()

    with pytest.raises(ValueError, match="MCP companion"):
        launcher_invocation(
            resource_root=tmp_path,
            executable=launcher,
            frozen=True,
            platform_name="win32",
        )


@pytest.mark.skipif(os.name == "nt", reason="Windows virtualenv uses launchers, not POSIX symlinks")
def test_source_launcher_preserves_virtualenv_python_symlink(tmp_path: Path) -> None:
    base_python = tmp_path / "runtime" / "python3.12"
    base_python.parent.mkdir()
    base_python.touch()
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(base_python)

    command, _args = launcher_invocation(
        resource_root=tmp_path,
        executable=venv_python,
        frozen=False,
    )

    assert command == str(venv_python.absolute())


def test_runtime_manifest_exposes_mcp_without_workspace_dependency(tmp_path: Path) -> None:
    paths = installation_paths(data_directory=tmp_path / "user-data")
    paths.bridge_token_file.parent.mkdir(parents=True)
    paths.bridge_token_file.write_text("secret\n", encoding="utf-8")

    payload = write_runtime_manifest(
        paths,
        resource_root=tmp_path / "resources",
        web_url="http://127.0.0.1:8765",
        executable=tmp_path / "OperaMind.exe",
        frozen=True,
        platform_name="darwin",
    )

    assert payload["product"] == "operamind"
    assert payload["mcp"] == {
        "command": str((tmp_path / "OperaMind.exe").resolve()),
        "args": ["--mcp"],
        "cwd": str((tmp_path / "resources").resolve()),
    }
    assert json.loads(paths.runtime_manifest_file.read_text(encoding="utf-8")) == payload


def test_windows_runtime_manifest_points_to_mcp_companion(tmp_path: Path) -> None:
    paths = installation_paths(data_directory=tmp_path / "user-data")
    launcher = tmp_path / "package" / "OperaMind.exe"
    launcher.parent.mkdir()
    launcher.touch()
    mcp = launcher.with_name("OperaMindMcp.exe")
    mcp.touch()

    payload = write_runtime_manifest(
        paths,
        resource_root=tmp_path / "resources",
        web_url="http://127.0.0.1:8765",
        executable=launcher,
        frozen=True,
        platform_name="win32",
    )

    assert payload["mcp"] == {
        "command": str(mcp),
        "args": ["--mcp"],
        "cwd": str((tmp_path / "resources").resolve()),
    }


def test_frozen_runtime_copies_all_web_diagnostics_resources(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    source = tmp_path / "bundle"
    for name in ("contracts", "golden-dataset", "migrations", "profiles", "readiness"):
        directory = source / name
        directory.mkdir(parents=True)
        (directory / "resource.txt").write_text(name, encoding="utf-8")
    extension = source / "vscode-extension"
    extension.mkdir()
    (extension / "package.json").write_text('{"version":"1.0.0"}', encoding="utf-8")
    paths = installation_paths(data_directory=tmp_path / "data")
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    result = prepare_runtime_root(source, paths)

    assert result == paths.runtime_root
    assert (result / "migrations" / "resource.txt").is_file()
    assert (result / "vscode-extension" / "package.json").is_file()
