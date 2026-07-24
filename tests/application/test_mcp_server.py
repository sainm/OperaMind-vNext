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
)

ROOT = Path(__file__).parents[2]


class _StubDispatcher:
    def call(self, name: str, arguments: object) -> dict[str, object]:
        if not isinstance(arguments, dict) or "project_id" not in arguments:
            raise ValueError("project_id is required")
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
        "analysis_list_ready_cases",
        "impact_get_report",
        "copilot_get_edit_packet",
        "copilot_get_approval_grant",
        "copilot_run_approved_command",
        "copilot_validate_worktree",
        "copilot_record_edit_result",
        "copilot_get_coding_task",
        "copilot_run_task_command",
        "copilot_validate_task_diff",
        "copilot_record_task_result",
        "verification_get_ui_plan",
        "validation_get_result",
    }
    assert by_name["analysis_list_ready_cases"]["inputSchema"]["properties"]["limit"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 50,
    }
    threshold = by_name["copilot_record_edit_result"]["inputSchema"]["properties"][
        "changed_line_coverage"
    ]["properties"]["minimum_coverage_percent"]
    assert threshold == {"type": "number", "minimum": 80, "maximum": 100}
    assert {
        name for name, tool in by_name.items() if tool["annotations"]["readOnlyHint"] is True
    } == {
        "analysis_list_ready_cases",
        "impact_get_report",
        "copilot_get_edit_packet",
        "copilot_get_approval_grant",
        "verification_get_ui_plan",
        "validation_get_result",
    }


def test_mcp_tool_business_error_is_a_tool_result_not_protocol_failure() -> None:
    server = OperaMindMcpServer(_StubDispatcher())  # type: ignore[arg-type]
    _initialize(server)

    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "copilot_get_approval_grant", "arguments": {}},
        }
    )

    assert response is not None
    assert response["result"]["isError"] is True
    assert "project_id is required" in response["result"]["structuredContent"]["error"]


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
            "name": "copilot_get_approval_grant",
            "arguments": {"project_id": "project-001"},
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


def test_vscode_mcp_configuration_prompts_for_database_url() -> None:
    config: object = json.loads((ROOT / ".vscode/mcp.json").read_text(encoding="utf-8"))

    assert isinstance(config, dict)
    server = config["servers"]["operaMind"]
    assert server["type"] == "stdio"
    assert server["command"] == "${workspaceFolder}/.venv/bin/operamind-mcp"
    assert server["env"]["OPERAMIND_DATABASE_URL"] == "${input:operamind-database-url}"
    assert config["inputs"][0]["password"] is True
