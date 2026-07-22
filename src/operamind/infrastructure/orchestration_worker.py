"""Fixed-command handler adapter for the OrchestrationTask worker."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from operamind.application.orchestration_worker import (
    OrchestrationTaskExecutionCancelled,
    OrchestrationTaskExecutionContext,
    OrchestrationTaskExecutionError,
    OrchestrationTaskExecutionResult,
)


@dataclass(frozen=True, slots=True)
class FixedCommandHandlerConfiguration:
    action: str
    argv: tuple[str, ...]
    working_directory: Path
    timeout_seconds: float = 900.0
    environment_keys: tuple[str, ...] = ()
    max_output_bytes: int = 1_000_000

    def __post_init__(self) -> None:
        if not self.action.strip():
            raise ValueError("worker handler action must not be blank")
        if not self.argv or any(not value for value in self.argv):
            raise ValueError("worker handler command must not be blank")
        if not self.working_directory.is_dir():
            raise ValueError("worker handler working directory must exist")
        if not 1 <= self.timeout_seconds <= 86400:
            raise ValueError("worker handler timeout is out of bounds")
        if not 1024 <= self.max_output_bytes <= 10_000_000:
            raise ValueError("worker handler output limit is out of bounds")
        if any(not key or "=" in key for key in self.environment_keys):
            raise ValueError("worker handler environment keys are invalid")


class FixedCommandOrchestrationTaskHandler:
    """Execute an operator-configured argv without interpreting Task text as a command."""

    def __init__(self, configuration: FixedCommandHandlerConfiguration) -> None:
        self._configuration = configuration

    def execute(
        self,
        *,
        task: Mapping[str, object],
        context: OrchestrationTaskExecutionContext,
    ) -> OrchestrationTaskExecutionResult:
        context.raise_if_cancelled()
        payload = json.dumps(
            {"protocol_version": "orchestration_worker_handler_v1", "task": task},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        environment = {
            key: environment_value
            for key in ("HOME", "LANG", "LC_ALL", "PATH", "TMPDIR")
            if (environment_value := os.environ.get(key)) is not None
        }
        environment.update(
            {
                key: os.environ[key]
                for key in self._configuration.environment_keys
                if key in os.environ
            }
        )
        try:
            process = subprocess.Popen(
                self._configuration.argv,
                cwd=self._configuration.working_directory,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                start_new_session=True,
            )
        except OSError as error:
            raise OrchestrationTaskExecutionError(
                "Task handler could not be started", error_kind="handler_launch_failed"
            ) from error

        deadline = time.monotonic() + self._configuration.timeout_seconds
        stdout = ""
        stderr = ""
        first_communicate = True
        while True:
            if context.cancelled:
                _terminate(process)
                raise OrchestrationTaskExecutionCancelled(
                    "Task handler stopped after worker cancellation"
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate(process)
                raise OrchestrationTaskExecutionError(
                    "Task handler exceeded its configured timeout",
                    error_kind="handler_timeout",
                )
            try:
                stdout, stderr = process.communicate(
                    input=payload if first_communicate else None,
                    timeout=min(0.1, remaining),
                )
                break
            except subprocess.TimeoutExpired:
                first_communicate = False

        output_bytes = len(stdout.encode("utf-8")) + len(stderr.encode("utf-8"))
        if output_bytes > self._configuration.max_output_bytes:
            raise OrchestrationTaskExecutionError(
                "Task handler output exceeded its configured limit",
                error_kind="handler_output_too_large",
            )
        if process.returncode != 0:
            raise OrchestrationTaskExecutionError(
                f"Task handler exited with code {process.returncode}",
                error_kind="handler_nonzero_exit",
            )
        try:
            value: object = json.loads(stdout)
        except json.JSONDecodeError as error:
            raise OrchestrationTaskExecutionError(
                "Task handler returned invalid JSON", error_kind="handler_invalid_json"
            ) from error
        return _parse_result(value)


def load_fixed_command_handlers(
    *, path: Path, repository_root: Path
) -> dict[str, FixedCommandOrchestrationTaskHandler]:
    """Load bounded argv handlers from an operator-owned JSON file."""
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {"schema_version", "handlers"}:
        raise ValueError("worker handler config requires schema_version and handlers")
    if value["schema_version"] != "orchestration_worker_handlers_v1":
        raise ValueError("worker handler config schema_version is unsupported")
    raw_handlers = value["handlers"]
    if not isinstance(raw_handlers, list) or not raw_handlers:
        raise ValueError("worker handler config requires a non-empty handlers array")
    handlers: dict[str, FixedCommandOrchestrationTaskHandler] = {}
    root = repository_root.resolve(strict=True)
    for raw in raw_handlers:
        if not isinstance(raw, dict):
            raise ValueError("worker handler entry must be an object")
        allowed = {
            "action",
            "command",
            "working_directory",
            "timeout_seconds",
            "environment_keys",
            "max_output_bytes",
        }
        if not set(raw) <= allowed or not {"action", "command"} <= set(raw):
            raise ValueError("worker handler entry contains missing or unknown fields")
        action = _text(raw["action"], "worker handler action")
        if action in handlers:
            raise ValueError(f"duplicate worker handler action: {action}")
        command = raw["command"]
        if not isinstance(command, list) or not command:
            raise ValueError("worker handler command must be a non-empty array")
        argv = tuple(_text(item, "worker handler command argument") for item in command)
        working_value = raw.get("working_directory", ".")
        working = _inside_root(root, _text(working_value, "worker handler working directory"))
        environment_value = raw.get("environment_keys", [])
        if not isinstance(environment_value, list):
            raise ValueError("worker handler environment_keys must be an array")
        environment_keys = tuple(
            _text(item, "worker handler environment key") for item in environment_value
        )
        configuration = FixedCommandHandlerConfiguration(
            action=action,
            argv=argv,
            working_directory=working,
            timeout_seconds=_number(raw.get("timeout_seconds", 900), "timeout_seconds"),
            environment_keys=environment_keys,
            max_output_bytes=_integer(raw.get("max_output_bytes", 1_000_000), "max_output_bytes"),
        )
        handlers[action] = FixedCommandOrchestrationTaskHandler(configuration)
    return handlers


def _parse_result(value: object) -> OrchestrationTaskExecutionResult:
    if not isinstance(value, dict):
        raise OrchestrationTaskExecutionError(
            "Task handler result must be a JSON object", error_kind="handler_invalid_result"
        )
    if set(value) != {"outcome", "summary", "artifact_refs", "evidence"}:
        raise OrchestrationTaskExecutionError(
            "Task handler result fields do not match the protocol",
            error_kind="handler_invalid_result",
        )
    outcome = value["outcome"]
    if outcome not in {"completed", "failed", "blocked"}:
        raise OrchestrationTaskExecutionError(
            "Task handler outcome is invalid", error_kind="handler_invalid_result"
        )
    summary = _text(value["summary"], "Task handler summary")
    artifact_values = value["artifact_refs"]
    if not isinstance(artifact_values, list):
        raise OrchestrationTaskExecutionError(
            "Task handler artifact_refs must be an array",
            error_kind="handler_invalid_result",
        )
    artifacts = tuple(_text(item, "Task handler artifact ref") for item in artifact_values)
    evidence = value["evidence"]
    if not isinstance(evidence, dict):
        raise OrchestrationTaskExecutionError(
            "Task handler evidence must be an object", error_kind="handler_invalid_result"
        )
    try:
        return OrchestrationTaskExecutionResult(
            outcome=outcome,
            summary=summary,
            artifact_refs=artifacts,
            evidence={str(key): item for key, item in evidence.items()},
        )
    except ValueError as error:
        raise OrchestrationTaskExecutionError(
            str(error), error_kind="handler_invalid_result"
        ) from error


def _terminate(process: subprocess.Popen[str]) -> None:
    _signal_process(process, signal.SIGTERM)
    try:
        process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        _signal_process(process, signal.SIGKILL)
        process.communicate()


def _signal_process(process: subprocess.Popen[str], signal_number: int) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal_number)
        elif signal_number == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()
    except ProcessLookupError:
        return


def _inside_root(root: Path, relative: str) -> Path:
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError("worker handler working directory leaves repository root")
    if not path.is_dir():
        raise ValueError("worker handler working directory must be a directory")
    return path


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-blank string")
    return value


def _number(value: object, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"worker handler {label} must be a number")
    return float(value)


def _integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"worker handler {label} must be an integer")
    return value
