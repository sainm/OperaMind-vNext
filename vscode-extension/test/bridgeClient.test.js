"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  BridgeClient,
  assertLoopbackUrl,
  buildCopilotPrompt,
  isMissingBridgeResource,
} = require("../bridgeClient");

test("Bridge client rejects non-loopback URLs", () => {
  assert.throws(() => assertLoopbackUrl("https://example.com"), /loopback-only/);
  assert.equal(assertLoopbackUrl("http://127.0.0.1:8765").hostname, "127.0.0.1");
});

test("Bridge client identifies a stale task response without treating server errors as stale", () => {
  assert.equal(isMissingBridgeResource({statusCode: 404}), true);
  assert.equal(isMissingBridgeResource({statusCode: 500}), false);
  assert.equal(isMissingBridgeResource(new Error("network failed")), false);
});

test("Change Task prompt carries identity, test planning, and MCP result tools", () => {
  const prompt = buildCopilotPrompt(
    {
      task: {
        coding_task_id: "task-1",
        execution_mode: "copilot_change_task",
        task_summary: "差戻しを追加する",
        target_project: {
          detection_status: "supported",
          framework: "Spring Boot 1.5",
          template_engine: "Thymeleaf",
          build_system: "Gradle Wrapper",
          change_constraints: [
            "Spring Boot 1.5 を維持する",
            "Framework を更新しない",
            "互換 JDK を使用する",
          ],
          compile_command: ["./gradlew", "classes", "testClasses", "--no-daemon"],
          test_command: ["./gradlew", "test", "--no-daemon"],
          build_command: ["./gradlew", "build", "--no-daemon"],
        },
        required_mcp_tools: [
          "copilot_get_coding_task",
          "copilot_record_change_outputs",
          "copilot_run_task_command",
          "copilot_validate_task_diff",
          "copilot_record_task_result",
        ],
      },
    },
    "/workspace",
  );
  assert.match(prompt, /task-1/);
  assert.match(prompt, /TestPlan/);
  assert.match(prompt, /TestDataPlan/);
  assert.match(prompt, /canonical_document facts/);
  assert.match(prompt, /document_edits/);
  assert.match(prompt, /OperaMind が限定セルだけ更新/);
  assert.match(prompt, /Spring Boot 1\.5 \/ Thymeleaf \/ Gradle Wrapper/);
  assert.match(prompt, /Framework を更新しない/);
  assert.match(prompt, /\.\/gradlew classes testClasses --no-daemon/);
  assert.match(prompt, /copilot_get_coding_task/);
  assert.match(prompt, /返却自体を現在の authoritative context/);
  assert.match(prompt, /next_context の欠落を停止理由にしない/);
  assert.match(prompt, /current_stage=compile_test かつ execution_scope\.bound=true/);
  assert.match(prompt, /状態を変更する MCP Tool が next_context を含めて返した場合だけ/);
  assert.match(prompt, /copilot_record_task_result/);
  assert.ok(
    prompt.indexOf("copilot_record_task_result") < prompt.indexOf("output_stage=test_planning"),
  );
  assert.doesNotMatch(prompt, /ai-response\.json/);
  assert.doesNotMatch(prompt, /Edit Packet|Approval|Grant|automation/);
});

test("Bridge client builds authenticated calls without a handoff file", async () => {
  const requests = [];
  const transport = async (...values) => {
    requests.push(values);
    return {task: null};
  };
  const client = new BridgeClient("http://127.0.0.1:8765", "secret-token", transport);

  await client.nextTask("/workspace", "vscode-1", "change-1");
  await client.nextConfirmation("/workspace", "change-1");
  await client.decideConfirmation(
    "change-1",
    "requirement",
    "confirmed",
    "developer",
    "decision-1",
  );
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

  assert.equal(requests.length, 7);
  assert.deepEqual(requests[0].slice(0, 3), [
    "http://127.0.0.1:8765/",
    "secret-token",
    "GET",
  ]);
  assert.match(requests[0][3], /workspace_root=%2Fworkspace/);
  assert.match(requests[0][3], /change_request_id=change-1/);
  assert.match(requests[1][3], /confirmations\/next/);
  assert.match(requests[1][3], /change_request_id=change-1/);
  assert.deepEqual(requests[2][4], {
    decision: "confirmed",
    actor: "developer",
    idempotency_key: "decision-1",
  });
  assert.deepEqual(requests[3][4], {
    workspace_root: "/workspace",
    consumer_id: "vscode-1",
    accepted_by: "developer",
  });
  assert.match(requests[4][3], /tasks\/task-1\/resume/);
  assert.deepEqual(requests[5][4], {
    workspace_root: "/workspace",
    consumer_id: "vscode-1",
    cancelled_by: "developer",
    reason: "範囲を再確認する",
  });
  assert.deepEqual(requests[6].slice(2), [
    "POST",
    "api/v1/local-bridge/diagnostics",
    {consumer_id: "vscode-1"},
  ]);
});

test("UI TestPlan revision prompt is read-only and asks for complete Playwright plans", () => {
  const prompt = buildCopilotPrompt(
    {
      task: {
        coding_task_id: "revision-1",
        execution_mode: "copilot_change_task",
        task_kind: "ui_test_plan_revision",
        initial_stage: "ui_test_revision",
        task_summary: "検索条件を差戻しに変更",
        required_mcp_tools: ["copilot_get_coding_task", "copilot_record_change_outputs"],
      },
    },
    "/workspace",
  );
  assert.match(prompt, /revision-1/);
  assert.match(prompt, /source_ui_test_plan/);
  assert.match(prompt, /output_stage=ui_test_revision/);
  assert.match(prompt, /限定 Playwright action/);
  assert.match(prompt, /コード、設計書.*変更せず/);
  assert.doesNotMatch(prompt, /document_edits/);
});
