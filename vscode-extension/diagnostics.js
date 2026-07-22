"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs/promises");
const path = require("node:path");

const EXPECTED_MCP_TOOL_NAMES = Object.freeze([
  "analysis_list_ready_cases",
  "impact_get_report",
  "copilot_get_edit_packet",
  "copilot_get_approval_grant",
  "copilot_run_approved_command",
  "copilot_validate_worktree",
  "copilot_record_edit_result",
  "copilot_get_coding_task",
  "copilot_run_task_command",
  "copilot_validate_task_diff",
  "copilot_record_task_result",
  "verification_get_ui_plan",
  "validation_get_result",
]);

function normalizeMcpToolNames(rawNames) {
  const normalized = new Set();
  for (const rawName of rawNames || []) {
    const name = typeof rawName === "string" ? rawName.toLowerCase() : "";
    for (const expected of EXPECTED_MCP_TOOL_NAMES) {
      if (name === expected || name.endsWith(`_${expected}`)) normalized.add(expected);
    }
  }
  return [...normalized].sort();
}

async function inspectLinkedWorktree(root, fileSystem = fs) {
  if (!root) return false;
  const dotGit = path.join(root, ".git");
  try {
    const stat = await fileSystem.stat(dotGit);
    if (!stat.isFile()) return false;
    const content = await fileSystem.readFile(dotGit, {encoding: "utf8"});
    if (content.length > 4096) return false;
    const match = /^gitdir:\s*(.+)\s*$/im.exec(content);
    if (!match) return false;
    const gitDirectory = path.resolve(root, match[1]);
    const gitStat = await fileSystem.stat(gitDirectory);
    if (!gitStat.isDirectory()) return false;
    const commonDirStat = await fileSystem.stat(path.join(gitDirectory, "commondir"));
    const worktreeBacklinkStat = await fileSystem.stat(path.join(gitDirectory, "gitdir"));
    return commonDirStat.isFile() && worktreeBacklinkStat.isFile();
  } catch (_error) {
    return false;
  }
}

function workspaceFingerprint(root) {
  if (!root) return null;
  const normalized = path.resolve(root).normalize();
  return crypto.createHash("sha256").update(normalized, "utf8").digest("hex");
}

function formatDiagnosticReport(result) {
  const lines = [
    "OperaMind ローカル環境診断",
    `総合結果: ${result.overall_status || "不明"}`,
    `診断日時: ${result.generated_at || "不明"}`,
    "",
  ];
  for (const check of result.checks || []) {
    const mark = check.status === "passed" ? "✓" : check.status === "warning" ? "!" : "✗";
    lines.push(`${mark} ${check.label}: ${check.summary}`);
    if (check.status !== "passed") lines.push(`  修復指引: ${check.remediation}`);
  }
  lines.push(
    "",
    "安全方針: Token、DB URL、Workspace path、ソースコードは収集・表示しません。",
    "Workspace Trust、認証情報、Migration は自動変更しません。",
  );
  return lines.join("\n");
}

function formatLocalDiagnostic(report, errorMessage) {
  const available = new Set(report.mcp_tool_names);
  const missing = EXPECTED_MCP_TOOL_NAMES.filter((name) => !available.has(name));
  const copilotReady =
    report.copilot_extension_installed &&
    report.copilot_extension_active &&
    report.copilot_model_api_available &&
    report.copilot_model_count > 0;
  const lines = [
    "OperaMind ローカル環境診断（VS Code ローカル結果）",
    `VSIX: ${report.vsix_version}`,
    `Bridge URL: ${report.bridge_url_loopback ? "loopback" : "不正"}`,
    `Bridge Token: ${report.bridge_token_configured ? "設定済み" : "未設定"}`,
    `Workspace Trust: ${report.workspace_trusted ? "Trusted" : "未確認"}`,
    `linked worktree: ${report.linked_worktree ? "確認済み" : "未確認"}`,
    `MCP tools: ${report.mcp_tool_names.length}/${EXPECTED_MCP_TOOL_NAMES.length}`,
    `GitHub Copilot Chat: ${copilotReady ? "有効" : "利用不可"}`,
    `GitHub Copilot models: ${report.copilot_model_count}（Credit/Quota 未検証）`,
  ];
  if (missing.length) lines.push(`不足 MCP tools: ${missing.join(", ")}`);
  if (errorMessage) lines.push(`Web 連携: ${errorMessage}`);
  const repair = [];
  if (!report.bridge_url_loopback) {
    repair.push("Bridge URL を 127.0.0.1、localhost、または ::1 に戻してください。");
  }
  if (!report.bridge_token_configured) {
    repair.push(
      "「OperaMind: Bridge Token を安全に登録」で Web と同じ Token を SecretStorage に登録してください。",
    );
  }
  if (!report.workspace_trusted) {
    repair.push("Workspace Trust はユーザー自身で確認してください。自動変更しません。");
  }
  if (!report.linked_worktree) {
    repair.push("git worktree add で隔離 Workspace を作成し、そのフォルダーを開いてください。");
  }
  if (missing.length) {
    repair.push("operaMind MCP の PostgreSQL 入力、起動ログ、Migration を確認してください。");
  }
  if (!copilotReady) {
    repair.push("Copilot Chat のインストール、サインイン、組織 Policy を確認してください。");
  }
  repair.push("実会話前に VS Code の Copilot Credit/Quota 表示を確認してください。");
  lines.push("", "安全な修復指引:", ...repair.map((item) => `- ${item}`));
  lines.push(
    "",
    "安全方針: Token、DB URL、Workspace path、ソースコードは収集・表示しません。",
    "Workspace Trust、認証情報、Migration は自動変更しません。",
  );
  return lines.join("\n");
}

module.exports = {
  EXPECTED_MCP_TOOL_NAMES,
  formatDiagnosticReport,
  formatLocalDiagnostic,
  inspectLinkedWorktree,
  normalizeMcpToolNames,
  workspaceFingerprint,
};
