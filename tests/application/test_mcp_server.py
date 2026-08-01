import io
import json
from pathlib import Path

import pytest

from operamind.commands.mcp_server import main
from operamind.mcp.server import (
    MCP_PROTOCOL_VERSION,
    MCP_TOOL_NAME_PATTERN,
    TOOLS,
    OperaMindMcpServer,
    _public_change_output,
    _public_command_result,
    _public_edit_result,
    _public_flow_status,
)

ROOT = Path(__file__).parents[2]


class _StubDispatcher:
    def call(self, name: str, arguments: object) -> dict[str, object]:
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        if arguments.get("coding_task_id") == "raise-error":
            raise ValueError("simulated business error")
        return {"tool": name, "arguments": arguments}


def _initialize(server: OperaMindMcpServer) -> None:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0.0"},
            },
        }
    )
    assert response is not None
    assert response["result"]["protocolVersion"] == MCP_PROTOCOL_VERSION
    assert (
        server.handle({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        is None
    )


def test_mcp_requires_lifecycle_before_listing_tools() -> None:
    server = OperaMindMcpServer(_StubDispatcher())  # type: ignore[arg-type]

    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    assert response is not None
    assert response["error"]["code"] == -32002


def test_mcp_lists_bounded_annotated_copilot_tools_after_initialization() -> None:
    server = OperaMindMcpServer(_StubDispatcher())  # type: ignore[arg-type]
    _initialize(server)

    response = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

    assert response is not None
    tools = response["result"]["tools"]
    assert [tool["name"] for tool in tools] == [tool["name"] for tool in TOOLS]
    assert all(MCP_TOOL_NAME_PATTERN.fullmatch(tool["name"]) for tool in tools)
    assert all(tool["inputSchema"]["additionalProperties"] is False for tool in tools)
    assert all("annotations" in tool for tool in tools)
    by_name = {tool["name"]: tool for tool in tools}
    assert set(by_name) == {
        "copilot_get_coding_task",
        "copilot_record_change_outputs",
        "copilot_run_task_command",
        "copilot_validate_task_diff",
        "copilot_record_task_result",
    }
    threshold = by_name["copilot_record_task_result"]["inputSchema"]["properties"][
        "changed_line_coverage"
    ]["properties"]["minimum_coverage_percent"]
    assert threshold == {"type": "number", "minimum": 80, "maximum": 100}
    outputs = by_name["copilot_record_change_outputs"]["inputSchema"]
    assert outputs["properties"]["output_stage"]["enum"] == [
        "document_change",
        "code_scope",
        "test_planning",
    ]
    assert len(outputs["oneOf"]) == 3
    assert "ui_test_result_refs" not in by_name["copilot_record_task_result"][
        "inputSchema"
    ]["properties"]
    assert {
        name for name, tool in by_name.items() if tool["annotations"]["readOnlyHint"] is True
    } == set()
    public_contract = json.dumps(tools).lower()
    assert all(
        internal_term not in public_contract
        for internal_term in (
            "approval",
            "grant",
            "packet",
            "lease",
            "worker",
            "orchestration",
        )
    )


def test_mcp_flow_status_drops_internal_automation_and_scheduler_fields() -> None:
    status = _public_flow_status(
        {
            "status": "in_progress",
            "current_stage": "code_scope",
            "progress_percent": 33,
            "blocking_reasons": [],
            "automation_run_id": "run-internal",
            "approval_grant_id": "grant-internal",
            "orchestration_tasks": [{"lease_token": "secret"}],
            "current_task": {"worker_id": "worker-internal"},
        }
    )

    assert status == {
        "status": "in_progress",
        "current_stage": "code_scope",
        "progress_percent": 33,
        "blocking_reasons": [],
    }


def test_mcp_tool_outputs_hide_internal_artifact_profile_and_scope_fields() -> None:
    change_output = _public_change_output(
        {
            "recorded_stage": "document_change",
            "next_stage": "code_scope",
            "coding_task_state": "in_progress",
            "document_ids": ["document-1"],
            "document_change_refs": ["change-internal"],
            "source_document_snapshot_id": "snapshot-internal",
            "search_index_build_id": "index-internal",
        },
        output_stage="document_change",
    )
    command = _public_command_result(
        {
            "command_execution_id": "command-1",
            "created": True,
            "command_ref": "springboot15-test",
            "status": "passed",
            "exit_code": 0,
            "stdout_digest": "stdout",
            "stderr_digest": "stderr",
            "stdout_bytes": 10,
            "stderr_bytes": 0,
            "output_truncated": False,
            "started_at": "2026-07-28T00:00:00Z",
            "completed_at": "2026-07-28T00:00:01Z",
            "coding_task_state": "in_progress",
            "command_profile_version_id": "profile-internal",
            "template_digest": "template-internal",
            "working_directory": "/registered/internal",
        }
    )
    edit = _public_edit_result(
        {
            "edit_result_id": "edit-1",
            "created": True,
            "status": "in_scope",
            "case_status": "editing",
            "command_evidence_status": "complete",
            "changed_paths": ["src/ExpenseService.java"],
            "out_of_scope_files": [],
            "result_repository_revision": "abc123",
            "coding_task_state": "completed",
            "approval_grant_id": "grant-internal",
            "changed_line_coverage": {
                "artifact_type": "ChangedLineCoverageReport",
                "changed_line_coverage_report_id": "coverage-internal",
                "project_id": "project-internal",
                "minimum_coverage_percent": 80,
                "changed_line_count": 1,
                "covered_changed_line_count": 1,
                "coverage_percent": 100,
                "files": [],
                "evidence_refs": ["command-1"],
                "status": "passed",
                "blocking_reasons": [],
            },
        }
    )

    assert change_output == {
        "recorded_stage": "document_change",
        "next_stage": "code_scope",
        "coding_task_state": "in_progress",
        "document_count": 1,
        "document_change_count": 1,
    }
    assert set(command) == {
        "command_execution_id",
        "created",
        "command_ref",
        "status",
        "exit_code",
        "stdout_digest",
        "stderr_digest",
        "stdout_bytes",
        "stderr_bytes",
        "output_truncated",
        "started_at",
        "completed_at",
        "coding_task_state",
    }
    assert set(edit) == {
        "edit_result_id",
        "created",
        "status",
        "command_evidence_status",
        "changed_paths",
        "out_of_scope_files",
        "result_repository_revision",
        "coding_task_state",
        "changed_line_coverage",
    }
    assert "internal" not in repr((change_output, command, edit))


def test_mcp_tool_business_error_is_a_tool_result_not_protocol_failure() -> None:
    server = OperaMindMcpServer(_StubDispatcher())  # type: ignore[arg-type]
    _initialize(server)

    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "copilot_get_coding_task",
                "arguments": {
                    "coding_task_id": "raise-error",
                    "workspace_root": "/workspace",
                },
            },
        }
    )

    assert response is not None
    assert response["result"]["isError"] is True
    assert "simulated business error" in response["result"]["structuredContent"]["error"]


