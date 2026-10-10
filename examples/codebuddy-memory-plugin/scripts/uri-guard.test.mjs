/**
 * URI guard tests.
 *
 * The guard exists to stop a model from treating a `viking://` URI as a local
 * path. That splits into two very different cases, and the tests pin both:
 * file tools whose *path* is a viking:// URI are denied, while a viking://
 * string that is merely data (a file's content, a grep pattern, a shell
 * argument) must pass through untouched.
 *
 * ⚠️ Deny-only by design — the shared guard's shell notice rides on
 * `additionalContext` under `PreToolUse`, which CodeBuddy does not define. See
 * docs/HOST-CONTRACT.md §5.
 */

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { evaluatePreToolUse } from "./uri-guard.mjs";

const ENTRY = resolve(dirname(fileURLToPath(import.meta.url)), "uri-guard.mjs");

const event = (toolName, toolInput) => ({
  hook_event_name: "PreToolUse",
  session_id: "s",
  cwd: "/tmp",
  tool_name: toolName,
  tool_input: toolInput,
});

/** Run the guard as the host does, so the stdout path is covered too. */
function runAsProcess(payload) {
  const r = spawnSync(process.execPath, [ENTRY], { input: JSON.stringify(payload), encoding: "utf8" });
  return { out: (r.stdout || "").trim(), code: r.status };
}

test("a file tool pointed at a viking:// URI is denied with a usable reason", () => {
  const out = evaluatePreToolUse(event("Read", { file_path: "viking://user/caozhiyong/memories/x.md" }));
  assert.equal(out.hookSpecificOutput.hookEventName, "PreToolUse");
  assert.equal(out.hookSpecificOutput.permissionDecision, "deny");
  const reason = out.hookSpecificOutput.permissionDecisionReason;
  assert.match(reason, /MCP read/);
  assert.match(reason, /read\(uris=/);
});

test("a write to a skill URI is redirected to add_skill", () => {
  const out = evaluatePreToolUse(event("Write", {
    file_path: "viking://user/caozhiyong/skills/foo/SKILL.md",
    content: "x",
  }));
  assert.equal(out.hookSpecificOutput.permissionDecision, "deny");
  assert.match(out.hookSpecificOutput.permissionDecisionReason, /add_skill/);
});

test("a plain local path is left alone", () => {
  assert.deepEqual(evaluatePreToolUse(event("Read", { file_path: "/tmp/x.md" })), {});
});

test("a local write whose body merely mentions viking:// is left alone", () => {
  // `content` is text, not a location: this is the false positive the shared
  // guard's content-key set exists to avoid.
  assert.deepEqual(
    evaluatePreToolUse(event("Write", { file_path: "/tmp/x.md", content: "见 viking://user/default/foo" })),
    {},
  );
});

test("a grep for the literal text viking:// is left alone", () => {
  assert.deepEqual(evaluatePreToolUse(event("Grep", { pattern: "viking://", path: "/tmp" })), {});
});

test("a shell command carrying viking:// is left alone", () => {
  // No notice channel on this host, so the guard makes no opinion about Bash.
  assert.deepEqual(evaluatePreToolUse(event("Bash", { command: "cat viking://user/x" })), {});
});

test("an empty or malformed payload produces no opinion and exits cleanly", () => {
  assert.deepEqual(evaluatePreToolUse({}), {});
  const r = runAsProcess({});
  assert.equal(r.code, 0);
  assert.equal(r.out, "");
});

test("an allow-shaped path prints the deny envelope, not an empty one", () => {
  const r = runAsProcess(event("Read", { file_path: "viking://user/x" }));
  assert.equal(r.code, 0);
  const parsed = JSON.parse(r.out);
  assert.equal(parsed.hookSpecificOutput.permissionDecision, "deny");
});
