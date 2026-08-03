"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {ALLOWED_COMMANDS, renderDashboardHtml} = require("../dashboardView");

test("dashboard webview renders a themed Japanese task and confirmation surface", () => {
  const html = renderDashboardHtml(
    {
      connectionStatus: "connected",
      connectionDetail: "loopback Bridge に接続しています。",
      workspaceRoot: "/workspace/VisionDemo",
      task: {codingTaskId: "task-42", summary: "経費状態検索を変更する"},
      taskState: "in_progress",
      confirmation: {
        stage_label: "コード影響範囲の確認",
        message: "変更対象を確認してください。",
      },
    },
    "nonce-123",
  );

  assert.match(html, /OperaMind/);
  assert.match(html, /VisionDemo/);
  assert.match(html, /経費状態検索を変更する/);
  assert.match(html, /コード影響範囲の確認/);
  assert.match(html, /OperaMind Web で確認/);
  assert.match(html, /Copilot で再開/);
  assert.doesNotMatch(html, /差し戻す/);
  assert.doesNotMatch(html, /タスクを取消/);
  assert.doesNotMatch(html, /textarea|contenteditable/);
  assert.match(html, /var\(--vscode-sideBar-background\)/);
  assert.match(html, /style-src 'nonce-nonce-123'/);
});

test("dashboard webview escapes task content and only exposes registered commands", () => {
  const html = renderDashboardHtml(
    {
      task: {
        codingTaskId: "task-<unsafe>",
        summary: '<script>alert("unsafe")</script>',
      },
      taskState: "pending",
    },
    "safe-nonce",
  );

  assert.doesNotMatch(html, /<script>alert/);
  assert.match(html, /&lt;script&gt;alert/);
  assert.equal(ALLOWED_COMMANDS.includes("operamind.openCurrentTask"), true);
  assert.equal(ALLOWED_COMMANDS.includes("operamind.rejectCurrentCheckpoint"), false);
  assert.equal(ALLOWED_COMMANDS.includes("operamind.cancelCurrentTask"), false);
  assert.equal(ALLOWED_COMMANDS.includes("workbench.action.terminal.new"), false);
});

test("dashboard webview shows a calm empty state without destructive task controls", () => {
  const html = renderDashboardHtml(
    {connectionStatus: "connected", taskState: "idle"},
    "nonce-empty",
  );

  assert.match(html, /待機中のタスクはありません/);
  assert.match(html, /今すぐ確認/);
  assert.doesNotMatch(html, /タスクを取消/);
});
