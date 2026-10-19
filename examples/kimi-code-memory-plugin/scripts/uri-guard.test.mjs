/**
 * PreToolUse URI guard (spec §7): a `viking://` path is denied with a pointer at
 * the MCP tools; a shell command that merely mentions one is left alone.
 */

import assert from "node:assert/strict";
import test from "node:test";

import { evaluatePreToolUse } from "./uri-guard.mjs";

test("a file tool aimed at a viking:// URI is denied with the MCP pointer", () => {
  const out = evaluatePreToolUse({ tool_name: "Read", tool_input: { path: "viking://user/default/memories/x.md" } });
  assert.equal(out.hookSpecificOutput?.permissionDecision, "deny");
  assert.match(out.hookSpecificOutput?.permissionDecisionReason || "", /MCP/);
});

test("a normal path passes through untouched", () => {
  assert.deepEqual(evaluatePreToolUse({ tool_name: "Read", tool_input: { path: "./README.md" } }), {});
});

test("a shell command that mentions a viking:// URI as data is not denied", () => {
  const out = evaluatePreToolUse({ tool_name: "Bash", tool_input: { command: "ov search viking://user/default" } });
  assert.equal(out.hookSpecificOutput?.permissionDecision, undefined);
});
