"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");

const {
  localDataDirectory,
  parseRuntimeManifest,
  runtimeManifestPath,
} = require("../runtimeManifest");

test("runtime manifest path is user-level on macOS and Windows", () => {
  assert.equal(
    runtimeManifestPath({platform: "darwin", environment: {}, home: "/Users/tester"}),
    "/Users/tester/Library/Application Support/OperaMind/runtime.json",
  );
  assert.equal(
    localDataDirectory({
      platform: "win32",
      environment: {LOCALAPPDATA: "C:\\Users\\tester\\AppData\\Local"},
      home: "C:\\Users\\tester",
    }),
    "C:\\Users\\tester\\AppData\\Local\\OperaMind",
  );
});

test("runtime manifest accepts a bounded loopback MCP definition", () => {
  const value = parseRuntimeManifest(
    JSON.stringify({
      schemaVersion: 1,
      product: "operamind",
      version: "1.2.3",
      webUrl: "http://127.0.0.1:8765",
      bridgeTokenFile: "/Users/tester/Library/Application Support/OperaMind/bridge-token",
      mcp: {
        command: "/Applications/OperaMind.app/Contents/MacOS/OperaMind",
        args: ["--mcp"],
        cwd: "/Users/tester/Library/Application Support/OperaMind/runtime/1.2.3",
      },
    }),
    {isAbsolute: path.posix.isAbsolute},
  );

  assert.equal(value.webUrl, "http://127.0.0.1:8765");
  assert.deepEqual(value.mcp.args, ["--mcp"]);
});

test("runtime manifest accepts native Windows paths", () => {
  const value = parseRuntimeManifest(
    JSON.stringify({
      schemaVersion: 1,
      product: "operamind",
      version: "1.2.3",
      webUrl: "http://localhost:8765",
      bridgeTokenFile: "C:\\Users\\tester\\AppData\\Local\\OperaMind\\bridge-token",
      mcp: {
        command: "C:\\Program Files\\OperaMind\\OperaMind.exe",
        args: ["--mcp"],
        cwd: "C:\\Users\\tester\\AppData\\Local\\OperaMind\\runtime\\1.2.3",
      },
    }),
    {isAbsolute: path.win32.isAbsolute},
  );

  assert.equal(value.mcp.command, "C:\\Program Files\\OperaMind\\OperaMind.exe");
  assert.equal(value.webUrl, "http://localhost:8765");
});

test("runtime manifest rejects a remote Web URL", () => {
  assert.throws(
    () => parseRuntimeManifest(JSON.stringify({
      schemaVersion: 1,
      product: "operamind",
      version: "1.2.3",
      webUrl: "https://example.com",
      bridgeTokenFile: "/tmp/token",
      mcp: {command: "/tmp/OperaMind", args: [], cwd: "/tmp"},
    })),
    /loopback/,
  );
});
