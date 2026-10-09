import assert from "node:assert/strict";
import { createServer } from "node:http";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { expectExit, runHookScript } from "../../memory-plugin-shared/testing/support.mjs";

const pluginRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const hookEntry = join(pluginRoot, "scripts", "hook.mjs");

async function runHook(event, input, env) {
  return expectExit(await runHookScript(hookEntry, {
    argv: [event, "grok"],
    input,
    env,
  }));
}

function hookOutput(run) {
  return run.stdout.trim() ? JSON.parse(run.stdout) : null;
}

test("Grok integration declares native deferred-delivery hooks", () => {
  for (const file of [
    "hosts/grok/hooks.json",
    "hosts/grok/.mcp.json",
    "hosts/grok/openviking.integration.json",
    "hosts/grok.mjs",
  ]) {
    assert.ok(existsSync(join(pluginRoot, file)), `${file} must exist`);
  }
  const integration = JSON.parse(readFileSync(
    join(pluginRoot, "hosts", "grok", "openviking.integration.json"),
    "utf8",
  ));
  const hooks = JSON.parse(readFileSync(join(pluginRoot, "hosts", "grok", "hooks.json"), "utf8"));
  assert.deepEqual(integration.clients, ["grok"]);
  assert.deepEqual(Object.keys(hooks.hooks), [
    "SessionStart",
    "UserPromptSubmit",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
  ]);
});

test("Grok caches prompt recall and delivers it once through either first tool-result event", async () => {
  let recallRequests = 0;
  const server = createServer((request, response) => {
    if (request.url === "/api/v1/search/search" || request.url === "/api/v1/search/recall") {
      recallRequests += 1;
      response.end(JSON.stringify({
        result: { rendered: `grok memory ${recallRequests}`, entries: [], stats: {} },
      }));
      return;
    }
    response.end(JSON.stringify({ result: { ok: true } }));
  });
  await new Promise((resolveListen) => server.listen(0, "127.0.0.1", resolveListen));
  const root = mkdtempSync(join(tmpdir(), "openviking-grok-recall-"));
  const env = {
    HOME: root,
    OPENVIKING_URL: `http://127.0.0.1:${server.address().port}`,
    OPENVIKING_HOOK_STATE_DIR: join(root, "state"),
    OPENVIKING_MEMORY_ENABLED: "1",
    OPENVIKING_PROFILE_ENABLED: "0",
  };
  try {
    for (const [index, carrier] of ["post-tool-use", "post-tool-use-failure"].entries()) {
      const base = { sessionId: `grok-session-${index}`, cwd: "/workspace" };
      const prompt = await runHook("user-prompt-submit", {
        ...base,
        promptId: `prompt-${index}`,
        prompt: `remember turn ${index}`,
      }, env);
      assert.equal(prompt.stdout, "", "Grok discards allowed UserPromptSubmit stdout");

      const delivered = hookOutput(await runHook(carrier, {
        ...base,
        promptId: `prompt-${index}`,
        toolName: "read_file",
        toolInput: { path: "README.md" },
      }, env));
      const expectedEvent = carrier === "post-tool-use" ? "PostToolUse" : "PostToolUseFailure";
      assert.equal(delivered?.hookSpecificOutput?.hookEventName, expectedEvent);
      assert.match(delivered?.hookSpecificOutput?.additionalContext ?? "", /grok memory/);

      const later = await runHook(carrier === "post-tool-use" ? "post-tool-use-failure" : "post-tool-use", {
        ...base,
        promptId: `prompt-${index}`,
        toolName: "grep",
        toolInput: { pattern: "memory" },
      }, env);
      assert.equal(later.stdout, "", "the cached recall must be consumed at most once");
    }
    assert.equal(recallRequests, 2, "each distinct prompt recalls once");
  } finally {
    await new Promise((resolveClose) => server.close(resolveClose));
    rmSync(root, { recursive: true, force: true });
  }
});

