import assert from "node:assert/strict";
import { createServer } from "node:http";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { expectExit, runHookScript } from "../../memory-plugin-shared/testing/support.mjs";
import { buildTraeTurns, cleanTraeText } from "../hosts/trae-turns.mjs";
import { evaluateHostUriGuard } from "../scripts/uri-guard.mjs";

const pluginRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const hookEntry = join(pluginRoot, "scripts", "hook.mjs");

test("TRAE integration contains native Hook and MCP declarations", () => {
  for (const file of [
    "hosts/trae/hooks.json",
    "hosts/trae/.mcp.json",
    "hosts/trae/openviking.integration.json",
    "hosts/trae.mjs",
    "scripts/uri-guard.mjs",
  ]) {
    assert.ok(existsSync(join(pluginRoot, file)), `${file} must exist`);
  }
  const integration = JSON.parse(readFileSync(join(pluginRoot, "hosts", "trae", "openviking.integration.json"), "utf8"));
  const hooks = JSON.parse(readFileSync(join(pluginRoot, "hosts", "trae", "hooks.json"), "utf8"));
  assert.deepEqual(integration.clients, ["trae", "trae-cn"]);
  assert.deepEqual(Object.keys(hooks.hooks), [
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "Stop",
  ]);
});

test("TRAE URI guard follows the Claude Code PreToolUse response contract", () => {
  const denied = evaluateHostUriGuard("trae", {
    tool_name: "Read",
    tool_input: { file_path: "viking://resources/project/file.md" },
  });
  assert.equal(denied.hookSpecificOutput?.hookEventName, "PreToolUse");
  assert.equal(denied.hookSpecificOutput?.permissionDecision, "deny");
  assert.match(
    denied.hookSpecificOutput?.permissionDecisionReason ?? "",
    /OpenViking MCP read/,
  );
  assert.deepEqual(evaluateHostUriGuard("trae", {
    tool_name: "Read",
    tool_input: { file_path: "/tmp/file.md" },
  }), {});
});

async function runHook(event, client, input, env) {
  const run = expectExit(await runHookScript(hookEntry, { argv: [event, client], input, env }));
  return JSON.parse(run.stdout.trim() || "{}");
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
      if (request.url === "/api/v1/search/search" || request.url === "/api/v1/search/recall") {
        response.end(JSON.stringify({
          result: { rendered: "trae memory", entries: [], stats: {} },
        }));
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
      runHook("stop", "trae-cn", { ...base, last_assistant_message: "the retry budget is three attempts" }, env),
      runHook("stop", "trae-cn", { ...base, last_assistant_message: "the retry budget is three attempts" }, env),
    ]);
    assert.equal(messages.length, 2);
    assert.equal(commits.length, 1, "the completed TRAE turn must be committed immediately");
    assert.ok(messages.every((item) => item.url.includes("trcn-same-session")));

    await runHook("user-prompt-submit", "trae-cn", { ...base, prompt: "remember this", generation_id: "prompt-2" }, env);
    await runHook("stop", "trae-cn", { ...base, last_assistant_message: "the retry budget is three attempts" }, env);
    assert.equal(messages.length, 4, "a later identical turn must not be mistaken for a duplicate Hook run");
    assert.equal(commits.length, 2);
  } finally {
    server.close();
    rmSync(root, { recursive: true, force: true });
  }
});
