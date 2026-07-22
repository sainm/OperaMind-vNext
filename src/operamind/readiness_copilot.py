"""Validate a VS Code GitHub Copilot session before accepting a live receipt."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

COPILOT_RECEIPT_REQUIRED_TOOLS = (
    "copilot_get_coding_task",
    "copilot_run_task_command",
    "copilot_validate_task_diff",
    "copilot_record_task_result",
)


def inspect_vscode_copilot_session(
    path: Path,
    *,
    request_id: str,
    required_tools: tuple[str, ...] = COPILOT_RECEIPT_REQUIRED_TOOLS,
) -> dict[str, object]:
    """Return sanitized proof metadata or reject an incomplete/non-Copilot request."""

    data = path.resolve().read_bytes()
    if len(data) > 32 * 1024 * 1024:
        raise ValueError("VS Code Copilot session exceeds the 32 MiB inspection limit")
    records = _jsonl_records(data)
    snapshots = [
        value
        for record in records
        if record.get("kind") == 0
        and isinstance((value := record.get("v")), dict)
        and isinstance(value.get("sessionId"), str)
    ]
    if not snapshots:
        raise ValueError("VS Code Copilot session metadata is missing")
    snapshot = snapshots[-1]
    if snapshot.get("version") != 3 or snapshot.get("responderUsername") != "GitHub Copilot":
        raise ValueError("Session is not a supported VS Code GitHub Copilot transcript")

    requests = {
        str(value["requestId"]): value
        for record in records
        for value in _walk(record)
        if _is_request(value)
    }
    request = requests.get(request_id)
    if request is None:
        raise ValueError(f"VS Code Copilot request not found: {request_id}")

    agent = _mapping(request, "agent")
    extension = _mapping(agent, "extensionId")
    if extension.get("_lower") != "github.copilot-chat":
        raise ValueError("Request was not handled by GitHub Copilot Chat")
    if agent.get("publisherDisplayName") != "GitHub" or agent.get("fullName") != "GitHub Copilot":
        raise ValueError("GitHub Copilot extension identity is incomplete")

    input_state = _mapping(snapshot, "inputState")
    selected_model = _mapping(input_state, "selectedModel")
    model = _mapping(selected_model, "metadata")
    auth = _mapping(model, "auth")
    if model.get("vendor") != "copilot" or model.get("isBYOK") is not False:
        raise ValueError("Session did not use the GitHub Copilot model provider")
    if auth.get("providerLabel") != "GitHub Copilot" or not auth.get("accountLabel"):
        raise ValueError("Signed-in GitHub Copilot account metadata is missing")

    result = _mapping(request, "result")
    error = result.get("errorDetails")
    if isinstance(error, Mapping):
        code = str(error.get("code") or "unknown")
        raise ValueError(f"GitHub Copilot request did not complete successfully: {code}")
    result_metadata = _mapping(result, "metadata")
    session_id = str(snapshot["sessionId"])
    if result_metadata.get("sessionId") != session_id:
        raise ValueError("GitHub Copilot response session ID does not match the transcript")
    model_state = _mapping(request, "modelState")
    completed_at = model_state.get("completedAt")
    if not isinstance(completed_at, int) or completed_at <= 0:
        raise ValueError("GitHub Copilot request has no completion timestamp")

    invocations = [
        value
        for value in _walk(request.get("response", []))
        if value.get("kind") == "toolInvocationSerialized"
        and value.get("isComplete") is True
        and isinstance(value.get("toolId"), str)
    ]
    actual_tools = {str(value["toolId"]) for value in invocations}
    missing = [
        name
        for name in required_tools
        if not any(tool == name or tool.endswith("_" + name) for tool in actual_tools)
    ]
    if missing:
        raise ValueError("Required OperaMind MCP tools were not completed: " + ", ".join(missing))
    for invocation in invocations:
        tool_id = str(invocation["toolId"])
        if any(tool_id == name or tool_id.endswith("_" + name) for name in required_tools):
            confirmation = invocation.get("isConfirmed")
            if not isinstance(confirmation, Mapping) or not confirmation.get("type"):
                raise ValueError(f"OperaMind MCP tool approval is not confirmed: {tool_id}")

    response_id = request.get("responseId")
    if not isinstance(response_id, str) or not response_id:
        raise ValueError("GitHub Copilot response ID is missing")
    return {
        "vscode_session_id": session_id,
        "vscode_chat_format_version": 3,
        "request_id": request_id,
        "response_id": response_id,
        "copilot_extension_id": str(extension.get("value")),
        "copilot_extension_version": str(agent.get("extensionVersion")),
        "copilot_account": str(auth["accountLabel"]),
        "copilot_model_id": str(request.get("modelId")),
        "copilot_model_version": str(result.get("details") or model.get("version")),
        "copilot_is_byok": False,
        "completed_at": datetime.fromtimestamp(completed_at / 1000, UTC).isoformat(),
        "completed_mcp_tools": list(required_tools),
        "session_transcript_sha256": hashlib.sha256(data).hexdigest(),
    }


def _jsonl_records(data: bytes) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(data.splitlines(), start=1):
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"Invalid VS Code session JSONL at line {line_number}") from error
        if not isinstance(value, dict):
            raise ValueError(f"Invalid VS Code session record at line {line_number}")
        records.append(value)
    return records


def _walk(value: object) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _is_request(value: dict[str, Any]) -> bool:
    return (
        isinstance(value.get("requestId"), str)
        and isinstance(value.get("agent"), dict)
        and isinstance(value.get("result"), dict)
        and isinstance(value.get("message"), dict)
    )


def _mapping(value: Mapping[str, object], key: str) -> Mapping[str, Any]:
    nested = value.get(key)
    if not isinstance(nested, Mapping):
        raise ValueError(f"VS Code Copilot session field is missing: {key}")
    return nested
