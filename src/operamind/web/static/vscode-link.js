(function(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.OperaMindVsCodeLink = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function() {
  "use strict";

  const EXTENSION_AUTHORITY = "operamind-local.operamind-copilot-bridge";
  const CONTROL_CHARACTER_PATTERN = /[\u0000\r\n]/;

  function buildOpenUrl(workspaceRoot, requestId = null) {
    const workspace = String(workspaceRoot || "").trim();
    if (!isAbsoluteWorkspacePath(workspace)) {
      throw new Error("コード Workspace にローカル絶対パスを設定してください。");
    }
    if (workspace.length > 4000 || CONTROL_CHARACTER_PATTERN.test(workspace)) {
      throw new Error("コード Workspace のパスが不正です。");
    }

    const params = new URLSearchParams({workspace});
    const request = String(requestId || "").trim();
    if (request) {
      if (request.length > 160 || CONTROL_CHARACTER_PATTERN.test(request)) {
        throw new Error("変更番号が不正です。");
      }
      params.set("request", request);
    }
    return `vscode://${EXTENSION_AUTHORITY}/open?${params.toString()}`;
  }

  function isAbsoluteWorkspacePath(value) {
    return value.startsWith("/")
      || /^[A-Za-z]:[\\/]/.test(value)
      || /^\\\\[^\\]/.test(value);
  }

  return {buildOpenUrl, EXTENSION_AUTHORITY};
});
