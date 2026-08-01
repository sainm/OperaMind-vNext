"""Small platform-aware helpers shared by local process entry points."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# These variables are required by the Windows process loader and temporary-file
# APIs.  They are inherited even when an approved Profile deliberately limits
# the rest of the environment.
WINDOWS_PROCESS_ENVIRONMENT_KEYS = (
    "SystemRoot",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "PATHEXT",
)

# ``subprocess`` only exposes these constants on Windows. Keeping the documented
# Win32 values here lets platform-neutral tests verify the launch contract.
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000


def approved_process_environment(
    environment_keys: tuple[str, ...] | list[str],
    *,
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
) -> dict[str, str]:
    """Build a bounded environment without breaking Windows process startup."""

    source = os.environ if environ is None else environ
    platform = os.name if platform_name is None else platform_name
    keys = list(environment_keys)
    if platform == "nt":
        keys.extend(WINDOWS_PROCESS_ENVIRONMENT_KEYS)
    return {key: source[key] for key in dict.fromkeys(keys) if key in source}


def venv_command(name: str, *, platform_name: str | None = None) -> str:
    """Return a repository-relative command from the local virtualenv."""

    if not name or "/" in name or "\\" in name:
        raise ValueError("Virtualenv command name must be a plain executable name")
    platform = os.name if platform_name is None else platform_name
    if platform == "nt":
        return f".venv/Scripts/{name}.exe"
    return f".venv/bin/{name}"


def subprocess_creation_flags(*, platform_name: str | None = None) -> int:
    """Keep approved Windows child commands hidden and independently terminable."""

    platform = os.name if platform_name is None else platform_name
    if platform != "nt":
        return 0
    return _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW


def terminate_windows_process_tree(process: subprocess.Popen[Any]) -> bool:
    """Terminate a live Windows process and its descendants without a shell."""

    if process.poll() is not None:
        return False
    system_root = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR")
    taskkill = (
        Path(system_root) / "System32" / "taskkill.exe"
        if system_root
        else Path("taskkill.exe")
    )
    try:
        completed = subprocess.run(
            (str(taskkill), "/PID", str(process.pid), "/T", "/F"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=subprocess_creation_flags(platform_name="nt"),
        )
    except OSError:
        process.kill()
        return True
    if completed.returncode != 0 and process.poll() is None:
        process.kill()
    return True
