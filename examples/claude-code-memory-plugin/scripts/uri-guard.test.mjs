import test from "node:test";
import assert from "node:assert/strict";
import { evaluatePreToolUse } from "./uri-guard.mjs";

test("Claude URI guard denies filesystem Read on viking URI", () => {
  const out = evaluatePreToolUse({
    tool_name: "Read",
    tool_input: { file_path: "viking://resources/project/file.md" },
  });

  assert.equal(out.hookSpecificOutput?.hookEventName, "PreToolUse");
  assert.equal(out.hookSpecificOutput?.permissionDecision, "deny");
  assert.match(out.hookSpecificOutput?.permissionDecisionReason ?? "", /OpenViking MCP read/);
});

test("Claude URI guard allows local paths and unrelated tools", () => {
  assert.deepEqual(evaluatePreToolUse({ tool_name: "Read", tool_input: { file_path: "/tmp/a.md" } }), {});
  assert.deepEqual(evaluatePreToolUse({ tool_name: "Bash", tool_input: { command: "cat viking://resources/a.md" } }), {});
});
