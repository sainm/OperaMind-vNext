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

  acceptTask(codingTaskId, workspaceRoot, consumerId, acceptedBy, claimToken = undefined) {
    return this.transport(
      this.baseUrl,
      this.token,
      "POST",
      `api/v1/local-bridge/tasks/${encodeURIComponent(codingTaskId)}/accept`,
      {
        workspace_root: workspaceRoot,
        consumer_id: consumerId,
        ...(claimToken ? {claim_token: claimToken} : {}),
        accepted_by: acceptedBy,
      },
    );
  }

  resumeTask(codingTaskId, workspaceRoot, consumerId, claimToken = undefined) {
    const query = new URLSearchParams({workspace_root: workspaceRoot, consumer_id: consumerId});
    if (claimToken) query.set("claim_token", claimToken);
    return this.transport(
      this.baseUrl,
      this.token,
      "GET",
      `api/v1/local-bridge/tasks/${encodeURIComponent(codingTaskId)}/resume?${query}`,
    );
  }

  cancelTask(
    codingTaskId,
    workspaceRoot,
    consumerId,
    cancelledBy,
    reason,
    claimToken = undefined,
  ) {
    return this.transport(
      this.baseUrl,
      this.token,
      "POST",
      `api/v1/local-bridge/tasks/${encodeURIComponent(codingTaskId)}/cancel`,
      {
        workspace_root: workspaceRoot,
        consumer_id: consumerId,
        ...(claimToken ? {claim_token: claimToken} : {}),
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

function buildCopilotPrompt(taskView, workspaceRoot, consumerId = undefined) {
  const task = taskView && taskView.task;
  if (!task || task.execution_mode !== "copilot_change_task") {
    throw new Error("OperaMind task is not a unified Copilot Change Task");
  }
  const currentStage = taskView.current_stage || task.initial_stage || "document_change";
  const stage = stagePrompt(currentStage, task.task_kind);
  const lines = [
    `OperaMind Change Task: ${task.coding_task_id}`,
    `対象 Workspace: ${workspaceRoot}`,
    `業務要件: ${task.task_summary}`,
    `現在工程: ${stage.label}`,
    `目的: ${stage.goal}`,
    `入力: ${stage.input}`,
    `出力: ${stage.output}`,
    `停止条件: ${stage.stop}`,
    `最初に operaMind MCP の copilot_get_coding_task を coding_task_id=${task.coding_task_id} とこの Workspace で呼び出してください。`,
    "完了後は stage_status.next_action に従い、wait_for_confirmation または stop ならそこで停止してください。",
  ];
  if (currentStage === "document_profile_learning") {
    if (!consumerId || !taskView.claim_token) {
      throw new Error("Document Profile learning claim identity is missing");
    }
    lines.splice(
      9,
      0,
      `document_profile_learning の全 MCP 呼び出しに consumer_id=${consumerId} と claim_token=${taskView.claim_token} を渡してください。`,
    );
  }
  return lines.join("\n");
}

function stagePrompt(currentStage, taskKind) {
  if (taskKind === "document_profile_learning" || currentStage === "document_profile_learning") {
    return {
      label: "Project 設計書学習",
      goal: "抽出済み XLSX／DOCX 構造から、この Project 専用の DocumentConventionProfile を作成する。",
      input: "MCP が返す構造 Sample、前回 Profile、構造差分、Schema 制約。",
      output: "copilot_record_change_outputs で document_profile_learning の草案を記録する。",
      stop: "Coverage 100% かつ曖昧 0 件の草案を記録したら、OperaMind Web の確認を待つ。",
    };
  }
  if (taskKind === "ui_test_plan_revision" || currentStage === "ui_test_revision") {
    return {
      label: "UI テスト計画の再作成",
      goal: "確認済みの自然言語修正だけを反映し、完全な UI TestPlan と TestDataPlan を再作成する。",
      input: "MCP が返す現行計画、確認済み修正、実行制約。",
      output: "copilot_record_change_outputs で ui_test_revision の完全な両計画を記録する。",
      stop: "計画を記録したら、OperaMind Web の確認を待つ。",
    };
  }
  const stages = {
    document_change: {
      label: "設計書変更",
      goal: "RAG が特定した設計書の必要箇所だけを業務要件に合わせて更新する。",
      input: "MCP が返す業務要件と Canonical RAG 文書候補。",
      output: "copilot_record_change_outputs で document_change の対象文書と項目変更を記録する。",
      stop: "設計書差分を記録したら、OperaMind Web の確認を待つ。",
    },
    code_scope: {
      label: "コード影響範囲",
      goal: "確認済み設計差分に対応するコードとテストの影響範囲を読み取り専用で特定する。",
      input: "MCP が返す業務要件、設計差分、Code Graph 検証条件。",
      output: "copilot_record_change_outputs で code_scope の対象 Path、Symbol、関連テスト、根拠を記録する。",
      stop: "影響範囲を記録したら、OperaMind Web の確認を待つ。",
    },
    compile_test: {
      label: "コード変更・コンパイル・テスト",
      goal: "確認済み範囲だけを変更し、差分、必須 Command、変更行 Coverage を検証する。",
      input: "MCP が返す編集可能 Path、読取 Path、テスト Path、必須 Command。",
      output: "差分検証、必須 Command、commit、copilot_record_task_result を順に完了する。",
      stop: "結果記録後、reload_current_task なら現在 Task を再取得し、それ以外は停止する。",
    },
    test_planning: {
      label: "UI テスト計画",
      goal: "確定済み設計とコードから、全業務要件を覆う UI TestPlan と TestDataPlan を作成する。",
      input: "MCP が返す業務要件、実行範囲、シナリオ、Evidence 制約。",
      output: "copilot_record_change_outputs で test_planning の完全な UI TestPlan と TestDataPlan を記録する。",
      stop: "業務 Coverage 100% で計画が受理されたら、OperaMind Web の確認を待つ。",
    },
  };
  if (!stages[currentStage]) throw new Error(`Unsupported OperaMind task stage: ${currentStage}`);
  return stages[currentStage];
}

module.exports = {
  BridgeClient,
  assertLoopbackUrl,
  buildCopilotPrompt,
  isMissingBridgeResource,
  requestJson,
  requestJsonOnce,
};
