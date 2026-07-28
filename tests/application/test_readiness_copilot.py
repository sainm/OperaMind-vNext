import json
from pathlib import Path

import pytest

from operamind.readiness_copilot import inspect_vscode_copilot_session


def _session(*, error_code: str | None = None, include_result: bool = True) -> bytes:
    snapshot = {
        "kind": 0,
        "v": {
            "version": 3,
            "responderUsername": "GitHub Copilot",
            "sessionId": "session-001",
            "inputState": {
                "selectedModel": {
                    "metadata": {
                        "vendor": "copilot",
                        "version": "model-version",
                        "isBYOK": False,
                        "auth": {
                            "providerLabel": "GitHub Copilot",
                            "accountLabel": "developer",
                        },
                    }
                }
            },
        },
    }
    tools = [
        "mcp_operaMind_copilot_get_coding_task",
        "mcp_operaMind_copilot_record_change_outputs",
        "mcp_operaMind_copilot_record_change_outputs",
        "mcp_operaMind_copilot_validate_task_diff",
        "mcp_operaMind_copilot_record_change_outputs",
        "mcp_operaMind_copilot_run_task_command",
    ]
    if include_result:
        tools.append("mcp_operaMind_copilot_record_task_result")
    request = {
        "requestId": "request-001",
        "responseId": "response-001",
        "modelId": "copilot/auto",
        "agent": {
            "extensionId": {
                "value": "GitHub.copilot-chat",
                "_lower": "github.copilot-chat",
            },
            "extensionVersion": "0.57.0",
            "publisherDisplayName": "GitHub",
            "fullName": "GitHub Copilot",
        },
        "result": {
            "metadata": {"sessionId": "session-001"},
            "details": "Copilot Model",
        },
        "modelState": {"completedAt": 1_784_443_124_779},
        "message": {"text": "verify the unified Change Task"},
        "response": [
            {
                "kind": "toolInvocationSerialized",
                "toolId": tool,
                "isComplete": True,
                "isConfirmed": {"type": 1},
            }
            for tool in tools
        ],
    }
    if error_code is not None:
        request["result"]["errorDetails"] = {"code": error_code}
    delta = {"kind": 2, "v": [request]}
    return (json.dumps(snapshot) + "\n" + json.dumps(delta) + "\n").encode()


def test_inspect_vscode_copilot_session_accepts_completed_change_task(tmp_path: Path) -> None:
    session = tmp_path / "session.jsonl"
    session.write_bytes(_session())

    result = inspect_vscode_copilot_session(session, request_id="request-001")

    assert result["vscode_session_id"] == "session-001"
    assert result["copilot_extension_id"] == "GitHub.copilot-chat"
    assert result["copilot_is_byok"] is False
    assert result["completed_mcp_tools"] == [
        "copilot_get_coding_task",
        "copilot_record_change_outputs",
        "copilot_run_task_command",
        "copilot_validate_task_diff",
        "copilot_record_task_result",
    ]
    assert len(result["session_transcript_sha256"]) == 64


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (_session(error_code="quota_exceeded"), "quota_exceeded"),
        (_session(include_result=False), "copilot_record_task_result"),
    ],
)
def test_inspect_vscode_copilot_session_rejects_non_receipt_sessions(
    tmp_path: Path,
    payload: bytes,
    message: str,
) -> None:
    session = tmp_path / "session.jsonl"
    session.write_bytes(payload)

    with pytest.raises(ValueError, match=message):
        inspect_vscode_copilot_session(session, request_id="request-001")
