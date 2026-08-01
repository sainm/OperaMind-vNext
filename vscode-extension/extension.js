"use strict";

const crypto = require("node:crypto");
const vscode = require("vscode");
const {BridgeClient, assertLoopbackUrl, buildCopilotPrompt} = require("./bridgeClient");
const {buildDashboardTree, dashboardState} = require("./dashboardModel");
const {loadRuntimeManifest, readBridgeToken} = require("./runtimeManifest");
const {
  formatDiagnosticReport,
  formatLocalDiagnostic,
  inspectLinkedWorktree,
  normalizeMcpToolNames,
  workspaceFingerprint,
} = require("./diagnostics");

const SECRET_KEY = "operamind.bridge.token";
const ACTIVE_TASK_KEY = "operamind.bridge.activeTask";
const TERMINAL_STATES = new Set(["completed", "failed", "reanalysis_required", "cancelled"]);

class DashboardTreeProvider {
  constructor(initialState) {
    this.state = dashboardState(initialState);
    this.changeEmitter = new vscode.EventEmitter();
    this.onDidChangeTreeData = this.changeEmitter.event;
  }

  update(values) {
    this.state = {...this.state, ...values};
    this.changeEmitter.fire(undefined);
  }

  getTreeItem(element) {
    const collapsibleState = element.children
      ? element.expanded
        ? vscode.TreeItemCollapsibleState.Expanded
        : vscode.TreeItemCollapsibleState.Collapsed
      : vscode.TreeItemCollapsibleState.None;
    const item = new vscode.TreeItem(element.label, collapsibleState);
    item.id = element.id;
    item.description = element.description;
    item.tooltip = element.tooltip;
    item.iconPath = element.icon ? new vscode.ThemeIcon(element.icon) : undefined;
    item.command = element.command;
    return item;
  }

  getChildren(element) {
    return element ? element.children || [] : buildDashboardTree(this.state);
  }

  dispose() {
    this.changeEmitter.dispose();
  }
}

function workspaceRoot() {
  const folder = vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders[0];
  return folder && folder.uri.scheme === "file" ? folder.uri.fsPath : undefined;
}

async function configureToken(context) {
  const token = await vscode.window.showInputBox({
    title: "OperaMind Local Bridge",
    prompt: "OPERAMIND_BRIDGE_TOKEN と同じ値を入力してください",
    password: true,
    ignoreFocusOut: true,
    validateInput: (value) => (value.trim() ? undefined : "Token は必須です"),
  });
  if (!token) return false;
  await context.secrets.store(SECRET_KEY, token.trim());
  void vscode.window.showInformationMessage("OperaMind Bridge Token を SecretStorage に保存しました。");
  return true;
}

function clientConfiguration(token, runtime) {
  const configuration = vscode.workspace.getConfiguration("operamind.bridge");
  return {
    client: new BridgeClient(runtime ? runtime.webUrl : configuration.get("url"), token),
    acceptedBy: configuration.get("acceptedBy"),
  };
}