test("Grok retries first-prompt profile context until a tool-result event delivers it", async () => {
  let profileReads = 0;
  const server = createServer((request, response) => {
    const url = new URL(request.url, "http://localhost");
    let result = {};
    if (url.pathname.endsWith("/system/status")) result = { user: "default" };
    else if (url.pathname.endsWith("/content/read")) {
      profileReads += 1;
      result = "grok profile line";
    } else if (url.pathname.endsWith("/fs/ls")) result = [];
    response.end(JSON.stringify({ result }));
  });
  await new Promise((resolveListen) => server.listen(0, "127.0.0.1", resolveListen));
  const root = mkdtempSync(join(tmpdir(), "openviking-grok-profile-"));
  const env = {
    HOME: root,
    OPENVIKING_URL: `http://127.0.0.1:${server.address().port}`,
    OPENVIKING_HOOK_STATE_DIR: join(root, "state"),
    OPENVIKING_AUTO_RECALL: "0",
    OPENVIKING_AUTO_CAPTURE: "0",
    OPENVIKING_SKILL_CATALOG: "0",
  };
  const base = { sessionId: "grok-profile", cwd: "/workspace" };
  try {
    const first = await runHook("user-prompt-submit", {
      ...base, promptId: "profile-1", prompt: "first no-tool turn",
    }, env);
    assert.equal(first.stdout, "");

    const second = await runHook("user-prompt-submit", {
      ...base, promptId: "profile-2", prompt: "second turn uses a tool",
    }, env);
    assert.equal(second.stdout, "");
    const delivered = hookOutput(await runHook("post-tool-use", {
      ...base, promptId: "profile-2", toolName: "read_file", toolInput: {},
    }, env));
    assert.match(delivered?.hookSpecificOutput?.additionalContext ?? "", /grok profile line/u);

    await runHook("user-prompt-submit", {
      ...base, promptId: "profile-3", prompt: "profile is already delivered",
    }, env);
    const later = await runHook("post-tool-use", {
      ...base, promptId: "profile-3", toolName: "read_file", toolInput: {},
    }, env);
    assert.equal(later.stdout, "");
    assert.equal(profileReads, 2, "profile must retry before delivery and stop after delivery");
  } finally {
    await new Promise((resolveClose) => server.close(resolveClose));
    rmSync(root, { recursive: true, force: true });
  }
});

test("Grok Stop captures the cached prompt and native final response, then commits", async () => {
  const messages = [];
  const commits = [];
  const server = createServer((request, response) => {
    let body = "";
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => {
      if (request.url === "/api/v1/search/search" || request.url === "/api/v1/search/recall") {
        response.end(JSON.stringify({
          result: { rendered: "memory for capture", entries: [], stats: {} },
        }));
      } else if (request.url?.includes("/messages")) {
        const parsed = JSON.parse(body);
        messages.push(...(parsed.messages ?? [parsed]));
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
  const root = mkdtempSync(join(tmpdir(), "openviking-grok-capture-"));
  const env = {
    HOME: root,
    OPENVIKING_URL: `http://127.0.0.1:${server.address().port}`,
    OPENVIKING_HOOK_STATE_DIR: join(root, "state"),
    OPENVIKING_MEMORY_ENABLED: "1",
    OPENVIKING_PROFILE_ENABLED: "0",
  };
  try {
    const base = { sessionId: "grok-capture", cwd: "/workspace", promptId: "turn-1" };
    await runHook("user-prompt-submit", { ...base, prompt: "what is the retry budget?" }, env);
    await runHook("post-tool-use", { ...base, toolName: "read_file", toolInput: {} }, env);
    const stop = await runHook("stop", {
      ...base,
      lastAssistantMessage: "The retry budget is three attempts.",
    }, env);

    assert.equal(stop.stdout, "");
    assert.deepEqual(messages, [
      { role: "user", content: "what is the retry budget?" },
      { role: "assistant", content: "The retry budget is three attempts." },
    ]);
    assert.equal(commits.length, 1);
    assert.match(commits[0], /gr-grok-capture\/commit$/);
  } finally {
    await new Promise((resolveClose) => server.close(resolveClose));
    rmSync(root, { recursive: true, force: true });
  }
});
