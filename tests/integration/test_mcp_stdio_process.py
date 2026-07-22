import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from operamind.mcp import MCP_PROTOCOL_VERSION

ROOT = Path(__file__).parents[2]
DATABASE_URL = os.getenv("OPERAMIND_TEST_DATABASE_URL")

pytestmark = pytest.mark.integration


@pytest.mark.skipif(DATABASE_URL is None, reason="OPERAMIND_TEST_DATABASE_URL is not set")
def test_installed_mcp_stdio_entrypoint_negotiates_and_lists_tools() -> None:
    assert DATABASE_URL is not None
    executable = Path(sys.executable).with_name("operamind-mcp")
    assert executable.is_file()
    requests = (
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "stdio-process-test", "version": "1.0.0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    )
    environment = {
        **os.environ,
        "OPERAMIND_DATABASE_URL": DATABASE_URL,
    }

    completed = subprocess.run(
        [str(executable), "--root", str(ROOT)],
        input="".join(json.dumps(request) + "\n" for request in requests),
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    responses = [json.loads(line) for line in completed.stdout.splitlines()]
    assert len(responses) == 2
    assert responses[0]["result"]["protocolVersion"] == MCP_PROTOCOL_VERSION
    assert {tool["name"] for tool in responses[1]["result"]["tools"]} == {
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
