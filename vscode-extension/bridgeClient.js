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
            error.statusCode = response.statusCode;
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

function isMissingBridgeResource(error) {
  return Boolean(error && error.statusCode === 404);
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

  nextTask(workspaceRoot, consumerId, changeRequestId) {
    const query = new URLSearchParams({workspace_root: workspaceRoot, consumer_id: consumerId});
    if (changeRequestId) query.set("change_request_id", changeRequestId);
    return this.transport(
      this.baseUrl,
      this.token,
      "GET",
      `api/v1/local-bridge/tasks/next?${query}`,
    );
  }

  nextConfirmation(workspaceRoot, changeRequestId) {
    const query = new URLSearchParams({workspace_root: workspaceRoot});
    if (changeRequestId) query.set("change_request_id", changeRequestId);
    return this.transport(
      this.baseUrl,
      this.token,
      "GET",
      `api/v1/local-bridge/confirmations/next?${query}`,
    );
  }

  decideConfirmation(requestId, checkpoint, decision, actor, idempotencyKey, note) {
    return this.transport(
      this.baseUrl,
      this.token,
      "POST",
      `api/v1/local-bridge/change-requests/${encodeURIComponent(requestId)}` +
        `/confirmations/${encodeURIComponent(checkpoint)}`,
      {
        decision,
        actor,
        idempotency_key: idempotencyKey,
        ...(note ? {note} : {}),
      },
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
  if (!task || task.execution_mode !== "copilot_change_task") {
    throw new Error("OperaMind task is not a unified Copilot Change Task");
  }
  const tools = Array.isArray(task.required_mcp_tools) ? task.required_mcp_tools.join(", ") : "";
  if (task.task_kind === "ui_test_plan_revision" || task.initial_stage === "ui_test_revision") {
    return [
      `OperaMind UI TestPlan revision Task ${task.coding_task_id} を実行してください。`,
      `対象 Workspace: ${workspaceRoot}`,
      `要求: ${task.task_summary}`,
      "最初に operaMind MCP の copilot_get_coding_task を task ID と Workspace で呼び出してください。",
      "source_ui_test_plan、source_test_data_plan、revision_instruction、confirmed_change_summary を読み、現在の UI 計画の意味を理解してください。",
      "コード、設計書、Git 履歴、Scope、Approval を変更せず、画面横断の自然言語 UiTestPlan と完全な TestDataPlan だけを再生成してください。",
      "UiTestPlan の全 Case は execution_mode=browser の UI Case とし、各 UI Step に同一 Origin 内の限定 Playwright action、UI assertion、Screenshot、cleanup を含めてください。",
      "TestDataPlan の data_sets、generation_flows、変数、依存順、最終業務 Assertion、cleanup を省略せず、全 UiTestPlan Case を過不足なく覆ってください。",
      "copilot_record_change_outputs に output_stage=ui_test_revision、test_plan、test_data_plan を渡してください。",
      "next_context が null の場合は先へ進まず、flow_status の停止理由を報告してください。",
      `必要な MCP tools: ${tools}`,
    ].join("\n");
  }
  const target = task.target_project || {};
  const targetLines = target.detection_status === "supported"
    ? [
        `対象技術 Stack: ${target.framework} / ${target.template_engine} / ${target.build_system}`,
        `変更制約: ${(target.change_constraints || []).join("、")}`,
        `固定 compile: ${(target.compile_command || []).join(" ")}`,
        `固定 test: ${(target.test_command || []).join(" ")}`,
        `固定 build: ${(target.build_command || []).join(" ")}`,
      ]
    : [
        "対象技術 Stack は自動確定していません。MCP が返す限定範囲を越えて Framework や Build Tool を変更しないでください。",
      ];
  return [
    `OperaMind Change Task ${task.coding_task_id} を実行してください。`,
    `対象 Workspace: ${workspaceRoot}`,
    `要求: ${task.task_summary}`,
    ...targetLines,
    "最初に operaMind MCP の copilot_get_coding_task を task ID と Workspace で呼び出してください。",
    "document_discovery が blocked の場合は編集せず、その blocking_reason を報告してください。",
    "設計書段階では Canonical RAG 候補の canonical_document facts から変更対象の stable_key、field、new_value を決め、output_stage=document_change、document_ids、document_edits を copilot_record_change_outputs に渡してください。XLSX 原本は OperaMind が限定セルだけ更新するため、コード Workspace 内でファイルを探したり shell で編集したりしないでください。",
    "次にコードを読み取り専用で調査し、Graph で確認できる code_scope を output_stage=code_scope として記録してください。",
    "copilot_get_coding_task の返却自体を現在の authoritative context として扱ってください。この Tool は next_context を返さないため、next_context の欠落を停止理由にしないでください。",
    "copilot_get_coding_task が current_stage=compile_test かつ execution_scope.bound=true を返した後だけ、editable/test 範囲内でコードとテストを変更してください。",
    "コード変更後、まず copilot_validate_task_diff を実行してください。",
    "次に、MCP が返した必須のコンパイル、コードテスト、カバレッジコマンドを copilot_run_task_command ですべて実行し、成功を確認してください。",
    "必須コマンドがすべて成功した後、検証済みコード差分を commit し、全 Command Evidence を指定して copilot_record_task_result を記録してください。",
    "next_context が test_planning に進んだことを確認した後だけ、committed コード差分、設計差分、要件から自然言語 UI TestPlan と実行可能 TestDataPlan を作り、output_stage=test_planning として記録してください。",
    "UI TestPlan の全 Case は browser UI とし、TestDataPlan の各 UI step に限定 Playwright action、UI assertion、Screenshot、cleanup を含めてください。",
    "状態を変更する MCP Tool が next_context を含めて返した場合だけ、その値を次工程判定に使用してください。next_context=null は人工確認待ちなので停止し、確認後の再開時は copilot_get_coding_task を再取得してください。",
    `必要な MCP tools: ${tools}`,
    "範囲外ファイルが必要な場合は停止し、任意の shell command を実行しないでください。",
  ].join("\n");
}

module.exports = {
  BridgeClient,
  assertLoopbackUrl,
  buildCopilotPrompt,
  isMissingBridgeResource,
  requestJson,
  requestJsonOnce,
};
