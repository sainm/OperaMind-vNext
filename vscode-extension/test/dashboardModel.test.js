"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {buildDashboardTree, dashboardState} = require("../dashboardModel");
const manifest = require("../package.json");

function sections(state) {
  const tree = buildDashboardTree(state);
  return {
    status: tree.find((item) => item.id === "operamind.status"),
    actions: tree.find((item) => item.id === "operamind.actions"),
  };
}

test("dashboard displays Bridge, Workspace, Coding Task, and Japanese execution state", () => {
  const {status} = sections(
    dashboardState({
      connectionStatus: "connected",
      connectionDetail: "接続しています。",
      workspaceRoot: "/workspace/VisionDemo",
      task: {codingTaskId: "task-42", summary: "経費状態検索を変更する"},
      taskState: "in_progress",
      confirmation: {stage_label: "変更要件の確認", message: "内容を確認してください。"},
    }),
  );

  assert.equal(status.expanded, true);
  assert.deepEqual(
    status.children.map((item) => [item.label, item.description]),
    [
      ["Bridge", "接続済み"],
      ["Workspace", "VisionDemo"],
      ["人工確認", "変更要件の確認"],
      ["Coding Task", "task-42"],
      ["実行状態", "実行中"],
    ],
  );
  assert.match(status.children[3].tooltip, /経費状態検索/);
});

test("dashboard exposes shared confirmations and task operations", () => {
  const {actions} = sections(dashboardState({connectionStatus: "token_missing"}));

  assert.equal(actions.expanded, true);
  assert.deepEqual(
    actions.children.map((item) => item.command.command),
    [
      "operamind.confirmCurrentCheckpoint",
      "operamind.rejectCurrentCheckpoint",
      "operamind.openCurrentTask",
      "operamind.resumeCurrentTask",
      "operamind.cancelCurrentTask",
      "operamind.refreshDashboard",
      "operamind.diagnoseLocalEnvironment",
      "operamind.configureBridgeToken",
      "operamind.openWeb",
    ],
  );
  assert.deepEqual(
    actions.children.map((item) => item.label),
    [
      "現在の工程を確認",
      "現在の工程を差戻し",
      "確認して Copilot を開く",
      "現在のタスクを再開",
      "現在のタスクを取消",
      "最新状態に更新",
      "ローカル環境を診断",
      "Bridge Token を復旧設定",
      "OperaMind Web を開く",
    ],
  );
});

test("manifest contributes the OperaMind Activity Bar control panel", () => {
  assert.deepEqual(manifest.contributes.viewsContainers.activitybar, [
    {
      id: "operamind",
      title: "OperaMind",
      icon: "media/operamind.svg",
    },
  ]);
  assert.equal(manifest.contributes.views.operamind[0].id, "operamind.controlPanel");
  assert.equal(manifest.contributes.views.operamind[0].name, "コントロール");
  assert.deepEqual(manifest.contributes.mcpServerDefinitionProviders, [
    {id: "operamind.local", label: "OperaMind Local"},
  ]);
  assert.ok(
    manifest.contributes.menus["view/title"].some(
      (item) => item.command === "operamind.refreshDashboard",
    ),
  );
});
