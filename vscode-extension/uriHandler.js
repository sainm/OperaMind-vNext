"use strict";

const path = require("node:path");

const EXTENSION_AUTHORITY = "operamind-local.operamind-copilot-bridge";
const OPEN_PATH = "/open";
const ALLOWED_QUERY_KEYS = new Set(["workspace", "request"]);
const CONTROL_CHARACTER_PATTERN = /[\u0000\r\n]/;

function parseOpenUri(value) {
  const parts = uriParts(value);
  if (parts.scheme !== "vscode") throw new Error("対応していない URI scheme です。");
  if (parts.authority.toLowerCase() !== EXTENSION_AUTHORITY) {
    throw new Error("別の Extension 宛ての URI です。");
  }
  if (parts.path !== OPEN_PATH) throw new Error("対応していない OperaMind URI です。");

  const params = new URLSearchParams(parts.query);
  for (const key of params.keys()) {
    if (!ALLOWED_QUERY_KEYS.has(key)) throw new Error(`許可されていない URI parameter です: ${key}`);
  }
  if (params.getAll("workspace").length !== 1 || params.getAll("request").length > 1) {
    throw new Error("URI parameter が重複しています。");
  }

  const workspaceRoot = params.get("workspace") || "";
  validateWorkspacePath(workspaceRoot);
  const requestId = params.get("request") || undefined;
  if (requestId && (requestId.length > 160 || CONTROL_CHARACTER_PATTERN.test(requestId))) {
    throw new Error("変更番号が不正です。");
  }
  return {workspaceRoot, requestId};
}

function uriParts(value) {
  if (typeof value === "string") {
    const parsed = new URL(value);
    return {
      scheme: parsed.protocol.slice(0, -1),
      authority: parsed.host,
      path: parsed.pathname,
      query: parsed.search.slice(1),
    };
  }
  return {
    scheme: String(value && value.scheme || ""),
    authority: String(value && value.authority || ""),
    path: String(value && value.path || ""),
    query: String(value && value.query || ""),
  };
}

function validateWorkspacePath(value) {
  if (!value || value.length > 4000 || CONTROL_CHARACTER_PATTERN.test(value)) {
    throw new Error("コード Workspace のパスが不正です。");
  }
  if (!path.posix.isAbsolute(value) && !path.win32.isAbsolute(value)) {
    throw new Error("コード Workspace はローカル絶対パスで指定してください。");
  }
}

function sameWorkspacePath(left, right) {
  if (!left || !right) return false;
  const windowsPath = isWindowsPath(left) || isWindowsPath(right);
  if (windowsPath) {
    return path.win32.normalize(left).toLowerCase() === path.win32.normalize(right).toLowerCase();
  }
  return path.posix.normalize(left) === path.posix.normalize(right);
}

function isWindowsPath(value) {
  return /^[A-Za-z]:[\\/]/.test(value) || value.startsWith("\\\\");
}

module.exports = {EXTENSION_AUTHORITY, parseOpenUri, sameWorkspacePath};
