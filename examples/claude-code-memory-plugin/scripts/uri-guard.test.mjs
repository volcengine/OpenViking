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

test("Claude URI guard denies Write to a viking URI path", () => {
  const out = evaluatePreToolUse({
    tool_name: "Write",
    tool_input: { file_path: "viking://resources/project/new.md", content: "hello" },
  });

  assert.equal(out.hookSpecificOutput?.permissionDecision, "deny");
  assert.match(out.hookSpecificOutput?.permissionDecisionReason ?? "", /OpenViking MCP write/);
});

test("Claude URI guard lets Bash carrying a viking URI run with a notice", () => {
  const out = evaluatePreToolUse({
    tool_name: "Bash",
    tool_input: { command: "ov read viking://resources/a.md" },
  });

  assert.equal(out.hookSpecificOutput?.hookEventName, "PreToolUse");
  assert.equal(out.hookSpecificOutput?.permissionDecision, undefined);
  assert.equal(out.hookSpecificOutput?.permissionDecisionReason, undefined);
  assert.match(out.hookSpecificOutput?.additionalContext ?? "", /viking:\/\/resources\/a\.md/);
  assert.match(out.hookSpecificOutput?.additionalContext ?? "", /ignore this notice/);
});

test("Claude URI guard allows local paths, URI text in content and unrelated tools", () => {
  assert.deepEqual(evaluatePreToolUse({ tool_name: "Read", tool_input: { file_path: "/tmp/a.md" } }), {});
  assert.deepEqual(evaluatePreToolUse({ tool_name: "Bash", tool_input: { command: "ls -la /tmp" } }), {});
  assert.deepEqual(
    evaluatePreToolUse({
      tool_name: "Edit",
      tool_input: {
        file_path: "/repo/README.md",
        old_string: "local/path",
        new_string: "viking://resources/a.md",
      },
    }),
    {},
  );
  assert.deepEqual(
    evaluatePreToolUse({ tool_name: "Grep", tool_input: { pattern: "viking://", path: "/repo" } }),
    {},
  );
  assert.deepEqual(
    evaluatePreToolUse({ tool_name: "WebFetch", tool_input: { url: "viking://resources/a.md" } }),
    {},
  );
});
