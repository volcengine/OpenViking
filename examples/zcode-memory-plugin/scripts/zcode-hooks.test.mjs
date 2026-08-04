import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { buildZcodeTurns, cleanZcodeText } from "./zcode-turns.mjs";
import { evaluateZcodeUriGuard } from "./uri-guard.mjs";

const pluginRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

test("ZCode integration contains native Hook and MCP declarations", () => {
  for (const file of [
    "hooks/hooks.json",
    ".mcp.json",
    "openviking.integration.json",
    "scripts/zcode-hook.mjs",
    "scripts/session-start.mjs",
    "scripts/auto-recall.mjs",
    "scripts/auto-capture.mjs",
    "scripts/uri-guard.mjs",
  ]) {
    assert.ok(existsSync(join(pluginRoot, file)), `${file} must exist`);
  }
  const integration = JSON.parse(readFileSync(join(pluginRoot, "openviking.integration.json"), "utf8"));
  const hooks = JSON.parse(readFileSync(join(pluginRoot, "hooks", "hooks.json"), "utf8"));
  assert.deepEqual(integration.clients, ["zcode"]);
  assert.deepEqual(Object.keys(hooks.hooks), [
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "Stop",
  ]);
});

test("hooks.json commands point at the ZCode scripts via the OPENVIKING_ZCODE_ROOT token", () => {
  const hooks = JSON.parse(readFileSync(join(pluginRoot, "hooks", "hooks.json"), "utf8"));
  const commands = JSON.stringify(hooks);
  assert.match(commands, /__OPENVIKING_ZCODE_ROOT__\/scripts\/session-start\.mjs/);
  assert.match(commands, /__OPENVIKING_ZCODE_ROOT__\/scripts\/auto-recall\.mjs/);
  assert.match(commands, /__OPENVIKING_ZCODE_ROOT__\/scripts\/auto-capture\.mjs/);
  assert.match(commands, /__OPENVIKING_ZCODE_ROOT__\/scripts\/uri-guard\.mjs/);
});

test("ZCode URI guard follows the Cursor/TRAE PreToolUse deny contract", () => {
  const denied = evaluateZcodeUriGuard({
    tool_name: "Read",
    tool_input: { file_path: "viking://resources/project/file.md" },
  });
  assert.equal(denied.hookSpecificOutput?.hookEventName, "PreToolUse");
  assert.equal(denied.hookSpecificOutput?.permissionDecision, "deny");
  assert.match(
    denied.hookSpecificOutput?.permissionDecisionReason ?? "",
    /OpenViking MCP read/,
  );
  assert.deepEqual(evaluateZcodeUriGuard({
    tool_name: "Read",
    tool_input: { file_path: "/tmp/file.md" },
  }), {});
});

test("ZCode capture uses event fields rather than a transcript path", () => {
  const turns = buildZcodeTurns({ prompt: "question", last_assistant_message: "answer" });
  assert.deepEqual(turns, [
    { role: "user", content: "question" },
    { role: "assistant", content: "answer" },
  ]);
});

test("ZCode capture removes previously injected memory blocks", () => {
  assert.equal(cleanZcodeText("before<openviking-context>secret</openviking-context>after"), "beforeafter");
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

test("ZCode prompt hook injects recall and Stop captures committed turns under the zc- session id", async () => {
  const messages = [];
  const commits = [];
  const server = createServer((request, response) => {
    let body = "";
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => {
      if (request.url === "/api/v1/search/recall") {
        response.end(JSON.stringify({ result: { rendered: "zcode memory" } }));
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
  const root = mkdtempSync(join(tmpdir(), "openviking-zcode-hook-"));
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
      runHook("user-prompt-submit", "zcode", promptInput, env),
      runHook("user-prompt-submit", "zcode", promptInput, env),
    ]);
    // Dedup: the second Hook invocation with the same generation_id must not
    // re-call recall and must emit an empty approve.
    assert.equal(
      recalled.filter((item) => /zcode memory/.test(item.hookSpecificOutput?.additionalContext || "")).length,
      1,
    );
    await Promise.all([
      runHook("stop", "zcode", { ...base, last_assistant_message: "done" }, env),
      runHook("stop", "zcode", { ...base, last_assistant_message: "done" }, env),
    ]);
    assert.equal(messages.length, 2);
    assert.equal(commits.length, 1, "the completed ZCode turn must be committed immediately");
    assert.ok(messages.every((item) => item.url.includes("zc-same-session")));

    await runHook("user-prompt-submit", "zcode", { ...base, prompt: "remember this", generation_id: "prompt-2" }, env);
    await runHook("stop", "zcode", { ...base, last_assistant_message: "done" }, env);
    assert.equal(messages.length, 4, "a later identical turn must not be mistaken for a duplicate Hook run");
    assert.equal(commits.length, 2);
  } finally {
    server.close();
    rmSync(root, { recursive: true, force: true });
  }
});
