"""Per-user installation state shared by the OperaMind launcher and VS Code."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

PRODUCT_ID = "operamind"
RUNTIME_MANIFEST_SCHEMA = 1
_BUNDLED_RESOURCE_DIRECTORIES = (
    "contracts",
    "golden-dataset",
    "migrations",
    "profiles",
    "readiness",
)
_BUNDLED_RESOURCE_FILES = ("vscode-extension/package.json",)


@dataclass(frozen=True)
class LocalInstallationPaths:
    """Files owned by one local OperaMind installation."""

    data_directory: Path
    config_file: Path
    bridge_token_file: Path
    runtime_manifest_file: Path
    runtime_root: Path


def application_version() -> str:
    """Return the installed distribution version without requiring package metadata."""

    try:
        return version("operamind-vnext")
    except PackageNotFoundError:
        return "0.1.0.dev0"


def local_data_directory(
    *,
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Resolve a user-owned, non-workspace runtime directory on each desktop OS."""

    platform = sys.platform if platform_name is None else platform_name
    environment = os.environ if environ is None else environ
    user_home = Path.home() if home is None else home
    if platform == "win32":
        windows_base = environment.get("LOCALAPPDATA")
        if not windows_base:
            raise ValueError("LOCALAPPDATA is required on Windows")
        return Path(windows_base) / "OperaMind"
    if platform == "darwin":
        return user_home / "Library" / "Application Support" / "OperaMind"
    xdg_data_home = environment.get("XDG_DATA_HOME")
    linux_base = Path(xdg_data_home) if xdg_data_home else user_home / ".local" / "share"
    return linux_base / PRODUCT_ID


def installation_paths(*, data_directory: Path | None = None) -> LocalInstallationPaths:
    directory = (data_directory or local_data_directory()).expanduser().resolve()
    return LocalInstallationPaths(
        data_directory=directory,
        config_file=directory / "config.env",
        bridge_token_file=directory / "bridge-token",
        runtime_manifest_file=directory / "runtime.json",
        runtime_root=directory / "runtime" / application_version(),
    )


def source_resource_root() -> Path:
    """Locate immutable application resources in source and frozen builds."""

    frozen_root = getattr(sys, "_MEIPASS", None)
    if isinstance(frozen_root, str) and frozen_root:
        return Path(frozen_root).resolve()
    return Path(__file__).resolve().parents[2]


def prepare_runtime_root(source_root: Path, paths: LocalInstallationPaths) -> Path:
    """Copy bundled resources to persistent user storage when running frozen."""

    root = source_root.resolve()
    if getattr(sys, "frozen", False) is not True:
        return root
    paths.runtime_root.mkdir(parents=True, exist_ok=True)
    for name in _BUNDLED_RESOURCE_DIRECTORIES:
        source = root / name
        if not source.is_dir():
            raise ValueError(f"Bundled OperaMind resource is missing: {source}")
        shutil.copytree(source, paths.runtime_root / name, dirs_exist_ok=True)
    for name in _BUNDLED_RESOURCE_FILES:
        source = root / name
        if not source.is_file():
            raise ValueError(f"Bundled OperaMind resource is missing: {source}")
        destination = paths.runtime_root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return paths.runtime_root


def ensure_bridge_token(path: Path) -> str:
    """Create or read a user-only token without putting it in a workspace file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if not token:
            raise ValueError(f"Bridge token file is blank: {path}")
    else:
        token = secrets.token_urlsafe(48)
        path.write_text(f"{token}\n", encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)
    return token


def launcher_invocation(
    *,
    resource_root: Path,
    executable: Path | None = None,
    frozen: bool | None = None,
    platform_name: str | None = None,
) -> tuple[str, tuple[str, ...]]:
    """Return the command VS Code should use for an MCP child process."""

    command = os.path.abspath(str(executable or Path(sys.executable)))
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if is_frozen:
        platform = sys.platform if platform_name is None else platform_name
        if platform == "win32":
            mcp_executable = Path(command).with_name("OperaMindMcp.exe")
            if not mcp_executable.is_file():
                raise ValueError(f"Packaged MCP companion is missing: {mcp_executable}")
            command = str(mcp_executable)
        return command, ("--mcp",)
    return command, (
        "-m",
        "operamind.commands.launcher",
        "--mcp",
        "--root",
        str(resource_root.resolve()),
    )


def write_runtime_manifest(
    paths: LocalInstallationPaths,
    *,
    resource_root: Path,
    web_url: str,
    executable: Path | None = None,
    frozen: bool | None = None,
    platform_name: str | None = None,
) -> dict[str, object]:
    """Publish the local runtime contract atomically for the VS Code extension."""

    command, args = launcher_invocation(
        resource_root=resource_root,
        executable=executable,
        frozen=frozen,
        platform_name=platform_name,
    )
    payload: dict[str, object] = {
        "schemaVersion": RUNTIME_MANIFEST_SCHEMA,
        "product": PRODUCT_ID,
        "version": application_version(),
        "webUrl": web_url,
        "mcp": {
            "command": command,
            "args": list(args),
            "cwd": str(resource_root.resolve()),
        },
        "bridgeTokenFile": str(paths.bridge_token_file.resolve()),
    }
    paths.data_directory.mkdir(parents=True, exist_ok=True)
    temporary = paths.runtime_manifest_file.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(paths.runtime_manifest_file)
    return payload


def load_environment_candidates(paths: LocalInstallationPaths, candidates: Sequence[Path]) -> None:
    """Load user configuration first, followed by non-overriding source defaults."""

    from operamind.environment_file import load_environment_file

    for candidate in (paths.config_file, *candidates):
        if candidate.is_file():
            load_environment_file(candidate)
