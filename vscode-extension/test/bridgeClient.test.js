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

test("Change Task prompt contains only the current document stage", () => {
  const prompt = buildCopilotPrompt(
    {
      current_stage: "document_change",
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
  assert.match(prompt, /現在工程: 設計書変更/);
  assert.match(prompt, /Canonical RAG 文書候補/);
  assert.match(prompt, /document_change/);
  assert.match(prompt, /copilot_get_coding_task/);
  assert.match(prompt, /stage_status\.next_action/);
  assert.doesNotMatch(prompt, /TestPlan|TestDataPlan|compile_test|copilot_record_task_result/);
  assert.doesNotMatch(prompt, /next_context|Edit Packet|Approval|Grant|automation/);
  assert.ok(prompt.length < 900);
});

test("Change Task prompt switches to only the current compile stage", () => {
  const prompt = buildCopilotPrompt(
    {
      current_stage: "compile_test",
      task: {
        coding_task_id: "task-compile",
        execution_mode: "copilot_change_task",
        task_summary: "差戻し検索を追加する",
      },
    },
    "/workspace",
  );

  assert.match(prompt, /現在工程: コード変更・コンパイル・テスト/);
  assert.match(prompt, /copilot_record_task_result/);
  assert.match(prompt, /reload_current_task/);
  assert.doesNotMatch(prompt, /Canonical RAG 文書候補|test_planning/);
  assert.ok(prompt.length < 1000);
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
  await client.acceptTask("task-1", "/workspace", "vscode-1", "developer", "claim-1");
  await client.resumeTask("task-1", "/workspace", "vscode-1", "claim-1");
  await client.cancelTask(
    "task-1",
    "/workspace",
    "vscode-1",
    "developer",
    "範囲を再確認する",
    "claim-1",
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
    claim_token: "claim-1",
    accepted_by: "developer",
  });
  assert.match(requests[4][3], /tasks\/task-1\/resume/);
  assert.match(requests[4][3], /claim_token=claim-1/);
  assert.deepEqual(requests[5][4], {
    workspace_root: "/workspace",
    consumer_id: "vscode-1",
    claim_token: "claim-1",
    cancelled_by: "developer",
    reason: "範囲を再確認する",
  });
  assert.deepEqual(requests[6].slice(2), [
    "POST",
    "api/v1/local-bridge/diagnostics",
    {consumer_id: "vscode-1"},
  ]);
});

test("UI TestPlan revision prompt stays compact and stage-specific", () => {
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
  assert.match(prompt, /現在工程: UI テスト計画の再作成/);
  assert.match(prompt, /ui_test_revision/);
  assert.match(prompt, /現行計画、確認済み修正、実行制約/);
  assert.doesNotMatch(prompt, /document_change|code_scope|compile_test|next_context/);
  assert.ok(prompt.length < 900);
});

test("Project document learning prompt is bounded and requires Web confirmation", () => {
  const prompt = buildCopilotPrompt(
    {
      task: {
        coding_task_id: "document-learning-001",
        execution_mode: "copilot_change_task",
        task_kind: "document_profile_learning",
        initial_stage: "document_profile_learning",
        task_summary: "Project 設計書構造を学習する",
      },
      current_stage: "document_profile_learning",
      claim_token: "learning-claim-token",
    },
    "/workspace/demo",
    "vscode-consumer-1",
  );

  assert.match(prompt, /Project 設計書学習/);
  assert.match(prompt, /document_profile_learning/);
  assert.match(prompt, /Coverage 100%/);
  assert.match(prompt, /OperaMind Web の確認を待つ/);
  assert.match(prompt, /consumer_id=vscode-consumer-1/);
  assert.match(prompt, /claim_token=learning-claim-token/);
  assert.doesNotMatch(prompt, /コード変更・コンパイル/);
});

test("Project document learning prompt fails closed without a Claim Token", () => {
  assert.throws(
    () =>
      buildCopilotPrompt(
        {
          task: {
            coding_task_id: "document-learning-001",
            execution_mode: "copilot_change_task",
            task_kind: "document_profile_learning",
            task_summary: "Project 設計書構造を学習する",
          },
          current_stage: "document_profile_learning",
        },
        "/workspace/demo",
        "vscode-consumer-1",
      ),
    /claim identity is missing/,
  );
});
