import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { evaluatePreToolUse } from "./uri-guard.mjs";

const scriptPath = join(dirname(fileURLToPath(import.meta.url)), "uri-guard.mjs");

function codexEvent(toolName, toolInput) {
  return {
    session_id: "019a0000-0000-7000-8000-000000000000",
    turn_id: "turn-1",
    tool_use_id: "call-1",
    hook_event_name: "PreToolUse",
    cwd: "/repo",
    permission_mode: "default",
    tool_name: toolName,
    tool_input: toolInput,
  };
}

test("Codex URI guard lets Bash carrying a viking URI run with a notice", () => {
  const out = evaluatePreToolUse(codexEvent("Bash", { command: "ov read viking://resources/a.md" }));

  assert.equal(out.hookSpecificOutput?.hookEventName, "PreToolUse");
  assert.equal(out.hookSpecificOutput?.permissionDecision, undefined);
  assert.equal(out.hookSpecificOutput?.permissionDecisionReason, undefined);
  assert.match(out.hookSpecificOutput?.additionalContext ?? "", /viking:\/\/resources\/a\.md/);
  assert.match(out.hookSpecificOutput?.additionalContext ?? "", /ignore this notice/);
});

test("Codex URI guard notices a command given as an argv array", () => {
  const out = evaluatePreToolUse(codexEvent("Bash", { command: ["bash", "-lc", "cat viking://user/memories/a.md"] }));

  assert.equal(out.hookSpecificOutput?.permissionDecision, undefined);
  assert.match(out.hookSpecificOutput?.additionalContext ?? "", /viking:\/\/user\/memories\/a\.md/);
});

test("Codex URI guard stays silent for plain commands and apply_patch", () => {
  assert.deepEqual(evaluatePreToolUse(codexEvent("Bash", { command: "ls -la /tmp" })), {});
  assert.deepEqual(
    evaluatePreToolUse(codexEvent("apply_patch", {
      command: "*** Begin Patch\n*** Update File: README.md\n@@\n-local/path\n+viking://resources/docs\n*** End Patch\n",
    })),
    {},
  );
});

test("Codex URI guard hook prints the notice envelope and nothing otherwise", () => {
  const run = (event) => execFileSync("node", [scriptPath], { input: JSON.stringify(event), encoding: "utf-8" });

  const notice = JSON.parse(run(codexEvent("Bash", { command: "curl -d '{\"uri\":\"viking://resources/a\"}' http://127.0.0.1:1933" })));
  assert.deepEqual(Object.keys(notice), ["hookSpecificOutput"]);
  assert.deepEqual(Object.keys(notice.hookSpecificOutput).sort(), ["additionalContext", "hookEventName"]);
  assert.equal(run(codexEvent("Bash", { command: "echo hello" })), "");
});
