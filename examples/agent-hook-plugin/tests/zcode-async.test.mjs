import assert from "node:assert/strict";
import { createServer } from "node:http";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { expectExit, runHookScript } from "../../memory-plugin-shared/testing/support.mjs";

const hook = fileURLToPath(new URL("../scripts/hook.mjs", import.meta.url));

// Mirrors OPENVIKING_TIMEOUT_MS below: the parent must never stall this long.
const HOOK_TIMEOUT_MS = 5000;
// Slow CI runners need a generous budget for the detached worker to finish
// (cold start + two delayed responses + state writes).
const WORKER_WAIT_MS = 20000;

function waitFor(predicate, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const poll = () => {
      if (predicate()) {
        resolve();
        return;
      }
      if (Date.now() >= deadline) {
        reject(new Error("timed out waiting for detached ZCode worker"));
        return;
      }
      setTimeout(poll, 25);
    };
    poll();
  });
}

function runHook(input, env) {
  return runHookScript(hook, { argv: ["stop", "zcode"], input, env });
}

test("Stop returns before slow writes while detached worker finishes capture", async (t) => {
  const requests = [];
  let completedResponses = 0;
  const server = createServer((request, response) => {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => {
      requests.push({
        url: request.url,
        body: Buffer.concat(chunks).toString(),
      });
      setTimeout(() => {
        response.writeHead(200, { "Content-Type": "application/json" });
        response.end('{"result":{}}');
        completedResponses++;
      }, 700);
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => server.close());

  const home = mkdtempSync(join(tmpdir(), "zcode-async-"));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const sessionId = "sess-async-test";
  const rolloutDir = join(home, ".zcode", "cli", "rollout");
  mkdirSync(rolloutDir, { recursive: true });
  writeFileSync(
    join(rolloutDir, `model-io-${sessionId}.jsonl`),
    `${JSON.stringify({
      turnId: "turn-001",
      request: { messages: [{ role: "user", content: "slow question" }] },
      response: { text: "slow answer" },
    })}\n`,
  );

  const startedAt = Date.now();
  const run = await runHookScript(hook, {
    argv: ["stop", "zcode"],
    input: { session_id: sessionId, cwd: home },
    env: {
      HOME: home,
      OPENVIKING_URL: `http://127.0.0.1:${server.address().port}`,
      OPENVIKING_WRITE_PATH_ASYNC: "1",
      OPENVIKING_TIMEOUT_MS: String(HOOK_TIMEOUT_MS),
    },
  });
  const elapsedMs = Date.now() - startedAt;

  expectExit(run);
  assert.equal(run.stdout, "");
  // Core async-path guarantee, independent of wall-clock speed: every server
  // response is delayed 700ms, so a parent that exits with zero completed
  // responses provably never awaited the network. A synchronous fallback
  // would have completed both responses before exiting.
  assert.equal(completedResponses, 0, "parent waited for a network response");
  // Hang guard only (not a latency budget): the parent must exit well before
  // the fetch timeout. Node cold start on a loaded CI runner can easily
  // exceed any tighter wall-clock bound, which made this flaky.
  assert.ok(elapsedMs < HOOK_TIMEOUT_MS, `parent hook took ${elapsedMs}ms`);

  await waitFor(() => requests.some(({ url }) => url?.endsWith("/commit")), WORKER_WAIT_MS);
  assert.equal(requests.length, 2);
  const batch = requests.find(({ url }) => url?.endsWith("/messages/batch"));
  assert.ok(batch);
  assert.deepEqual(JSON.parse(batch.body), {
    messages: [
      { role: "user", content: "slow question", turn_id: "turn-001" },
      { role: "assistant", content: "slow answer", turn_id: "turn-001" },
    ],
  });

  const statePath = join(home, ".openviking", "hook-state", "zcode", `${sessionId}.json`);
  await waitFor(() => existsSync(statePath), WORKER_WAIT_MS);
  const state = JSON.parse(readFileSync(statePath, "utf8"));
  assert.equal(state.lastTurnId, "turn-001");
  assert.deepEqual(state.capturedTurnIds, [
    "turn-001:user",
    "turn-001:assistant",
  ]);
  assert.equal(state.captured, undefined);
});

test("400 failure does not advance state and successful retry prevents duplicate Stop", async (t) => {
  let batchAttempts = 0;
  const requests = [];
  const server = createServer((request, response) => {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => {
      requests.push({
        url: request.url,
        body: Buffer.concat(chunks).toString(),
      });
      const isBatch = request.url?.endsWith("/messages/batch");
      if (isBatch) batchAttempts++;
      const status = isBatch && batchAttempts === 1 ? 400 : 200;
      response.writeHead(status, { "Content-Type": "application/json" });
      response.end(status === 400
        ? '{"status":"error","error":{"message":"invalid request"}}'
        : '{"result":{}}');
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => server.close());

  const home = mkdtempSync(join(tmpdir(), "zcode-retry-"));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const sessionId = "sess-retry-test";
  const rolloutDir = join(home, ".zcode", "cli", "rollout");
  const stateDir = join(home, ".openviking", "hook-state", "zcode");
  mkdirSync(rolloutDir, { recursive: true });
  mkdirSync(stateDir, { recursive: true });
  writeFileSync(
    join(rolloutDir, `model-io-${sessionId}.jsonl`),
    `${JSON.stringify({
      turnId: "turn-001",
      request: { messages: [{ role: "user", content: "retry question" }] },
      response: { text: "retry answer" },
    })}\n`,
  );
  const statePath = join(stateDir, `${sessionId}.json`);
  const pendingPrompt = { prompt: "retry question", hash: "prompt-hash", at: 123 };
  writeFileSync(statePath, `${JSON.stringify({ version: 1, pendingPrompt })}\n`);
  const env = {
    HOME: home,
    OPENVIKING_URL: `http://127.0.0.1:${server.address().port}`,
    OPENVIKING_WRITE_PATH_ASYNC: "0",
    OPENVIKING_TIMEOUT_MS: String(HOOK_TIMEOUT_MS),
  };

  expectExit(await runHook({ session_id: sessionId, cwd: home }, env));
  const failedState = JSON.parse(readFileSync(statePath, "utf8"));
  assert.equal(failedState.lastTurnId ?? null, null);
  assert.deepEqual(failedState.capturedTurnIds, []);
  assert.deepEqual(failedState.pendingPrompt, pendingPrompt);
  assert.equal(requests.length, 1);

  expectExit(await runHook({ session_id: sessionId, cwd: home }, env));
  const retriedBatch = requests.filter(({ url }) => url?.endsWith("/messages/batch"))[1];
  assert.deepEqual(JSON.parse(retriedBatch.body), {
    messages: [
      { role: "user", content: "retry question", turn_id: "turn-001" },
      { role: "assistant", content: "retry answer", turn_id: "turn-001" },
    ],
  });
  const successfulState = JSON.parse(readFileSync(statePath, "utf8"));
  assert.equal(successfulState.lastTurnId, "turn-001");
  assert.deepEqual(successfulState.capturedTurnIds, [
    "turn-001:user",
    "turn-001:assistant",
  ]);
  assert.equal(successfulState.pendingPrompt, null);
  assert.equal(successfulState.captured, undefined);

  const requestCountAfterSuccess = requests.length;
  expectExit(await runHook({ session_id: sessionId, cwd: home }, env));
  assert.equal(requests.length, requestCountAfterSuccess);
});
