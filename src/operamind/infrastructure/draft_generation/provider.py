"""Import a VS Code GitHub Copilot response through an untrusted JSON boundary."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from jsonschema import Draft202012Validator, FormatChecker


@dataclass(frozen=True, slots=True)
class DraftGenerationResponse:
    """Schema-validated but still semantically untrusted Copilot response."""

    payload: dict[str, Any]
    provider_id: str
    stdout_path: Path
    stderr_path: Path
    response_path: Path


class DraftGenerationProvider(Protocol):
    """Supply one proposed case from a bounded, non-secret JSON context."""

    def generate(
        self,
        *,
        prompt: str,
        workspace_root: Path,
        output_root: Path,
    ) -> DraftGenerationResponse: ...


class FileDraftGenerationProvider:
    """Import one GitHub Copilot response through the same untrusted schema gate."""

    def __init__(self, *, repository_root: Path, response_path: Path) -> None:
        self._root = repository_root.resolve(strict=True)
        self._source = response_path.resolve(strict=True)
        if not self._source.is_file() or self._source.is_symlink():
            raise ValueError("Copilot Draft response must be a safe regular file")
        self._schema = _load_object(
            self._root / "drafts/schemas/change-draft-ai-response.schema.json"
        )

    def generate(
        self,
        *,
        prompt: str,
        workspace_root: Path,
        output_root: Path,
    ) -> DraftGenerationResponse:
        if not prompt.strip() or not workspace_root.resolve(strict=True).is_dir():
            raise ValueError("Copilot Draft import requires prompt and workspace evidence")
        payload = _load_object(self._source)
        errors = sorted(
            Draft202012Validator(self._schema, format_checker=FormatChecker()).iter_errors(payload),
            key=lambda error: list(error.absolute_path),
        )
        if errors:
            first = errors[0]
            location = ".".join(str(part) for part in first.absolute_path) or "$"
            raise ValueError(f"Invalid Copilot Draft response at {location}: {first.message}")
        output = output_root.absolute()
        output.mkdir(parents=True, exist_ok=True)
        response_path = output / "ai-response.json"
        stdout_path = output / "provider.stdout.log"
        stderr_path = output / "provider.stderr.log"
        response_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        stdout_path.write_text(
            f"Imported VS Code GitHub Copilot response from {self._source}\n",
            encoding="utf-8",
        )
        stderr_path.write_text("", encoding="utf-8")
        return DraftGenerationResponse(
            payload=payload,
            provider_id="github-copilot-vscode",
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            response_path=response_path,
        )


def _load_object(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return cast(dict[str, Any], value)
