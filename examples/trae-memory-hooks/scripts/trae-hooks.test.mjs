import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { buildTraeTurns, cleanTraeText } from "./trae-turns.mjs";
import { evaluateTraeUriGuard } from "./uri-guard.mjs";

const pluginRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

test("TRAE integration contains native Hook and MCP declarations", () => {
  for (const file of [
    "hooks/hooks.json",
    ".mcp.json",
    "openviking.integration.json",
    "scripts/trae-hook.mjs",
    "scripts/session-start.mjs",
    "scripts/auto-recall.mjs",
    "scripts/auto-capture.mjs",
    "scripts/uri-guard.mjs",
  ]) {
    assert.ok(existsSync(join(pluginRoot, file)), `${file} must exist`);
  }
  const integration = JSON.parse(readFileSync(join(pluginRoot, "openviking.integration.json"), "utf8"));
  const hooks = JSON.parse(readFileSync(join(pluginRoot, "hooks", "hooks.json"), "utf8"));
  assert.deepEqual(integration.clients, ["trae", "trae-cn"]);
  assert.deepEqual(Object.keys(hooks.hooks), [
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "Stop",
  ]);
});

test("TRAE URI guard follows the Claude Code PreToolUse response contract", () => {
  const denied = evaluateTraeUriGuard({
    tool_name: "Read",
    tool_input: { file_path: "viking://resources/project/file.md" },
  });
  assert.equal(denied.hookSpecificOutput?.hookEventName, "PreToolUse");
  assert.equal(denied.hookSpecificOutput?.permissionDecision, "deny");
  assert.match(
    denied.hookSpecificOutput?.permissionDecisionReason ?? "",
    /OpenViking MCP read/,
  );
  assert.deepEqual(evaluateTraeUriGuard({
    tool_name: "Read",
    tool_input: { file_path: "/tmp/file.md" },
  }), {});
});

function runHook(event, client, input, env) {
  const entrypoint = {
    "session-start": "session-start.mjs",
    "user-prompt-submit": "auto-recall.mjs",
    stop: "auto-capture.mjs",
  }[event];
  return new Promise((resolveRun, reject) => {
    const child = spawn(process.execPath, [join(pluginRoot, "scripts", entrypoint), client], {
      env: { ...process.env, ...env },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) reject(new Error(stderr || `hook exited ${code}`));
      else resolveRun(JSON.parse(stdout.trim() || "{}"));
    });
    child.stdin.end(JSON.stringify(input));
  });
}

test("TRAE capture uses event fields rather than a transcript path", () => {
  const turns = buildTraeTurns({ prompt: "question", last_assistant_message: "answer" });
  assert.deepEqual(turns, [
    { role: "user", content: "question" },
    { role: "assistant", content: "answer" },
  ]);
});

test("TRAE capture removes previously injected memory blocks", () => {
  assert.equal(cleanTraeText("before<openviking-context>secret</openviking-context>after"), "beforeafter");
});

test("TRAE prompt hook injects recall and Stop captures dedicated event fields", async () => {
  const messages = [];
  const commits = [];
  const server = createServer((request, response) => {
    let body = "";
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => {
      if (request.url === "/api/v1/search/recall") {
        response.end(JSON.stringify({ result: { rendered: "trae memory" } }));
      } else if (request.url?.includes("/messages")) {
        const parsed = JSON.parse(body);
        messages.push(...(parsed.messages ?? [parsed]).map((message) => ({ url: request.url, body: message })));
        response.end(JSON.stringify({ result: { ok: true } }));
      } else if (request.url?.endsWith("/commit")) {
        commits.push(request.url);
        response.end(JSON.stringify({ result: { ok: true } }));
      } else {
        response.end(JSON.stringify({ result: { ok: true } }));
      }
    });
  });
  await new Promise((resolveListen) => server.listen(0, "127.0.0.1", resolveListen));
  const root = mkdtempSync(join(tmpdir(), "openviking-trae-hook-"));
  const env = {
    HOME: root,
    OPENVIKING_URL: `http://127.0.0.1:${server.address().port}`,
    OPENVIKING_HOOK_STATE_DIR: join(root, "state"),
    OPENVIKING_MEMORY_ENABLED: "1",
  };
  try {
    const base = { session_id: "same-session", cwd: "/workspace" };
    const promptInput = { ...base, prompt: "remember this", generation_id: "prompt-1" };
    const recalled = await Promise.all([
      runHook("user-prompt-submit", "trae-cn", promptInput, env),
      runHook("user-prompt-submit", "trae-cn", promptInput, env),
    ]);
    assert.equal(recalled.filter((item) => /trae memory/.test(item.hookSpecificOutput?.additionalContext || "")).length, 1);
    await Promise.all([
      runHook("stop", "trae-cn", { ...base, last_assistant_message: "done" }, env),
      runHook("stop", "trae-cn", { ...base, last_assistant_message: "done" }, env),
    ]);
    assert.equal(messages.length, 2);
    assert.equal(commits.length, 1, "the completed TRAE turn must be committed immediately");
    assert.ok(messages.every((item) => item.url.includes("trcn-same-session")));

    await runHook("user-prompt-submit", "trae-cn", { ...base, prompt: "remember this", generation_id: "prompt-2" }, env);
    await runHook("stop", "trae-cn", { ...base, last_assistant_message: "done" }, env);
    assert.equal(messages.length, 4, "a later identical turn must not be mistaken for a duplicate Hook run");
    assert.equal(commits.length, 2);
  } finally {
    server.close();
    rmSync(root, { recursive: true, force: true });
  }
});
