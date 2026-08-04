"use strict";

const path = require("node:path");

const TASK_STATE_LABELS = Object.freeze({
  idle: "タスクなし",
  pending: "確認待ち",
  accepted: "確認済み",
  in_progress: "実行中",
  completed: "完了",
  failed: "失敗",
  reanalysis_required: "再分析が必要",
  cancelled: "取消済み",
  draft_ready: "Web 確認待ち",
  confirmed: "適用済み",
  superseded: "旧バージョン",
});

const CONNECTION_LABELS = Object.freeze({
  checking: "確認中",
  connected: "接続済み",
  disconnected: "接続失敗",
  token_missing: "Token 未設定",
  workspace_missing: "Workspace 未選択",
});

function dashboardState(overrides = {}) {
  return {
    connectionStatus: "checking",
    connectionDetail: "OperaMind Bridge への接続を確認しています。",
    workspaceRoot: undefined,
    task: undefined,
    taskState: "idle",
    confirmation: undefined,
    ...overrides,
  };
}

function buildDashboardTree(value) {
  const state = dashboardState(value);
  const workspaceName = state.workspaceRoot ? path.basename(state.workspaceRoot) : "未選択";
  const taskId = state.task && state.task.codingTaskId;
  const taskSummary = state.task && state.task.summary;
  const taskState = TASK_STATE_LABELS[state.taskState] || state.taskState || "不明";
  const connection = CONNECTION_LABELS[state.connectionStatus] || "不明";

  return [
    {
      id: "operamind.status",
      label: "現在の状態",
      icon: "dashboard",
      expanded: true,
      children: [
        {
          id: "operamind.status.connection",
          label: "Bridge",
          description: connection,
          tooltip: state.connectionDetail,
          icon: state.connectionStatus === "connected" ? "pass-filled" : "plug",
        },
        {
          id: "operamind.status.workspace",
          label: "Workspace",
          description: workspaceName,
          tooltip: state.workspaceRoot || "ローカル Workspace を開いてください。",
          icon: "root-folder",
        },
        {
          id: "operamind.status.confirmation",
          label: "人工確認",
          description: state.confirmation ? state.confirmation.stage_label : "なし",
          tooltip: state.confirmation
            ? state.confirmation.message
            : "現在、確認待ちの工程はありません。",
          icon: state.confirmation ? "question" : "pass-filled",
        },
        {
          id: "operamind.status.task",
          label: "Coding Task",
          description: taskId || "なし",
          tooltip: taskSummary || "現在の Workspace に割り当てられたタスクはありません。",
          icon: "checklist",
        },
        {
          id: "operamind.status.execution",
          label: "実行状態",
          description: taskState,
          tooltip: taskId ? `${taskId}: ${taskState}` : taskState,
          icon: state.taskState === "completed" ? "pass-filled" : "pulse",
        },
      ],
    },
    {
      id: "operamind.actions",
      label: "操作",
      icon: "tools",
      expanded: true,
      children: [
        action(
          "webConfirmation",
          "OperaMind Web で工程を確認",
          "operamind.openWeb",
          "globe",
          "工程の確認、差戻し、自然言語入力は OperaMind Web で行います。",
        ),
        action(
          "confirm",
          "確認して Copilot を開く",
          "operamind.openCurrentTask",
          "play",
          "確認待ちの変更タスクを受け入れ、GitHub Copilot Chat を開きます。",
        ),
        action(
          "resume",
          "現在のタスクを再開",
          "operamind.resumeCurrentTask",
          "debug-continue",
          "同じ Task ID で GitHub Copilot Chat を再開します。",
        ),
        action(
          "refresh",
          "最新状態に更新",
          "operamind.refreshDashboard",
          "refresh",
          "Bridge と現在のタスクの状態を再取得します。",
        ),
        action(
          "diagnose",
          "ローカル環境を診断",
          "operamind.diagnoseLocalEnvironment",
          "pulse",
          "Bridge、Workspace、MCP、GitHub Copilot の状態を確認します。",
        ),
        action(
          "token",
          "Bridge Token を復旧設定",
          "operamind.configureBridgeToken",
          "key",
          "自動同期に失敗した場合だけ Bridge Token を SecretStorage に保存します。",
        ),
        action(
          "web",
          "OperaMind Web を開く",
          "operamind.openWeb",
          "globe",
          "設定済みの loopback OperaMind Web を既定ブラウザーで開きます。",
        ),
      ],
    },
  ];
}

function action(id, label, command, icon, tooltip) {
  return {
    id: `operamind.action.${id}`,
    label,
    icon,
    tooltip,
    command: {command, title: label},
  };
}

module.exports = {
  CONNECTION_LABELS,
  TASK_STATE_LABELS,
  buildDashboardTree,
  dashboardState,
};
