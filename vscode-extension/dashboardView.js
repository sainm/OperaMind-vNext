"use strict";

const path = require("node:path");

const {CONNECTION_LABELS, TASK_STATE_LABELS, dashboardState} = require("./dashboardModel");

const ALLOWED_COMMANDS = Object.freeze([
  "operamind.openCurrentTask",
  "operamind.resumeCurrentTask",
  "operamind.refreshDashboard",
  "operamind.diagnoseLocalEnvironment",
  "operamind.configureBridgeToken",
  "operamind.openWeb",
]);

function renderDashboardHtml(value, nonce) {
  const state = dashboardState(value);
  const connectionLabel = CONNECTION_LABELS[state.connectionStatus] || "不明";
  const taskLabel = TASK_STATE_LABELS[state.taskState] || state.taskState || "不明";
  const workspaceName = state.workspaceRoot ? path.basename(state.workspaceRoot) : "未選択";
  const taskId = state.task && state.task.codingTaskId;
  const taskSummary = state.task && state.task.summary;
  const confirmation = state.confirmation;
  const connectionTone = connectionStatusTone(state.connectionStatus);
  const taskTone = taskStatusTone(state.taskState);
  const hasTask = Boolean(taskId);
  const hasConfirmation = Boolean(confirmation);

  return `<!doctype html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'nonce-${escapeAttribute(nonce)}'; script-src 'nonce-${escapeAttribute(nonce)}';">
  <style nonce="${escapeAttribute(nonce)}">
    :root { color-scheme: light dark; }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      padding: 0 0 24px;
      color: var(--vscode-foreground);
      background: var(--vscode-sideBar-background);
      font-family: var(--vscode-font-family);
      font-size: var(--vscode-font-size);
    }
    button { font: inherit; }
    .hero {
      position: relative;
      overflow: hidden;
      padding: 20px 16px 18px;
      border-bottom: 1px solid var(--vscode-sideBar-border, var(--vscode-widget-border));
      background:
        radial-gradient(circle at 90% 5%, color-mix(in srgb, var(--vscode-focusBorder) 24%, transparent), transparent 45%),
        linear-gradient(145deg, color-mix(in srgb, var(--vscode-sideBar-background) 90%, var(--vscode-focusBorder)), var(--vscode-sideBar-background));
    }
    .eyebrow {
      margin: 0 0 8px;
      color: var(--vscode-descriptionForeground);
      font-size: 10px;
      font-weight: 700;
      letter-spacing: .14em;
      text-transform: uppercase;
    }
    h1 { margin: 0; font-size: 21px; line-height: 1.2; letter-spacing: -.02em; }
    .hero-copy { margin: 7px 0 0; color: var(--vscode-descriptionForeground); line-height: 1.55; }
    .hero-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    .refresh-icon {
      width: 30px; height: 30px; flex: 0 0 auto; border-radius: 9px;
      border: 1px solid var(--vscode-button-secondaryBackground);
      color: var(--vscode-foreground); background: color-mix(in srgb, var(--vscode-sideBar-background) 75%, transparent);
      cursor: pointer;
    }
    .refresh-icon:hover { background: var(--vscode-toolbar-hoverBackground); }
    main { display: grid; gap: 12px; padding: 14px 12px 0; }
    .card {
      padding: 13px;
      border: 1px solid var(--vscode-widget-border, var(--vscode-sideBar-border));
      border-radius: 11px;
      background: color-mix(in srgb, var(--vscode-editor-background) 82%, transparent);
      box-shadow: 0 1px 0 color-mix(in srgb, var(--vscode-foreground) 6%, transparent);
    }
    .section-heading {
      display: flex; align-items: center; justify-content: space-between; gap: 8px;
      margin-bottom: 11px;
    }
    h2 { margin: 0; font-size: 12px; font-weight: 700; letter-spacing: .02em; }
    .pill {
      display: inline-flex; align-items: center; gap: 6px;
      max-width: 100%; padding: 4px 8px; border-radius: 999px;
      font-size: 11px; font-weight: 700;
      background: var(--vscode-badge-background); color: var(--vscode-badge-foreground);
    }
    .pill::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
    .tone-good { color: var(--vscode-testing-iconPassed, #73c991); background: color-mix(in srgb, var(--vscode-testing-iconPassed, #73c991) 14%, transparent); }
    .tone-warn { color: var(--vscode-editorWarning-foreground, #cca700); background: color-mix(in srgb, var(--vscode-editorWarning-foreground, #cca700) 14%, transparent); }
    .tone-bad { color: var(--vscode-testing-iconFailed, #f14c4c); background: color-mix(in srgb, var(--vscode-testing-iconFailed, #f14c4c) 14%, transparent); }
    .tone-muted { color: var(--vscode-descriptionForeground); background: color-mix(in srgb, var(--vscode-descriptionForeground) 12%, transparent); }
    .status-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 9px; }
    .status-item { min-width: 0; padding: 9px; border-radius: 8px; background: var(--vscode-editorWidget-background, var(--vscode-editor-background)); }
    .label { display: block; margin-bottom: 5px; color: var(--vscode-descriptionForeground); font-size: 10px; }
    .value { display: block; overflow: hidden; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }
    .confirmation { border-color: color-mix(in srgb, var(--vscode-editorWarning-foreground, #cca700) 55%, var(--vscode-widget-border)); }
    .confirmation-title { margin: 0 0 7px; font-size: 13px; line-height: 1.4; }
    .copy { margin: 0; color: var(--vscode-descriptionForeground); line-height: 1.55; overflow-wrap: anywhere; }
    .connection-copy { margin-top: 10px; }
    .task-summary { margin: 0 0 10px; font-weight: 650; line-height: 1.55; overflow-wrap: anywhere; }
    .task-id { display: block; color: var(--vscode-descriptionForeground); font-family: var(--vscode-editor-font-family); font-size: 10px; overflow-wrap: anywhere; }
    .button-row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 12px; }
    .button-row.single { grid-template-columns: 1fr; }
    .button {
      min-height: 32px; padding: 7px 10px; border: 1px solid transparent; border-radius: 7px;
      cursor: pointer; line-height: 1.25;
    }
    .button.primary { color: var(--vscode-button-foreground); background: var(--vscode-button-background); }
    .button.primary:hover { background: var(--vscode-button-hoverBackground); }
    .button.secondary { color: var(--vscode-button-secondaryForeground); background: var(--vscode-button-secondaryBackground); }
    .button.secondary:hover { background: var(--vscode-button-secondaryHoverBackground); }
    .button.danger { color: var(--vscode-errorForeground); border-color: color-mix(in srgb, var(--vscode-errorForeground) 55%, transparent); background: transparent; }
    .button:disabled { cursor: default; opacity: .42; }
    .utility-grid { display: grid; gap: 2px; }
    .utility {
      display: flex; align-items: center; justify-content: space-between; gap: 10px;
      width: 100%; padding: 9px 7px; border: 0; border-radius: 6px;
      color: var(--vscode-foreground); background: transparent; text-align: left; cursor: pointer;
    }
    .utility:hover { background: var(--vscode-list-hoverBackground); }
    .utility span:last-child { color: var(--vscode-descriptionForeground); }
    .empty { padding: 6px 0 2px; text-align: center; }
    .empty-mark { display: grid; place-items: center; width: 34px; height: 34px; margin: 0 auto 9px; border-radius: 11px; color: var(--vscode-descriptionForeground); background: var(--vscode-editorWidget-background, var(--vscode-editor-background)); font-size: 17px; }
    @media (max-width: 230px) { .status-grid, .button-row { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <header class="hero">
    <p class="eyebrow">Change orchestration</p>
    <div class="hero-row">
      <h1>OperaMind</h1>
      <button class="refresh-icon" data-command="operamind.refreshDashboard" title="最新状態に更新" aria-label="最新状態に更新">↻</button>
    </div>
    <p class="hero-copy">確定済みの Copilot Task を、この Workspace で読み取り専用に受け取ります。</p>
  </header>
  <main>
    <section class="card" aria-label="接続状態">
      <div class="section-heading"><h2>現在の状態</h2><span class="pill ${connectionTone}">${escapeHtml(connectionLabel)}</span></div>
      <div class="status-grid">
        <div class="status-item"><span class="label">Workspace</span><span class="value" title="${escapeAttribute(state.workspaceRoot || "")}">${escapeHtml(workspaceName)}</span></div>
        <div class="status-item"><span class="label">実行状態</span><span class="value ${taskTone}">${escapeHtml(taskLabel)}</span></div>
      </div>
      <p class="copy connection-copy">${escapeHtml(state.connectionDetail)}</p>
    </section>
    ${hasConfirmation ? confirmationCard(confirmation) : ""}
    <section class="card" aria-label="現在のタスク">
      <div class="section-heading"><h2>現在の Coding Task</h2>${hasTask ? `<span class="pill ${taskTone}">${escapeHtml(taskLabel)}</span>` : ""}</div>
      ${hasTask ? taskCard(taskId, taskSummary, state.taskState) : emptyTaskCard()}
    </section>
    <section class="card" aria-label="ツール">
      <div class="section-heading"><h2>ツール</h2></div>
      <div class="utility-grid">
        ${utilityButton("ローカル環境を診断", "Bridge・MCP・Copilot", "operamind.diagnoseLocalEnvironment")}
        ${utilityButton("OperaMind Web を開く", "↗", "operamind.openWeb")}
        ${utilityButton("Bridge Token を復旧設定", "SecretStorage", "operamind.configureBridgeToken")}
      </div>
    </section>
  </main>
  <script nonce="${escapeAttribute(nonce)}">
    const vscode = acquireVsCodeApi();
    document.addEventListener("click", (event) => {
      const target = event.target.closest("[data-command]");
      if (!target || target.disabled) return;
      vscode.postMessage({command: target.dataset.command});
    });
  </script>
</body>
</html>`;
}

