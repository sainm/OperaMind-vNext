"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {parseOpenUri, sameWorkspacePath} = require("../uriHandler");

test("web handoff accepts a local POSIX workspace and selected request", () => {
  const result = parseOpenUri(
    "vscode://operamind-local.operamind-copilot-bridge/open?workspace=%2FUsers%2Fme%2Fdemo&request=change-42",
  );

  assert.deepEqual(result, {workspaceRoot: "/Users/me/demo", requestId: "change-42"});
});

test("web handoff accepts and compares Windows workspace paths case-insensitively", () => {
  const result = parseOpenUri(
    "vscode://operamind-local.operamind-copilot-bridge/open?workspace=C%3A%5Cwork%5Cexpense-system",
  );

  assert.equal(result.workspaceRoot, "C:\\work\\expense-system");
  assert.equal(sameWorkspacePath("C:\\Work\\expense-system", result.workspaceRoot), true);
});

test("web handoff rejects relative paths, unknown actions, and secret parameters", () => {
  assert.throws(
    () => parseOpenUri("vscode://operamind-local.operamind-copilot-bridge/open?workspace=relative"),
    /絶対パス/,
  );
  assert.throws(
    () => parseOpenUri("vscode://operamind-local.operamind-copilot-bridge/run?workspace=%2Ftmp"),
    /対応していない/,
  );
  assert.throws(
    () => parseOpenUri(
      "vscode://operamind-local.operamind-copilot-bridge/open?workspace=%2Ftmp&token=secret",
    ),
    /許可されていない/,
  );
});

test("web handoff rejects duplicate workspace parameters", () => {
  assert.throws(
    () => parseOpenUri(
      "vscode://operamind-local.operamind-copilot-bridge/open?workspace=%2Fa&workspace=%2Fb",
    ),
    /重複/,
  );
});