def test_mcp_unknown_tool_is_invalid_params() -> None:
    server = OperaMindMcpServer(_StubDispatcher())  # type: ignore[arg-type]
    _initialize(server)

    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "copilot.run_shell", "arguments": {}},
        }
    )

    assert response is not None
    assert response["error"]["code"] == -32602


def test_mcp_rate_limits_tool_calls_per_session() -> None:
    server = OperaMindMcpServer(_StubDispatcher(), max_tool_calls=1)  # type: ignore[arg-type]
    _initialize(server)
    request = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "copilot_get_coding_task",
            "arguments": {
                "coding_task_id": "task-001",
                "workspace_root": "/workspace",
            },
        },
    }

    first = server.handle(request)
    request["id"] = 3
    second = server.handle(request)

    assert first is not None and first["result"]["isError"] is False
    assert second is not None and second["result"]["isError"] is True
    assert "limit exceeded" in second["result"]["structuredContent"]["error"]


def test_mcp_stdio_emits_only_one_line_json_rpc_messages() -> None:
    server = OperaMindMcpServer(_StubDispatcher())  # type: ignore[arg-type]
    input_stream = io.StringIO(
        "not-json\n"
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            }
        )
        + "\n"
    )
    output_stream = io.StringIO()

    server.serve(input_stream, output_stream)

    lines = output_stream.getvalue().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["error"]["code"] == -32700
    assert json.loads(lines[1])["result"]["protocolVersion"] == MCP_PROTOCOL_VERSION


def test_mcp_cli_requires_database_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("OPERAMIND_DATABASE_URL", raising=False)

    assert main(["--root", str(Path.cwd())]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: OPERAMIND_DATABASE_URL is required\n"


def test_vscode_extension_provides_mcp_without_workspace_configuration() -> None:
    manifest: object = json.loads(
        (ROOT / "vscode-extension/package.json").read_text(encoding="utf-8")
    )

    assert not (ROOT / ".vscode/mcp.json").exists()
    assert isinstance(manifest, dict)
    assert manifest["contributes"]["mcpServerDefinitionProviders"] == [
        {"id": "operamind.local", "label": "OperaMind Local"}
    ]
