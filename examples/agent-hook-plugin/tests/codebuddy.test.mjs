import assert from "node:assert/strict";
import { rmSync } from "node:fs";
import test from "node:test";

import {
  writeCredentialFiles,
} from "../../memory-plugin-shared/testing/support.mjs";
import { loadAgentHookConfig } from "../lib/agent-hook-runtime.mjs";
import { codebuddy } from "../hosts/codebuddy.mjs";

const event = (toolName, toolInput) => ({
  hook_event_name: "PreToolUse",
  session_id: "s",
  cwd: "/tmp",
  tool_name: toolName,
  tool_input: toolInput,
});

test("a file tool pointed at a viking:// URI is denied with a usable reason", () => {
  const out = codebuddy.guard(event("Read", { file_path: "viking://user/caozhiyong/memories/x.md" }));
  assert.equal(out.hookSpecificOutput.hookEventName, "PreToolUse");
  assert.equal(out.hookSpecificOutput.permissionDecision, "deny");
  const reason = out.hookSpecificOutput.permissionDecisionReason;
  assert.match(reason, /MCP read/);
  assert.match(reason, /read\(uri=/);
});

test("a shell command carrying viking:// is never denied", () => {
  // The shared evaluator answers shell tools with a notice, and CodeBuddy's
  // PreToolUse has no channel for one — what must hold is that the command
  // still runs, which a notice envelope does (no permissionDecision).
  const out = codebuddy.guard(event("Bash", { command: "cat viking://user/x" }));
  assert.ok(!out.hookSpecificOutput || !out.hookSpecificOutput.permissionDecision);
});

test("a host-generated continuation is not a searchable prompt", () => {
  assert.equal(codebuddy.prompt({ prompt: "继续", is_internal_continuation: true }), "");
  assert.equal(codebuddy.prompt({ prompt: "继续" }), "继续");
});

test("the prompt is read from a string or an array of content parts", () => {
  assert.equal(codebuddy.prompt({ prompt: "  trimmed  " }), "trimmed");
  assert.equal(codebuddy.prompt({ prompt: [{ text: "a" }, { text: "b" }] }), "a\nb");
  assert.equal(codebuddy.prompt({}), "");
});

test("both injection events answer the Claude Code envelope, write-path events stay silent", () => {
  const start = codebuddy.envelope("session-start", "BLOCK");
  assert.deepEqual(start, {
    decision: "approve",
    hookSpecificOutput: { hookEventName: "SessionStart", additionalContext: "BLOCK" },
  });
  const prompt = codebuddy.envelope("user-prompt-submit", "BLOCK");
  assert.deepEqual(prompt, {
    decision: "approve",
    hookSpecificOutput: { hookEventName: "UserPromptSubmit", additionalContext: "BLOCK" },
  });
  for (const event of ["stop", "pre-compact", "session-end"]) {
    assert.equal(codebuddy.envelope(event, "BLOCK"), null);
    assert.equal(codebuddy.envelope(event, ""), null);
  }
  // An empty envelope is verified safe on this host, so a stage with nothing
  // to say answers nothing at all.
  assert.equal(codebuddy.envelope("session-start", ""), null);
});

test("plugin.codebuddy.* overrides apply and plugin.workbuddy.* does not", async () => {
  // `workbuddy` is the server-side ingest adapter's name for this same
  // transcript format; borrowing it as a harness key would silently drop the
  // user's overrides. Only `codebuddy` resolves (DESIGN.md §10).
  const files = await writeCredentialFiles("ov-cb-harness-key-", {
    ovcli: { plugin: { workbuddy: { recallLimit: 3 }, codebuddy: { recallLimit: 7 } } },
    ov: {},
  });
  try {
    const cfg = loadAgentHookConfig("codebuddy", files.dir, { env: files.env });
    assert.equal(cfg.recallLimit, 7);
  } finally {
    await rmSync(files.dir, { recursive: true, force: true });
  }
});