async function activate(context) {
  let consumerId = context.globalState.get("operamind.bridge.consumerId");
  if (!consumerId) {
    consumerId = `vscode-${crypto.randomUUID()}`;
    await context.globalState.update("operamind.bridge.consumerId", consumerId);
  }
  const notified = new Set();
  const diagnosticOutput = vscode.window.createOutputChannel("OperaMind ローカル環境診断");
  const dashboard = new DashboardTreeProvider({workspaceRoot: workspaceRoot()});
  const dashboardView = vscode.window.createTreeView("operamind.controlPanel", {
    treeDataProvider: dashboard,
    showCollapseAll: false,
  });
  const mcpDefinitionsChanged = new vscode.EventEmitter();
  let runtime;
  let runtimeFingerprint;
  function refreshRuntime() {
    try {
      const discovered = loadRuntimeManifest();
      const fingerprint = JSON.stringify(discovered);
      if (fingerprint !== runtimeFingerprint) {
        runtime = discovered;
        runtimeFingerprint = fingerprint;
        mcpDefinitionsChanged.fire(undefined);
      }
    } catch (_error) {
      if (runtime !== undefined) {
        runtime = undefined;
        runtimeFingerprint = undefined;
        mcpDefinitionsChanged.fire(undefined);
      }
    }
    return runtime;
  }
  context.subscriptions.push(diagnosticOutput, dashboard, dashboardView, mcpDefinitionsChanged);
  context.subscriptions.push(
    vscode.lm.registerMcpServerDefinitionProvider("operamind.local", {
      onDidChangeMcpServerDefinitions: mcpDefinitionsChanged.event,
      provideMcpServerDefinitions: () => {
        const discovered = refreshRuntime();
        if (!discovered) return [];
        const definition = new vscode.McpStdioServerDefinition(
          "OperaMind",
          discovered.mcp.command,
          discovered.mcp.args,
          {},
          discovered.version,
        );
        definition.cwd = vscode.Uri.file(discovered.mcp.cwd);
        return [definition];
      },
    }),
  );

  async function tokenFor(interactive) {
    const discovered = refreshRuntime();
    let token = await context.secrets.get(SECRET_KEY);
    if (discovered) {
      try {
        const managedToken = readBridgeToken(discovered);
        if (managedToken !== token) {
          await context.secrets.store(SECRET_KEY, managedToken);
          token = managedToken;
        }
      } catch (_error) {
        // Keep an existing SecretStorage token as a recovery fallback.
      }
    }
    return token;
  }

  async function clearActive(taskId) {
    const active = context.workspaceState.get(ACTIVE_TASK_KEY);
    if (!taskId || (active && active.codingTaskId === taskId)) {
      await context.workspaceState.update(ACTIVE_TASK_KEY, undefined);
    }
    if (taskId) notified.delete(taskId);
  }

  async function presentTask(view, root, bridge, acceptedBy, {interactive = false} = {}) {
    if (!view || !view.task) return;
    const taskId = view.task.coding_task_id;
    dashboard.update({
      task: {codingTaskId: taskId, summary: view.task.task_summary},
      taskState: view.state || "pending",
    });
    await context.workspaceState.update(ACTIVE_TASK_KEY, {
      codingTaskId: taskId,
      workspaceRoot: root,
    });
    if (interactive) notified.delete(taskId);
    if (notified.has(taskId)) return;
    notified.add(taskId);
    const continuing = view.state === "accepted" || view.state === "in_progress";
    const openAction = continuing ? "Copilot を再開" : "確認して Copilot を開く";
    const action = await vscode.window.showInformationMessage(
      `OperaMind: ${view.task.task_summary}`,
      {
        modal: true,
        detail: continuing
          ? "前回の変更作業を同じ Task ID から再開します。"
          : "この変更を VS Code GitHub Copilot で開きます。",
      },
      openAction,
      "タスクを取消",
      "後で",
    );
    if (action === "タスクを取消") {
      const cancelled = await bridge.cancelTask(
        taskId,
        root,
        consumerId,
        acceptedBy,
        "VS Code でユーザーがタスクを取り消しました",
      );
      await clearActive(taskId);
      dashboard.update({
        task: {codingTaskId: taskId, summary: view.task.task_summary},
        taskState: "cancelled",
      });
      void vscode.window.showInformationMessage(
        `OperaMind タスクを取り消しました: ${cancelled.task.coding_task_id}`,
      );
      return;
    }
    if (action !== openAction) return;
    const accepted = continuing
      ? view
      : await bridge.acceptTask(taskId, root, consumerId, acceptedBy);
    dashboard.update({
      task: {codingTaskId: taskId, summary: view.task.task_summary},
      taskState: accepted.state || (continuing ? view.state : "accepted"),
    });
    await context.workspaceState.update(ACTIVE_TASK_KEY, {
      codingTaskId: taskId,
      workspaceRoot: root,
    });
    const prompt = buildCopilotPrompt(accepted, root);
    await vscode.commands.executeCommand("workbench.action.chat.open", {query: prompt});
  }

  async function checkForTasks({interactive = false} = {}) {
    const root = workspaceRoot();
    if (!root) {
      dashboard.update({
        connectionStatus: "workspace_missing",
        connectionDetail: "タスクを確認するにはローカル Workspace を開いてください。",
        workspaceRoot: undefined,
        task: undefined,
        taskState: "idle",
      });
      if (interactive) void vscode.window.showWarningMessage("ローカル Workspace を開いてください。");
      return;
    }
    dashboard.update({workspaceRoot: root});
    if (!vscode.workspace.isTrusted) {
      dashboard.update({
        connectionStatus: "checking",
        connectionDetail: "Workspace Trust の確認後に Bridge へ接続します。",
        task: undefined,
        taskState: "idle",
      });
      if (interactive) {
        void vscode.window.showWarningMessage(
          "Workspace が未信頼です。ローカル環境診断以外の OperaMind 操作は実行しません。",
        );
      }
      return;
    }
    const token = await tokenFor(interactive);
    if (!token) {
      dashboard.update({
        connectionStatus: "token_missing",
        connectionDetail: "OperaMind Launcher を起動すると Token が自動設定されます。",
        task: undefined,
        taskState: "idle",
      });
      return;
    }
    dashboard.update({
      connectionStatus: "checking",
      connectionDetail: "OperaMind Bridge から最新状態を取得しています。",
    });
    try {
      const {client, acceptedBy} = clientConfiguration(token, refreshRuntime());
      const active = context.workspaceState.get(ACTIVE_TASK_KEY);
      if (active && active.workspaceRoot === root) {
        const resumed = await client.resumeTask(active.codingTaskId, root, consumerId);
        dashboard.update({
          connectionStatus: "connected",
          connectionDetail: "loopback OperaMind Bridge に接続しています。",
        });
        if (TERMINAL_STATES.has(resumed.state)) {
          dashboard.update({
            task: {
              codingTaskId: active.codingTaskId,
              summary: resumed.task && resumed.task.task_summary,
            },
            taskState: resumed.state,
          });
          await clearActive(active.codingTaskId);
          if (interactive || resumed.state === "completed") {
            void vscode.window.showInformationMessage(
              `OperaMind タスクは ${resumed.state} です: ${active.codingTaskId}`,
            );
          }
          return;
        }
        await presentTask(resumed, root, client, acceptedBy, {interactive});
        return;
      }
      if (active && active.workspaceRoot !== root) await clearActive(active.codingTaskId);
      const response = await client.nextTask(root, consumerId);
      dashboard.update({
        connectionStatus: "connected",
        connectionDetail: "loopback OperaMind Bridge に接続しています。",
      });
      if (response.task) {
        await presentTask(response.task, root, client, acceptedBy, {interactive});
      } else {
        dashboard.update({task: undefined, taskState: "idle"});
      }
    } catch (error) {
      dashboard.update({
        connectionStatus: "disconnected",
        connectionDetail: `OperaMind Bridge に接続できません: ${error.message}`,
      });
      if (interactive) void vscode.window.showErrorMessage(`OperaMind Bridge: ${error.message}`);
    }
  }

  async function cancelCurrentTask() {
    const root = workspaceRoot();
    if (!vscode.workspace.isTrusted) {
      void vscode.window.showWarningMessage(
        "Workspace が未信頼です。OperaMind タスク操作は実行しません。",
      );
      return;
    }
    const active = context.workspaceState.get(ACTIVE_TASK_KEY);
    if (!root || !active || active.workspaceRoot !== root) {
      void vscode.window.showWarningMessage("この Workspace に再開可能な OperaMind タスクはありません。");
      return;
    }
    const reason = await vscode.window.showInputBox({
      title: "OperaMind タスクを取消",
      prompt: "監査履歴に残す取消理由を入力してください",
      ignoreFocusOut: true,
      validateInput: (value) => (value.trim() ? undefined : "取消理由は必須です"),
    });
    if (!reason) return;
    const token = await tokenFor(true);
    if (!token) return;
    try {
      const {client, acceptedBy} = clientConfiguration(token, refreshRuntime());
      await client.resumeTask(active.codingTaskId, root, consumerId);
      await client.cancelTask(
        active.codingTaskId,
        root,
        consumerId,
        acceptedBy,
        reason.trim(),
      );
      await clearActive(active.codingTaskId);
      dashboard.update({
        task: {codingTaskId: active.codingTaskId},
        taskState: "cancelled",
      });
      void vscode.window.showInformationMessage("OperaMind タスクを取り消しました。");
    } catch (error) {
      void vscode.window.showErrorMessage(`OperaMind タスクを取り消せません: ${error.message}`);
    }
  }

  async function diagnoseLocalEnvironment() {
    const root = workspaceRoot();
    const token = await tokenFor(false);
    const configuration = vscode.workspace.getConfiguration("operamind.bridge");
    const bridgeUrl = (refreshRuntime() || {}).webUrl || configuration.get("url");
    let bridgeUrlLoopback = false;
    try {
      assertLoopbackUrl(bridgeUrl);
      bridgeUrlLoopback = true;
    } catch (_error) {
      bridgeUrlLoopback = false;
    }

    const toolNames = normalizeMcpToolNames(
      Array.isArray(vscode.lm && vscode.lm.tools)
        ? vscode.lm.tools.map((tool) => tool.name)
        : [],
    );
    const copilot = vscode.extensions.getExtension("GitHub.copilot-chat");
    let models = [];
    let modelApiAvailable = false;
    if (vscode.lm && typeof vscode.lm.selectChatModels === "function") {
      modelApiAvailable = true;
      try {
        models = await vscode.lm.selectChatModels({vendor: "copilot"});
      } catch (_error) {
        models = [];
      }
    }
    const report = {
      consumer_id: consumerId,
      observed_at: new Date().toISOString(),
      workspace_fingerprint: workspaceFingerprint(root),
      vsix_version: context.extension.packageJSON.version,
      bridge_url_loopback: bridgeUrlLoopback,
      bridge_token_configured: Boolean(token),
      workspace_trusted: Boolean(vscode.workspace.isTrusted),
      linked_worktree: await inspectLinkedWorktree(root),
      mcp_tool_names: toolNames,
      copilot_extension_installed: Boolean(copilot),
      copilot_extension_active: Boolean(copilot && copilot.isActive),
      copilot_extension_version: copilot ? copilot.packageJSON.version || null : null,
      copilot_model_api_available: modelApiAvailable,
      copilot_model_count: models.length,
    };

    let rendered;
    let overall = "ローカル結果のみ";
    if (token && bridgeUrlLoopback) {
      try {
        const result = await new BridgeClient(bridgeUrl, token).reportDiagnostics(report);
        rendered = formatDiagnosticReport(result);
        overall = result.overall_status;
        dashboard.update({
          connectionStatus: "connected",
          connectionDetail: "診断 API を含む loopback Bridge 接続に成功しました。",
        });
      } catch (error) {
        rendered = formatLocalDiagnostic(report, `接続できません (${error.message})`);
        dashboard.update({
          connectionStatus: "disconnected",
          connectionDetail: `OperaMind Bridge に接続できません: ${error.message}`,
        });
      }
    } else {
      rendered = formatLocalDiagnostic(
        report,
        token ? "Bridge URL は loopback に限定してください" : "Bridge Token が未設定です",
      );
    }
    diagnosticOutput.clear();
    diagnosticOutput.appendLine(rendered);
    diagnosticOutput.show(true);
    void vscode.window.showInformationMessage(`OperaMind ローカル環境診断: ${overall}`);
  }

  async function openWeb() {
    const configured = (refreshRuntime() || {}).webUrl ||
      vscode.workspace.getConfiguration("operamind.bridge").get("url");
    try {
      const url = assertLoopbackUrl(configured);
      await vscode.env.openExternal(vscode.Uri.parse(url.toString()));
    } catch (error) {
      void vscode.window.showErrorMessage(`OperaMind Web を開けません: ${error.message}`);
    }
  }

  context.subscriptions.push(
    vscode.commands.registerCommand("operamind.checkForTasks", () =>
      checkForTasks({interactive: true}),
    ),
    vscode.commands.registerCommand("operamind.openCurrentTask", () =>
      checkForTasks({interactive: true}),
    ),
    vscode.commands.registerCommand("operamind.resumeCurrentTask", () =>
      checkForTasks({interactive: true}),
    ),
    vscode.commands.registerCommand("operamind.cancelCurrentTask", cancelCurrentTask),
    vscode.commands.registerCommand("operamind.configureBridgeToken", async () => {
      if (await configureToken(context)) {
        dashboard.update({
          connectionStatus: "checking",
          connectionDetail: "Bridge Token を保存しました。接続を確認しています。",
        });
        await checkForTasks();
      }
    }),
    vscode.commands.registerCommand("operamind.refreshDashboard", () => checkForTasks()),
    vscode.commands.registerCommand("operamind.openWeb", openWeb),
    vscode.commands.registerCommand(
      "operamind.diagnoseLocalEnvironment",
      diagnoseLocalEnvironment,
    ),
  );
  const seconds = Math.max(
    2,
    Number(vscode.workspace.getConfiguration("operamind.bridge").get("pollSeconds")) || 5,
  );
  const timer = setInterval(() => void checkForTasks(), seconds * 1000);
  context.subscriptions.push({dispose: () => clearInterval(timer)});
  context.subscriptions.push(
    vscode.workspace.onDidChangeWorkspaceFolders(() => {
      dashboard.update({
        workspaceRoot: workspaceRoot(),
        task: undefined,
        taskState: "idle",
      });
      void checkForTasks();
    }),
  );
  void checkForTasks();
}

function deactivate() {}

module.exports = {activate, deactivate};
