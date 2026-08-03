"use strict";

const crypto = require("node:crypto");
const vscode = require("vscode");
const {
  BridgeClient,
  assertLoopbackUrl,
  buildCopilotPrompt,
  isMissingBridgeResource,
} = require("./bridgeClient");
const {dashboardState} = require("./dashboardModel");
const {ALLOWED_COMMANDS, renderDashboardHtml} = require("./dashboardView");
const {loadRuntimeManifest, readBridgeToken} = require("./runtimeManifest");
const {parseOpenUri, sameWorkspacePath} = require("./uriHandler");
const {
  formatDiagnosticReport,
  formatLocalDiagnostic,
  inspectLinkedWorktree,
  normalizeMcpToolNames,
  workspaceFingerprint,
} = require("./diagnostics");

const SECRET_KEY = "operamind.bridge.token";
const ACTIVE_TASK_KEY = "operamind.bridge.activeTask";
const PENDING_WEB_OPEN_KEY = "operamind.bridge.pendingWebOpen";
const PENDING_WEB_OPEN_MAX_AGE_MS = 5 * 60 * 1000;
const TERMINAL_STATES = new Set(["completed", "failed", "reanalysis_required", "cancelled"]);

class DashboardWebviewProvider {
  constructor(initialState) {
    this.state = dashboardState(initialState);
    this.view = undefined;
    this.messageSubscription = undefined;
  }

  update(values) {
    this.state = {...this.state, ...values};
    this.render();
  }

  resolveWebviewView(view) {
    this.view = view;
    view.webview.options = {enableScripts: true};
    this.messageSubscription?.dispose();
    this.messageSubscription = view.webview.onDidReceiveMessage(async (message) => {
      if (!message || !ALLOWED_COMMANDS.includes(message.command)) return;
      await vscode.commands.executeCommand(message.command);
    });
    this.render();
  }

  render() {
    if (!this.view) return;
    const nonce = crypto.randomBytes(18).toString("base64url");
    this.view.webview.html = renderDashboardHtml(this.state, nonce);
  }

  dispose() {
    this.messageSubscription?.dispose();
    this.view = undefined;
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
  const dashboard = new DashboardWebviewProvider({workspaceRoot: workspaceRoot()});
  const dashboardView = vscode.window.registerWebviewViewProvider(
    "operamind.controlPanel",
    dashboard,
    {webviewOptions: {retainContextWhenHidden: true}},
  );
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
      requestId: view.task.change_request_id,
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
      "後で",
    );
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
      requestId: view.task.change_request_id,
    });
    const prompt = buildCopilotPrompt(accepted, root);
    await vscode.commands.executeCommand("workbench.action.chat.open", {query: prompt});
  }

  async function checkForTasks({interactive = false, requestId} = {}) {
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
      const confirmationResponse = await client.nextConfirmation(root, requestId);
      const currentConfirmation = confirmationResponse.confirmation || undefined;
      dashboard.update({confirmation: currentConfirmation});
      if (currentConfirmation) {
        dashboard.update({
          connectionStatus: "connected",
          connectionDetail: "Web と共通の人工確認を待っています。",
        });
        if (interactive) {
          const action = await vscode.window.showInformationMessage(
            `OperaMind: ${currentConfirmation.stage_label}`,
            {modal: true, detail: "この工程の判断と入力は OperaMind Web で行います。"},
            "OperaMind Web を開く",
            "後で",
          );
          if (action === "OperaMind Web を開く") await openWeb();
        }
        return;
      }
      const active = context.workspaceState.get(ACTIVE_TASK_KEY);
      const activeMatchesRequest = !requestId || (active && active.requestId === requestId);
      if (active && active.workspaceRoot === root && activeMatchesRequest) {
        let resumed;
        try {
          resumed = await client.resumeTask(active.codingTaskId, root, consumerId);
        } catch (error) {
          if (!isMissingBridgeResource(error)) throw error;
          await clearActive(active.codingTaskId);
          dashboard.update({task: undefined, taskState: "idle"});
        }
        if (resumed) {
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
          } else {
            await presentTask(resumed, root, client, acceptedBy, {interactive});
            return;
          }
        }
      }
      if (active && active.workspaceRoot !== root) await clearActive(active.codingTaskId);
      const response = await client.nextTask(root, consumerId, requestId);
      dashboard.update({
        connectionStatus: "connected",
        connectionDetail: "loopback OperaMind Bridge に接続しています。",
      });
      if (response.task) {
        await presentTask(response.task, root, client, acceptedBy, {interactive});
      } else {
        dashboard.update({task: undefined, taskState: "idle"});
        if (interactive && requestId && active && active.workspaceRoot === root) {
          void vscode.window.showWarningMessage(
            "別の OperaMind タスクがこの Workspace で実行中です。完了または取消後に選択した変更を開いてください。",
          );
        }
      }
    } catch (error) {
      dashboard.update({
        connectionStatus: "disconnected",
        connectionDetail: `OperaMind Bridge に接続できません: ${error.message}`,
      });
      if (interactive) void vscode.window.showErrorMessage(`OperaMind Bridge: ${error.message}`);
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

  async function handleWebOpenUri(uri) {
    let target;
    try {
      target = parseOpenUri(uri);
    } catch (error) {
      void vscode.window.showErrorMessage(`OperaMind Web から開けません: ${error.message}`);
      return;
    }
    const pending = {...target, createdAt: Date.now()};
    await context.globalState.update(PENDING_WEB_OPEN_KEY, pending);
    const root = workspaceRoot();
    if (root && sameWorkspacePath(root, target.workspaceRoot)) {
      await context.globalState.update(PENDING_WEB_OPEN_KEY, undefined);
      await checkForTasks({interactive: true, requestId: target.requestId});
      return;
    }
    await vscode.commands.executeCommand(
      "vscode.openFolder",
      vscode.Uri.file(target.workspaceRoot),
      false,
    );
  }

  async function resumeWebOpen() {
    const pending = context.globalState.get(PENDING_WEB_OPEN_KEY);
    if (!pending) return false;
    if (!pending.createdAt || Date.now() - pending.createdAt > PENDING_WEB_OPEN_MAX_AGE_MS) {
      await context.globalState.update(PENDING_WEB_OPEN_KEY, undefined);
      return false;
    }
    const root = workspaceRoot();
    if (!root || !sameWorkspacePath(root, pending.workspaceRoot)) return false;
    await context.globalState.update(PENDING_WEB_OPEN_KEY, undefined);
    await checkForTasks({interactive: true, requestId: pending.requestId});
    return true;
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
    vscode.window.registerUriHandler({handleUri: handleWebOpenUri}),
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
  void resumeWebOpen().then((resumed) => {
    if (!resumed) void checkForTasks();
  });
}

function deactivate() {}

module.exports = {activate, deactivate};
