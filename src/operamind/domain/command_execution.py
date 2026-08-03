"""Validated, immutable command templates resolved from a versioned Profile."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, cast


@dataclass(frozen=True, slots=True)
class SafeCommandTemplate:
    """One exact argv-based command definition; no shell expression is accepted."""

    command_ref: str
    argv: tuple[str, ...]
    working_directory: str
    timeout_seconds: int
    expected_exit_codes: tuple[int, ...]
    environment_keys: tuple[str, ...]
    output_limit_bytes: int
    failure_policy: str
    purpose: str = "test"
    coverage_report_format: str | None = None
    coverage_report_path: str | None = None

    @classmethod
    def from_profile(
        cls,
        profile: dict[str, Any],
        *,
        command_ref: str,
    ) -> SafeCommandTemplate:
        if profile.get("profile_type") != "CommandExecutionProfile":
            raise ValueError("Bound Profile is not a Command Execution Profile")
        templates = cast(list[dict[str, Any]], profile.get("templates"))
        matches = [template for template in templates if template.get("command_ref") == command_ref]
        if not matches:
            raise ValueError(f"Command Profile does not define command_ref: {command_ref}")
        if len(matches) != 1:
            raise RuntimeError(
                f"Validated Command Profile has duplicate command_ref: {command_ref}"
            )
        template = matches[0]
        purpose = str(template.get("purpose") or "test")
        coverage_report = cast(dict[str, Any] | None, template.get("coverage_report"))
        return cls(
            command_ref=str(template["command_ref"]),
            argv=tuple(str(value) for value in template["argv"]),
            working_directory=str(template["working_directory"]),
            timeout_seconds=int(template["timeout_seconds"]),
            expected_exit_codes=tuple(int(value) for value in template["expected_exit_codes"]),
            environment_keys=tuple(str(value) for value in template["environment_keys"]),
            output_limit_bytes=int(template["output_limit_bytes"]),
            failure_policy=str(template["failure_policy"]),
            purpose=purpose,
            coverage_report_format=(
                str(coverage_report["format"]) if coverage_report is not None else None
            ),
            coverage_report_path=(
                str(coverage_report["path"]) if coverage_report is not None else None
            ),
        )

    @property
    def digest(self) -> str:
        payload = {
            "argv": list(self.argv),
            "command_ref": self.command_ref,
            "environment_keys": list(self.environment_keys),
            "expected_exit_codes": list(self.expected_exit_codes),
            "failure_policy": self.failure_policy,
            "output_limit_bytes": self.output_limit_bytes,
            "timeout_seconds": self.timeout_seconds,
            "working_directory": self.working_directory,
            "purpose": self.purpose,
            "coverage_report_format": self.coverage_report_format,
            "coverage_report_path": self.coverage_report_path,
        }
        canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()
