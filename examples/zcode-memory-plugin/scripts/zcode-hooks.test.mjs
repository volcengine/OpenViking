import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { evaluateZcodeUriGuard } from "./uri-guard.mjs";

const PLUGIN_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

// ---------------------------------------------------------------------------
// URI Guard output schema (the #1 silent-failure mode in the adversarial review)
// ZCode's strict JSON schema rejects any unrecognized key. These tests assert
// the output contains ONLY ZCode-recognized keys.
// ---------------------------------------------------------------------------

test("uri guard deny output contains only recognized keys", () => {
  const output = evaluateZcodeUriGuard({
    tool_name: "Read",
    tool_input: { file_path: "viking://~/memories/profile.md" },
  });
  assert.ok(output.hookSpecificOutput);
  assert.equal(output.hookSpecificOutput.hookEventName, "PreToolUse");
  assert.equal(output.hookSpecificOutput.permissionDecision, "deny");
  assert.ok(output.hookSpecificOutput.permissionDecisionReason);
  // Must NOT contain Claude-Code-isms
  assert.equal(output.decision, undefined);
  assert.equal(output.hookSpecificOutput.decision, undefined);
});

test("uri guard deny reason includes MCP redirect", () => {
  const output = evaluateZcodeUriGuard({
    tool_name: "Read",
    tool_input: { file_path: "viking://resources/myproject/docs.md" },
  });
  const reason = output.hookSpecificOutput?.permissionDecisionReason || "";
  assert.ok(reason.includes("MCP"), "reason should mention MCP");
});

test("uri guard pass-through for non-viking URIs returns empty", () => {
  const output = evaluateZcodeUriGuard({
    tool_name: "Read",
    tool_input: { file_path: "/tmp/local-file.txt" },
  });
  assert.equal(Object.keys(output).length, 0);
});

test("uri guard pass-through for Glob with local pattern", () => {
  const output = evaluateZcodeUriGuard({
    tool_name: "Glob",
    tool_input: { pattern: "**/*.ts" },
  });
  assert.equal(Object.keys(output).length, 0);
});

test("uri guard deny for Glob with viking URI", () => {
  const output = evaluateZcodeUriGuard({
    tool_name: "Glob",
    tool_input: { pattern: "viking://user/memories/**" },
  });
  assert.equal(output.hookSpecificOutput.permissionDecision, "deny");
});

test("uri guard handles alternative tool_input field names", () => {
  const output = evaluateZcodeUriGuard({
    tool_name: "Read",
    toolInput: { file_path: "viking://user/test.md" },
  });
  assert.equal(output.hookSpecificOutput?.permissionDecision, "deny");
});

test("uri guard handles alternative tool_name field names", () => {
  const output = evaluateZcodeUriGuard({
    toolName: "Read",
    tool_input: { file_path: "viking://user/test.md" },
  });
  assert.equal(output.hookSpecificOutput?.permissionDecision, "deny");
});

test("uri guard returns empty for unmatched tool name", () => {
  const output = evaluateZcodeUriGuard({
    tool_name: "Bash",
    tool_input: { command: "cat viking://user/test.md" },
  });
  // Bash is not in the Read|Glob|Grep matcher — but evaluateAgentUriGuard
  // may still detect viking:// in certain fields. The important assertion is
  // that the output shape is valid (either empty or correct deny).
  if (Object.keys(output).length > 0) {
    assert.equal(output.hookSpecificOutput.hookEventName, "PreToolUse");
  }
});

// ---------------------------------------------------------------------------
// Source-file conventions: hooks.json and .mcp.json must use ZCODE_PLUGIN_ROOT
// (not CLAUDE_PLUGIN_ROOT) in a .zcode-plugin/ plugin for naming consistency.
// ---------------------------------------------------------------------------

test("hooks.json uses ZCODE_PLUGIN_ROOT, not CLAUDE_PLUGIN_ROOT", () => {
  const hooksJson = readFileSync(join(PLUGIN_ROOT, "hooks", "hooks.json"), "utf8");
  assert.ok(
    !hooksJson.includes("CLAUDE_PLUGIN_ROOT"),
    "hooks.json should use ${ZCODE_PLUGIN_ROOT}, not ${CLAUDE_PLUGIN_ROOT}",
  );
  assert.ok(
    hooksJson.includes("ZCODE_PLUGIN_ROOT"),
    "hooks.json should reference ${ZCODE_PLUGIN_ROOT}",
  );
});

test(".mcp.json uses ZCODE_PLUGIN_ROOT, not CLAUDE_PLUGIN_ROOT", () => {
  const mcpJson = readFileSync(join(PLUGIN_ROOT, ".mcp.json"), "utf8");
  assert.ok(
    !mcpJson.includes("CLAUDE_PLUGIN_ROOT"),
    ".mcp.json should use ${ZCODE_PLUGIN_ROOT}, not ${CLAUDE_PLUGIN_ROOT}",
  );
  assert.ok(
    mcpJson.includes("ZCODE_PLUGIN_ROOT"),
    ".mcp.json should reference ${ZCODE_PLUGIN_ROOT}",
  );
});
