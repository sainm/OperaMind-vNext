"""Strict, dependency-free loading for repository-local environment files."""

from __future__ import annotations

import os
import re
from collections.abc import MutableMapping
from pathlib import Path

_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def load_environment_file(
    path: Path,
    *,
    environ: MutableMapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Load one trusted ``KEY=value`` file without overriding process variables."""

    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"Environment file is not a regular file: {resolved}")
    target = os.environ if environ is None else environ
    parsed: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        resolved.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        name, separator, raw_value = line.partition("=")
        name = name.strip()
        if not separator or _ENVIRONMENT_NAME.fullmatch(name) is None:
            raise ValueError(f"Invalid environment entry at {resolved}:{line_number}")
        if name in parsed:
            raise ValueError(f"Duplicate environment variable at {resolved}:{line_number}: {name}")
        value = raw_value.strip()
        if value.startswith(("'", '"')):
            quote = value[0]
            if len(value) < 2 or value[-1] != quote:
                raise ValueError(f"Unterminated quoted value at {resolved}:{line_number}")
            value = value[1:-1]
        parsed[name] = value

    applied: list[str] = []
    for name, value in parsed.items():
        if name in target:
            continue
        target[name] = value
        applied.append(name)
    return tuple(applied)
