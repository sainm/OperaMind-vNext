"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {BridgeClient, assertLoopbackUrl, buildCopilotPrompt} = require("../bridgeClient");

test("Bridge client rejects non-loopback URLs", () => {
  assert.throws(() => assertLoopbackUrl("https://example.com"), /loopback-only/);
  assert.equal(assertLoopbackUrl("http://127.0.0.1:8765").hostname, "127.0.0.1");
});

test("Coding Plan prompt carries task identity and MCP result tools", () => {
  const prompt = buildCopilotPrompt(
    {
      task: {
        coding_task_id: "task-1",
        execution_mode: "copilot_coding_plan",
        task_summary: "差戻しを追加する",
        required_mcp_tools: [
          "copilot_get_coding_task",
          "copilot_run_task_command",
          "copilot_validate_task_diff",
          "copilot_record_task_result",
        ],
      },
    },
    "/workspace",
  );
  assert.match(prompt, /task-1/);
  assert.match(prompt, /copilot_get_coding_task/);
  assert.match(prompt, /copilot_record_task_result/);
  assert.doesNotMatch(prompt, /ai-response\.json/);
});

test("Bridge client builds authenticated calls without a handoff file", async () => {
  const requests = [];
  const transport = async (...values) => {
    requests.push(values);
    return {task: null};
  };
  const client = new BridgeClient("http://127.0.0.1:8765", "secret-token", transport);

  await client.nextTask("/workspace", "vscode-1");
  await client.acceptTask("task-1", "/workspace", "vscode-1", "developer");
  await client.resumeTask("task-1", "/workspace", "vscode-1");
  await client.cancelTask(
    "task-1",
    "/workspace",
    "vscode-1",
    "developer",
    "範囲を再確認する",
  );
  await client.reportDiagnostics({consumer_id: "vscode-1"});

  assert.equal(requests.length, 5);
  assert.deepEqual(requests[0].slice(0, 3), [
    "http://127.0.0.1:8765/",
    "secret-token",
    "GET",
  ]);
  assert.match(requests[0][3], /workspace_root=%2Fworkspace/);
  assert.deepEqual(requests[1][4], {
    workspace_root: "/workspace",
    consumer_id: "vscode-1",
    accepted_by: "developer",
  });
  assert.match(requests[2][3], /tasks\/task-1\/resume/);
  assert.deepEqual(requests[3][4], {
    workspace_root: "/workspace",
    consumer_id: "vscode-1",
    cancelled_by: "developer",
    reason: "範囲を再確認する",
  });
  assert.deepEqual(requests[4].slice(2), [
    "POST",
    "api/v1/local-bridge/diagnostics",
    {consumer_id: "vscode-1"},
  ]);
});
