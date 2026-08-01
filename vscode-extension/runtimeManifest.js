"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

function localDataDirectory({
  platform = process.platform,
  environment = process.env,
  home = os.homedir(),
} = {}) {
  if (platform === "win32") {
    if (!environment.LOCALAPPDATA) throw new Error("LOCALAPPDATA is required on Windows");
    return path.win32.join(environment.LOCALAPPDATA, "OperaMind");
  }
  if (platform === "darwin") {
    return path.posix.join(home, "Library", "Application Support", "OperaMind");
  }
  return path.posix.join(
    environment.XDG_DATA_HOME || path.posix.join(home, ".local", "share"),
    "operamind",
  );
}

function runtimeManifestPath(options) {
  const platform = options?.platform || process.platform;
  const pathApi = platform === "win32" ? path.win32 : path.posix;
  return pathApi.join(localDataDirectory(options), "runtime.json");
}

function requireString(value, label) {
  if (typeof value !== "string" || !value.trim()) throw new Error(`${label} must not be blank`);
  return value;
}

function parseRuntimeManifest(content, {isAbsolute = path.isAbsolute} = {}) {
  const value = JSON.parse(content);
  if (!value || value.schemaVersion !== 1 || value.product !== "operamind") {
    throw new Error("Unsupported OperaMind runtime manifest");
  }
  const webUrl = new URL(requireString(value.webUrl, "webUrl"));
  if (!["127.0.0.1", "localhost", "::1", "[::1]"].includes(webUrl.hostname)) {
    throw new Error("OperaMind Web URL must use a loopback host");
  }
  if (!value.mcp || typeof value.mcp !== "object") throw new Error("mcp is required");
  const command = requireString(value.mcp.command, "mcp.command");
  const cwd = requireString(value.mcp.cwd, "mcp.cwd");
  const bridgeTokenFile = requireString(value.bridgeTokenFile, "bridgeTokenFile");
  if (![command, cwd, bridgeTokenFile].every(isAbsolute)) {
    throw new Error("OperaMind runtime paths must be absolute");
  }
  if (!Array.isArray(value.mcp.args) || value.mcp.args.some(item => typeof item !== "string")) {
    throw new Error("mcp.args must be a string array");
  }
  return {
    schemaVersion: 1,
    product: "operamind",
    version: requireString(value.version, "version"),
    webUrl: webUrl.toString().replace(/\/$/, ""),
    bridgeTokenFile,
    mcp: {command, args: [...value.mcp.args], cwd},
  };
}

function loadRuntimeManifest(file = runtimeManifestPath()) {
  return parseRuntimeManifest(fs.readFileSync(file, "utf8"));
}

function readBridgeToken(manifest) {
  const stat = fs.statSync(manifest.bridgeTokenFile);
  if (!stat.isFile() || stat.size > 16 * 1024) throw new Error("Invalid Bridge Token file");
  return requireString(fs.readFileSync(manifest.bridgeTokenFile, "utf8").trim(), "Bridge Token");
}

module.exports = {
  loadRuntimeManifest,
  localDataDirectory,
  parseRuntimeManifest,
  readBridgeToken,
  runtimeManifestPath,
};