function confirmationCard(confirmation) {
  return `<section class="card confirmation" aria-label="確認待ち">
    <div class="section-heading"><h2>確認が必要です</h2><span class="pill tone-warn">Human checkpoint</span></div>
    <h3 class="confirmation-title">${escapeHtml(confirmation.stage_label || "工程の確認")}</h3>
    <p class="copy">${escapeHtml(confirmation.message || "内容を確認してください。")}</p>
    <div class="button-row single">
      ${commandButton("OperaMind Web で確認", "operamind.openWeb", "primary")}
    </div>
  </section>`;
}

function taskCard(taskId, summary, state) {
  const continuing = state === "accepted" || state === "in_progress";
  return `<p class="task-summary">${escapeHtml(summary || "変更タスクの詳細を取得しています。")}</p>
    <code class="task-id">${escapeHtml(taskId)}</code>
    <div class="button-row single">
      ${commandButton(
        continuing ? "Copilot で再開" : "確認して Copilot を開く",
        continuing ? "operamind.resumeCurrentTask" : "operamind.openCurrentTask",
        "primary",
      )}
    </div>
    <div class="button-row single">
      ${commandButton("最新状態", "operamind.refreshDashboard", "secondary")}
    </div>`;
}

function emptyTaskCard() {
  return `<div class="empty"><div class="empty-mark">✓</div><p class="task-summary">待機中のタスクはありません</p><p class="copy">新しい Task または人工確認を自動で監視しています。</p></div>
    <div class="button-row single">${commandButton("今すぐ確認", "operamind.refreshDashboard", "secondary")}</div>`;
}

function commandButton(label, command, tone) {
  return `<button class="button ${tone}" data-command="${escapeAttribute(command)}">${escapeHtml(label)}</button>`;
}

function utilityButton(label, detail, command) {
  return `<button class="utility" data-command="${escapeAttribute(command)}"><span>${escapeHtml(label)}</span><span>${escapeHtml(detail)}</span></button>`;
}

function connectionStatusTone(status) {
  if (status === "connected") return "tone-good";
  if (status === "disconnected") return "tone-bad";
  if (status === "checking") return "tone-warn";
  return "tone-muted";
}

function taskStatusTone(status) {
  if (status === "completed") return "tone-good";
  if (status === "failed" || status === "reanalysis_required") return "tone-bad";
  if (status === "pending" || status === "in_progress") return "tone-warn";
  return "tone-muted";
}

function escapeHtml(value) {
  return String(value == null ? "" : value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function escapeAttribute(value) {
  return escapeHtml(value).replaceAll("`", "&#96;");
}

module.exports = {
  ALLOWED_COMMANDS,
  connectionStatusTone,
  escapeHtml,
  renderDashboardHtml,
  taskStatusTone,
};
