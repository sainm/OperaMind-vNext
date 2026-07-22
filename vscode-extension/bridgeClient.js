"use strict";

const http = require("node:http");
const https = require("node:https");

function assertLoopbackUrl(value) {
  const url = new URL(value);
  const host = url.hostname.toLowerCase();
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("OperaMind Bridge URL must use HTTP or HTTPS");
  }
  if (!new Set(["127.0.0.1", "::1", "[::1]", "localhost"]).has(host)) {
    throw new Error("OperaMind Bridge URL must be loopback-only");
  }
  return url;
}

function requestJsonOnce(baseUrl, token, method, path, body) {
  const base = assertLoopbackUrl(baseUrl);
  const target = new URL(path, base);
  const payload = body === undefined ? undefined : Buffer.from(JSON.stringify(body));
  const transport = target.protocol === "https:" ? https : http;
  return new Promise((resolve, reject) => {
    const request = transport.request(
      target,
      {
        method,
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "application/json",
          ...(payload
            ? {"Content-Type": "application/json", "Content-Length": payload.length}
            : {}),
        },
        timeout: 10_000,
      },
      (response) => {
        const chunks = [];
        response.on("data", (chunk) => chunks.push(chunk));
        response.on("end", () => {
          const text = Buffer.concat(chunks).toString("utf8");
          let parsed;
          try {
            parsed = text ? JSON.parse(text) : {};
          } catch (error) {
            reject(new Error(`OperaMind Bridge returned invalid JSON: ${error.message}`));
            return;
          }
          if (!response.statusCode || response.statusCode < 200 || response.statusCode >= 300) {
            const error = new Error(
              `OperaMind Bridge request failed (${response.statusCode || "unknown"}): ${
                parsed.detail || parsed.message || "unknown error"
              }`,
            );
            error.retryable = Boolean(response.statusCode && response.statusCode >= 500);
            reject(error);
            return;
          }
          resolve(parsed);
        });
      },
    );
    request.on("timeout", () => {
      const error = new Error("OperaMind Bridge request timed out");
      error.retryable = true;
      request.destroy(error);
    });
    request.on("error", (error) => {
      error.retryable = true;
      reject(error);
    });
    if (payload) request.write(payload);
    request.end();
  });
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function requestJson(baseUrl, token, method, path, body) {
  let lastError;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      return await requestJsonOnce(baseUrl, token, method, path, body);
    } catch (error) {
      lastError = error;
      if (!error.retryable || attempt === 2) throw error;
      await delay(250 * 2 ** attempt);
    }
  }
  throw lastError;
}

class BridgeClient {
  constructor(baseUrl, token, transport = requestJson) {
    assertLoopbackUrl(baseUrl);
    if (!token) throw new Error("OperaMind Bridge token is required");
    this.baseUrl = baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`;
    this.token = token;
    this.transport = transport;
  }

  nextTask(workspaceRoot, consumerId) {
    const query = new URLSearchParams({workspace_root: workspaceRoot, consumer_id: consumerId});
    return this.transport(
      this.baseUrl,
      this.token,
      "GET",
      `api/v1/local-bridge/tasks/next?${query}`,
    );
  }

  acceptTask(codingTaskId, workspaceRoot, consumerId, acceptedBy) {
    return this.transport(
      this.baseUrl,
      this.token,
      "POST",
      `api/v1/local-bridge/tasks/${encodeURIComponent(codingTaskId)}/accept`,
      {
        workspace_root: workspaceRoot,
        consumer_id: consumerId,
        accepted_by: acceptedBy,
      },
    );
  }

  resumeTask(codingTaskId, workspaceRoot, consumerId) {
    const query = new URLSearchParams({workspace_root: workspaceRoot, consumer_id: consumerId});
    return this.transport(
      this.baseUrl,
      this.token,
      "GET",
      `api/v1/local-bridge/tasks/${encodeURIComponent(codingTaskId)}/resume?${query}`,
    );
  }

  cancelTask(codingTaskId, workspaceRoot, consumerId, cancelledBy, reason) {
    return this.transport(
      this.baseUrl,
      this.token,
      "POST",
      `api/v1/local-bridge/tasks/${encodeURIComponent(codingTaskId)}/cancel`,
      {
        workspace_root: workspaceRoot,
        consumer_id: consumerId,
        cancelled_by: cancelledBy,
        reason,
      },
    );
  }

  reportDiagnostics(report) {
    return this.transport(
      this.baseUrl,
      this.token,
      "POST",
      "api/v1/local-bridge/diagnostics",
      report,
    );
  }
}

function buildCopilotPrompt(taskView, workspaceRoot) {
  const task = taskView && taskView.task;
  if (!task || task.execution_mode !== "copilot_coding_plan") {
    throw new Error("OperaMind task is not a Copilot Coding Plan");
  }
  const tools = Array.isArray(task.required_mcp_tools) ? task.required_mcp_tools.join(", ") : "";
  return [
    `OperaMind の承認済み Coding Plan タスク ${task.coding_task_id} を実行してください。`,
    `対象 Workspace: ${workspaceRoot}`,
    `要求: ${task.task_summary}`,
    "最初に operaMind MCP の copilot_get_coding_task を task ID と Workspace で呼び出してください。",
    "返された Edit Packet の editable/test 範囲だけを変更し、範囲拡大が必要なら停止してください。",
    "テストは copilot_run_task_command、Diff は copilot_validate_task_diff、commit 後の結果は copilot_record_task_result で記録してください。",
    `必要な MCP tools: ${tools}`,
    "Context Package や未承認ファイルを要求せず、任意の shell command を実行しないでください。",
  ].join("\n");
}

module.exports = {
  BridgeClient,
  assertLoopbackUrl,
  buildCopilotPrompt,
  requestJson,
  requestJsonOnce,
};
