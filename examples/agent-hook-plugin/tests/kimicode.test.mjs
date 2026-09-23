import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const HOOK = join(ROOT, "scripts", "hook.mjs");
const URI_GUARD = join(ROOT, "scripts", "uri-guard.mjs");

function runScript(script, args, input, env = {}) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [script, ...args], {
      env: { ...process.env, ...env },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("close", (status) => resolve({ status, stdout, stderr }));
    child.stdin.end(JSON.stringify(input));
  });
}

const runHook = (args, input, env) => runScript(HOOK, args, input, env);

test("URI guard emits Kimi's permission decision at the process boundary", async () => {
  const result = await runScript(URI_GUARD, ["kimicode"], {
    tool_name: "Read",
    tool_input: { file_path: "viking://~/memories/profile.md" },
  });
  assert.equal(result.status, 0, result.stderr);
  const output = JSON.parse(result.stdout);
  assert.deepEqual(Object.keys(output.hookSpecificOutput).sort(), [
    "permissionDecision",
    "permissionDecisionReason",
  ]);
  assert.equal(output.hookSpecificOutput.permissionDecision, "deny");
});

test("profile retries until success, emits raw text, then injects only once", async (t) => {
  let profileReads = 0;
  const server = createServer((request, response) => {
    const url = new URL(request.url, "http://localhost");
    let result = {};
    if (url.pathname.endsWith("/system/status")) result = { user: "default" };
    else if (url.pathname.endsWith("/content/read")) {
      profileReads++;
      result = profileReads === 1 ? {} : "profile line";
    }
    else if (url.pathname.endsWith("/fs/ls")) result = [];
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ result }));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => server.close());
  const home = mkdtempSync(join(tmpdir(), "ov-kimi-hook-"));
  const env = {
    HOME: home,
    KIMI_CODE_HOME: home,
    OPENVIKING_HOOK_STATE_DIR: join(home, "state"),
    OPENVIKING_URL: `http://127.0.0.1:${server.address().port}`,
    OPENVIKING_AUTO_RECALL: "0",
    OPENVIKING_SKILL_CATALOG: "0",
  };
  const first = await runHook(["user-prompt-submit", "kimicode"], {
    session_id: "session-1", request_id: "1", cwd: home, prompt: [{ type: "text", text: "hello" }],
  }, env);
  assert.equal(first.status, 0, first.stderr);
  assert.equal(first.stdout, "");

  const second = await runHook(["user-prompt-submit", "kimicode"], {
    session_id: "session-1", request_id: "2", cwd: home, prompt: [{ type: "text", text: "retry" }],
  }, env);
  assert.equal(second.status, 0, second.stderr);
  assert.match(second.stdout, /^<openviking-context source="session-start">/);
  assert.doesNotMatch(second.stdout, /^"/);
  assert.doesNotMatch(second.stdout, /\\n/);

  const third = await runHook(["user-prompt-submit", "kimicode"], {
    session_id: "session-1", request_id: "3", cwd: home, prompt: [{ type: "text", text: "later" }],
  }, env);
  assert.equal(third.status, 0, third.stderr);
  assert.equal(third.stdout, "");
  assert.equal(profileReads, 2);
});

test("Interrupt shares one two-second request budget across capture and commit", async (t) => {
  let requests = 0;
  const server = createServer((_request, response) => {
    requests++;
    setTimeout(() => {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end('{"result":{}}');
    }, 1400);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => server.close());

  const home = mkdtempSync(join(tmpdir(), "ov-kimi-interrupt-"));
  const sessionId = "session-budget";
  const sessionDir = join(home, "sessions", "wd", sessionId);
  mkdirSync(join(sessionDir, "agents", "main"), { recursive: true });
  writeFileSync(join(home, "session_index.jsonl"), `${JSON.stringify({ sessionId, sessionDir })}\n`);
  writeFileSync(join(sessionDir, "agents", "main", "wire.jsonl"), [
    JSON.stringify({ type: "turn.prompt", input: [{ type: "text", text: "question" }] }),
    JSON.stringify({ type: "context.append_loop_event", event: { type: "content.part", turnId: "9", part: { type: "text", text: "answer" } } }),
  ].join("\n") + "\n");

  const started = Date.now();
  const result = await runHook(["interrupt", "kimicode"], {
    session_id: sessionId,
    cwd: home,
  }, {
    HOME: home,
    KIMI_CODE_HOME: home,
    OPENVIKING_HOOK_STATE_DIR: join(home, "state"),
    OPENVIKING_PENDING_DIR: join(home, "pending"),
    OPENVIKING_URL: `http://127.0.0.1:${server.address().port}`,
    OPENVIKING_TIMEOUT_MS: "10000",
  });
  const elapsed = Date.now() - started;
  assert.equal(result.status, 0, result.stderr);
  assert.ok(elapsed < 2100, `Interrupt took ${elapsed}ms`);
  assert.equal(requests, 1, "commit started after the remaining budget was below one request quantum");
});
