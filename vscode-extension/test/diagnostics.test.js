"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  EXPECTED_MCP_TOOL_NAMES,
  formatDiagnosticReport,
  inspectLinkedWorktree,
  normalizeMcpToolNames,
  workspaceFingerprint,
} = require("../diagnostics");

test("normalizes the exact five unified Change Task MCP tool names", () => {
  const raw = EXPECTED_MCP_TOOL_NAMES.map((name) => `mcp_operamind_${name}`);
  assert.deepEqual(normalizeMcpToolNames([...raw, "unrelated_tool"]), [
    ...EXPECTED_MCP_TOOL_NAMES,
  ].sort());
  assert.equal(normalizeMcpToolNames(raw).length, 5);
});

test("recognizes a linked worktree git file but not a normal repository git directory", async () => {
  const temporary = await fs.mkdtemp(path.join(os.tmpdir(), "operamind-diagnostic-"));
  const linked = path.join(temporary, "linked");
  const normal = path.join(temporary, "normal");
  const metadata = path.join(temporary, "metadata");
  await fs.mkdir(linked);
  await fs.mkdir(normal);
  await fs.mkdir(metadata);
  await fs.mkdir(path.join(normal, ".git"));
  await fs.writeFile(path.join(linked, ".git"), `gitdir: ${metadata}\n`, "utf8");
  await fs.writeFile(path.join(metadata, "commondir"), "../..\n", "utf8");
  await fs.writeFile(path.join(metadata, "gitdir"), path.join(linked, ".git"), "utf8");

  assert.equal(await inspectLinkedWorktree(linked), true);
  assert.equal(await inspectLinkedWorktree(normal), false);
  assert.match(workspaceFingerprint(linked), /^[0-9a-f]{64}$/);
});

test("does not mistake a submodule-style git file for a linked worktree", async () => {
  const temporary = await fs.mkdtemp(path.join(os.tmpdir(), "operamind-submodule-"));
  const workspace = path.join(temporary, "workspace");
  const metadata = path.join(temporary, "modules", "workspace");
  await fs.mkdir(workspace, {recursive: true});
  await fs.mkdir(metadata, {recursive: true});
  await fs.writeFile(path.join(workspace, ".git"), `gitdir: ${metadata}\n`, "utf8");

  assert.equal(await inspectLinkedWorktree(workspace), false);
});

test("Japanese formatter provides safe manual repair guidance", () => {
  const rendered = formatDiagnosticReport({
    overall_status: "blocked",
    generated_at: "2026-07-20T00:00:00Z",
    checks: [
      {
        label: "Workspace Trust",
        status: "blocked",
        summary: "未確認です。",
        remediation: "ユーザー自身で確認してください。",
      },
    ],
  });

  assert.match(rendered, /修復指引/);
  assert.match(rendered, /自動変更しません/);
  assert.doesNotMatch(rendered, /postgresql:\/\//);
});

test("local formatter marks Copilot quota unknown and gives bounded repair steps", () => {
  const rendered = require("../diagnostics").formatLocalDiagnostic(
    {
      vsix_version: "0.3.1",
      bridge_url_loopback: true,
      bridge_token_configured: false,
      workspace_trusted: true,
      linked_worktree: false,
      mcp_tool_names: EXPECTED_MCP_TOOL_NAMES.slice(0, 9),
      copilot_extension_installed: true,
      copilot_extension_active: true,
      copilot_model_api_available: true,
      copilot_model_count: 4,
    },
    "Bridge Token が未設定です",
  );

  assert.match(rendered, /Credit\/Quota 未検証/);
  assert.match(rendered, /Launcher を起動/);
  assert.match(rendered, /git worktree add/);
  assert.match(rendered, /自動変更しません/);
});
