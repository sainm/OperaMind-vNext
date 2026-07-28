"""Sanitized local Evidence storage shared by bounded test-data executors."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(authorization|password|passwd|"
    r"(?:access|refresh|id|auth|session|csrf)?[_-]?token|"
    r"(?:client|api)?[_-]?secret|(?:api|private|signing)[_-]?key|"
    r"cookie|set-cookie|credentials?)\b"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
_CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SENSITIVE_KEY_PARTS = frozenset(
    {
        "authorization",
        "credential",
        "credentials",
        "cookie",
        "passwd",
        "password",
        "secret",
        "token",
    }
)


@dataclass(frozen=True, slots=True)
class StoredBrowserEvidence:
    evidence_id: str
    scenario_id: str
    evidence_type: str
    evidence_ref: str
    content_digest: str
    sanitized: bool = True


class LocalEvidenceStore:
    """Write sanitized Evidence under one approved root and return opaque refs."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def store_json(
        self,
        *,
        project_id: str,
        run_id: str,
        evidence_id: str,
        scenario_id: str,
        evidence_type: str,
        payload: object,
    ) -> StoredBrowserEvidence:
        encoded = json.dumps(
            _sanitize_json(payload),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode()
        return self._store(
            project_id=project_id,
            run_id=run_id,
            evidence_id=evidence_id,
            scenario_id=scenario_id,
            evidence_type=evidence_type,
            extension="json",
            content=encoded,
        )

    def store_screenshot(
        self,
        *,
        project_id: str,
        run_id: str,
        evidence_id: str,
        scenario_id: str,
        content: bytes,
    ) -> StoredBrowserEvidence:
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Browser screenshot Evidence must be PNG")
        return self._store(
            project_id=project_id,
            run_id=run_id,
            evidence_id=evidence_id,
            scenario_id=scenario_id,
            evidence_type="screenshot",
            extension="png",
            content=content,
        )

    def _store(
        self,
        *,
        project_id: str,
        run_id: str,
        evidence_id: str,
        scenario_id: str,
        evidence_type: str,
        extension: str,
        content: bytes,
    ) -> StoredBrowserEvidence:
        for value in (project_id, run_id, evidence_id, scenario_id, evidence_type, extension):
            if _SAFE_COMPONENT.fullmatch(value) is None:
                raise ValueError(f"Unsafe Browser Evidence path component: {value!r}")
        directory = (self._root / project_id / run_id).resolve()
        if not directory.is_relative_to(self._root):
            raise ValueError("Browser Evidence path escapes approved root")
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{evidence_id}.{extension}"
        temporary = directory / f".{evidence_id}.{extension}.tmp"
        digest = hashlib.sha256(content).hexdigest()
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError(
                    f"Browser Evidence identity has different content: {evidence_id}"
                )
        else:
            temporary.write_bytes(content)
            temporary.replace(target)
        return StoredBrowserEvidence(
            evidence_id=evidence_id,
            scenario_id=scenario_id,
            evidence_type=evidence_type,
            evidence_ref=f"evidence://{project_id}/{run_id}/{evidence_id}",
            content_digest=digest,
        )


def _sanitize_json(value: object) -> object:
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]" if _is_sensitive_key(str(key)) else _sanitize_json(item)
            )
            for key, item in value.items()
        }
    return value


def _is_sensitive_key(value: str) -> bool:
    normalized = _CAMEL_CASE_BOUNDARY.sub("_", value).casefold()
    parts = tuple(part for part in re.split(r"[^a-z0-9]+", normalized) if part)
    if any(part in _SENSITIVE_KEY_PARTS for part in parts):
        return True
    return any(
        pair in {("api", "key"), ("private", "key"), ("signing", "key")}
        for pair in pairwise(parts)
    )


def _sanitize_text(value: str) -> str:
    redacted = _BEARER.sub("Bearer [REDACTED]", value)
    return _SENSITIVE_ASSIGNMENT.sub(r"\1\2[REDACTED]", redacted)
