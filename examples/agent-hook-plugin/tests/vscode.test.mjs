import assert from "node:assert/strict";
import { createServer } from "node:http";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { expectExit, runHookScript } from "../../memory-plugin-shared/testing/support.mjs";
import { HOSTS } from "../hosts/index.mjs";
import { vscode } from "../hosts/vscode.mjs";
import { buildVscodeTurns, cleanVscodeText } from "../hosts/vscode-turns.mjs";
import { evaluateHostUriGuard } from "../scripts/uri-guard.mjs";

const pluginRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const hookEntry = resolve(pluginRoot, "scripts", "hook.mjs");

test("VS Code integration contains native Local hook declarations", () => {
  for (const file of [
    "hosts/vscode/hooks.json",
    "hosts/vscode/openviking.integration.json",
    "hosts/vscode.mjs",
    "hosts/vscode-turns.mjs",
    "scripts/uri-guard.mjs",
  ]) {
    assert.ok(existsSync(join(pluginRoot, file)), `${file} must exist`);
  }
  assert.ok(HOSTS.vscode, "the registry must expose the vscode host");
  const plugin = JSON.parse(readFileSync(join(pluginRoot, "plugin.json"), "utf8"));
  const integration = JSON.parse(readFileSync(join(pluginRoot, "hosts", "vscode", "openviking.integration.json"), "utf8"));
  const hooks = JSON.parse(readFileSync(join(pluginRoot, "hosts", "vscode", "hooks.json"), "utf8"));
  assert.equal(plugin.version, integration.version);
  assert.deepEqual(integration.clients, ["vscode"]);
  // VS Code's native file maps PascalCase events to flat command entries —
  // no Claude-style matcher/hooks nesting, no sessionEnd (VS Code declares
  // none), and every entry is a `type: "command"` object.
  assert.deepEqual(Object.keys(hooks.hooks), [
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "Stop",
    "PreCompact",
  ]);
  for (const entries of Object.values(hooks.hooks)) {
    for (const entry of entries) {
      assert.equal(entry.type, "command");
      assert.match(entry.command, /scripts\/(hook|uri-guard)\.mjs (SessionStart|UserPromptSubmit|PreToolUse|Stop|PreCompact)? ?__OPENVIKING_CLIENT_ID__$/u);
    }
  }
});

test("VS Code envelope answers only where the Local hooks reference allows", () => {
  // SessionStart is the one event documented to carry additionalContext.
  assert.deepEqual(vscode.envelope("SessionStart", "context block"), {
    hookSpecificOutput: { hookEventName: "SessionStart", additionalContext: "context block" },
  });
  // UserPromptSubmit and the capture events accept the common output only:
  // nothing may be written even when a block was produced.
  assert.equal(vscode.envelope("UserPromptSubmit", "context block"), null);
  assert.equal(vscode.envelope("Stop", "context block"), null);
  assert.equal(vscode.envelope("SessionStart", ""), null);
});

test("VS Code URI guard follows the Claude Code PreToolUse response contract", () => {
  const denied = evaluateHostUriGuard("vscode", {
    tool_name: "read_file",
    tool_input: { file_path: "viking://resources/project/file.md" },
  });
  assert.equal(denied.hookSpecificOutput?.hookEventName, "PreToolUse");
  assert.equal(denied.hookSpecificOutput?.permissionDecision, "deny");
  assert.match(
    denied.hookSpecificOutput?.permissionDecisionReason ?? "",
    /OpenViking MCP read/,
  );
  assert.deepEqual(evaluateHostUriGuard("vscode", {
    tool_name: "read_file",
    tool_input: { file_path: "/tmp/file.md" },
  }), {});
});

test("VS Code capture holds the user turn only", () => {
  // Stop documents no assistant response and the transcript format is
  // declared unstable, so the builder never invents an assistant side.
  assert.deepEqual(buildVscodeTurns({ prompt: "question" }), [
    { role: "user", content: "question" },
  ]);
  assert.deepEqual(buildVscodeTurns({}, { pendingPrompt: { prompt: "tracked prompt" } }), [
    { role: "user", content: "tracked prompt" },
  ]);
  assert.deepEqual(buildVscodeTurns({}, {}), []);
});

test("VS Code capture removes previously injected memory blocks", () => {
  assert.equal(cleanVscodeText("before<openviking-context>secret</openviking-context>after"), "beforeafter");
});

test("VS Code prompt hook writes no output and Stop captures the tracked prompt", async () => {
  const messages = [];
  const recallHits = [];
  const commits = [];
  const server = createServer((request, response) => {
    let body = "";
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => {
      if (request.url === "/api/v1/search/search" || request.url === "/api/v1/search/recall") {
        recallHits.push(request.url);
        response.end(JSON.stringify({
          result: { rendered: "vscode memory", entries: [], stats: {} },
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
  const root = mkdtempSync(join(tmpdir(), "openviking-vscode-hook-"));
  const env = {
    HOME: root,
    OPENVIKING_URL: `http://127.0.0.1:${server.address().port}`,
    OPENVIKING_HOOK_STATE_DIR: join(root, "state"),
    OPENVIKING_MEMORY_ENABLED: "1",
  };
  try {
    async function runHook(event, input) {
      const run = expectExit(await runHookScript(hookEntry, { argv: [event, "vscode"], input, env }));
      return JSON.parse(run.stdout.trim() || "{}");
    }

    const base = { session_id: "vs-session", cwd: "/workspace" };
    // UserPromptSubmit has no injection channel, so the hook must answer the
    // common output only — and not spend the submission's budget on a recall
    // fetch whose answer could not be shown anyway.
    const prompts = await Promise.all([
      runHook("UserPromptSubmit", { ...base, prompt: "remember this" }),
      runHook("UserPromptSubmit", { ...base, prompt: "remember this" }),
    ]);
    assert.deepEqual(prompts, [{}, {}]);
    assert.deepEqual(recallHits, []);

    await Promise.all([
      runHook("Stop", base),
      runHook("Stop", base),
    ]);
    assert.equal(messages.length, 1);
    assert.equal(commits.length, 1, "a Stop may be the session's last event, so the capture commits at once");
    assert.ok(messages[0].url.includes("vscode-vs-session"));
    assert.deepEqual(
      { role: messages[0].body.role, content: messages[0].body.content },
      { role: "user", content: "remember this" },
    );

    await runHook("PreCompact", base);
    assert.equal(messages.length, 1, "a Stop without a new prompt captures nothing new");

    await runHook("UserPromptSubmit", { ...base, prompt: "and this too" });
    await runHook("Stop", base);
    assert.equal(messages.length, 2, "a later turn must not be mistaken for a duplicate Hook run");
    assert.equal(commits.length, 2);
  } finally {
    server.close();
    rmSync(root, { recursive: true, force: true });
  }
});
