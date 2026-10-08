import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { expectExit, runHookScript } from "../../memory-plugin-shared/testing/support.mjs";
import { parseCursorTranscript } from "../hosts/cursor-transcript.mjs";
import { qoder } from "../hosts/qoder.mjs";

const pluginRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const hookEntry = resolve(pluginRoot, "scripts", "hook.mjs");

async function runHook(event, input, env) {
  const run = expectExit(await runHookScript(hookEntry, { argv: [event, "qoder"], input, env }));
  return JSON.parse(run.stdout.trim() || "{}");
}

test("Qoder integration declares its three documented lifecycle hooks and MCP entrypoint", () => {
  for (const file of [
    "hosts/qoder/hooks.json",
    "hosts/qoder/.mcp.json",
    "hosts/qoder/openviking.integration.json",
    "hosts/qoder/settings-config.mjs",
    "hosts/qoder.mjs",
    "hosts/transcript-capture.mjs",
    "scripts/hook.mjs",
    "servers/mcp-proxy.mjs",
  ]) {
    assert.ok(existsSync(join(pluginRoot, file)), `${file} must exist`);
  }
  const plugin = JSON.parse(readFileSync(join(pluginRoot, "plugin.json"), "utf8"));
  const integration = JSON.parse(readFileSync(join(pluginRoot, "hosts", "qoder", "openviking.integration.json"), "utf8"));
  const hooks = JSON.parse(readFileSync(join(pluginRoot, "hosts", "qoder", "hooks.json"), "utf8"));
  assert.equal(plugin.version, integration.version);
  assert.deepEqual(Object.keys(hooks.hooks), ["SessionStart", "UserPromptSubmit", "Stop"]);
  assert.deepEqual(integration.capabilities, ["hooks", "mcp", "skills"]);
  assert.deepEqual(qoder.envelope("session-start", "context"), {
    hookSpecificOutput: { hookEventName: "SessionStart", additionalContext: "context" },
  });
  assert.deepEqual(qoder.envelope("user-prompt-submit", "context"), {
    hookSpecificOutput: { hookEventName: "UserPromptSubmit", additionalContext: "context" },
  });
});

test("Qoder transcript parser reads Claude-schema JSONL messages", () => {
  const raw = [
    JSON.stringify({ type: "workspace-directories", sessionId: "q1" }),
    JSON.stringify({ type: "user", message: { role: "user", content: "remember blue" } }),
    JSON.stringify({
      type: "assistant",
      message: { role: "assistant", content: [{ type: "text", text: "saved" }] },
    }),
    JSON.stringify({ type: "active-leaf", sessionId: "q1" }),
  ].join("\n");
  assert.deepEqual(parseCursorTranscript(raw), [
    { role: "user", content: "remember blue" },
    { role: "assistant", content: "saved" },
  ]);
});

test("Qoder injects recall and captures its transcript on Stop", async () => {
  const messages = [];
  const commits = [];
  const server = createServer((request, response) => {
    let body = "";
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => {
      if (request.url === "/api/v1/search/search" || request.url === "/api/v1/search/recall") {
        response.end(JSON.stringify({ result: { rendered: "remembered context", entries: [], stats: {} } }));
      } else if (request.url?.includes("/messages")) {
        const parsed = JSON.parse(body);
        messages.push(...(parsed.messages ?? [parsed]));
        response.end(JSON.stringify({ result: { ok: true } }));
      } else if (request.url?.endsWith("/commit")) {
        commits.push(request.url);
        response.end(JSON.stringify({ result: { ok: true } }));
      } else {
        response.statusCode = 404;
        response.end(JSON.stringify({ status: "error" }));
      }
    });
  });
  await new Promise((resolveListen) => server.listen(0, "127.0.0.1", resolveListen));
  const root = mkdtempSync(join(tmpdir(), "openviking-qoder-hook-"));
  const env = {
    HOME: root,
    OPENVIKING_URL: `http://127.0.0.1:${server.address().port}`,
    OPENVIKING_HOOK_STATE_DIR: join(root, "state"),
    OPENVIKING_MEMORY_ENABLED: "1",
  };
  try {
    const base = { session_id: "qoder-test", cwd: root };
    const recalled = await runHook("user-prompt-submit", { ...base, prompt: "what did we decide?" }, env);
    assert.equal(recalled.hookSpecificOutput.hookEventName, "UserPromptSubmit");
    assert.match(recalled.hookSpecificOutput.additionalContext, /remembered context/u);

    const transcript = join(root, "qoder-test.jsonl");
    writeFileSync(transcript, [
      JSON.stringify({ type: "user", message: { role: "user", content: "remember blue" } }),
      JSON.stringify({
        type: "assistant",
        message: { role: "assistant", content: "saved the blue preference for later" },
      }),
    ].join("\n"));
    assert.deepEqual(await runHook("stop", { ...base, transcript_path: transcript }, env), {});
    assert.deepEqual(messages, [
      { role: "user", content: "remember blue" },
      { role: "assistant", content: "saved the blue preference for later" },
    ]);
    assert.deepEqual(commits, ["/api/v1/sessions/qd-qoder-test/commit"]);
  } finally {
    server.close();
    rmSync(root, { recursive: true, force: true });
  }
});
