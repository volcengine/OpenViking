import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const hook = fileURLToPath(new URL("./kimicode-hook.mjs", import.meta.url));
const HOOK_TIMEOUT_MS = 5000;
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
        reject(new Error("timed out waiting for detached Kimicode worker"));
        return;
      }
      setTimeout(poll, 25);
    };
    poll();
  });
}

test("Stop argv is copied into env so the detached worker still captures", async (t) => {
  const requests = [];
  let completedResponses = 0;
  const server = createServer((request, response) => {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => {
      requests.push({ url: request.url, body: Buffer.concat(chunks).toString() });
      setTimeout(() => {
        response.writeHead(200, { "Content-Type": "application/json" });
        response.end('{"result":{}}');
        completedResponses++;
      }, 700);
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => server.close());

  const home = mkdtempSync(join(tmpdir(), "kc-async-"));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const sessionId = "session_async";
  const sessionDir = join(home, "sessions", "wd_x", sessionId);
  mkdirSync(join(sessionDir, "agents", "main"), { recursive: true });
  writeFileSync(
    join(home, "session_index.jsonl"),
    JSON.stringify({ sessionId, sessionDir, workDir: home }) + "\n",
  );
  writeFileSync(
    join(sessionDir, "agents", "main", "wire.jsonl"),
    [
      JSON.stringify({
        type: "turn.prompt",
        input: [{ type: "text", text: "slow question" }],
      }),
      JSON.stringify({
        type: "context.append_message",
        message: { role: "user", content: [{ type: "text", text: "slow question" }] },
      }),
      JSON.stringify({
        type: "context.append_loop_event",
        event: {
          type: "content.part",
          turnId: "turn-001",
          part: { type: "text", text: "slow answer" },
        },
      }),
    ].join("\n") + "\n",
  );

  const startedAt = Date.now();
  const child = spawn(process.execPath, [hook, "stop"], {
    env: {
      ...process.env,
      HOME: home,
      KIMI_CODE_HOME: home,
      OPENVIKING_URL: `http://127.0.0.1:${server.address().port}`,
      OPENVIKING_WRITE_PATH_ASYNC: "1",
      OPENVIKING_TIMEOUT_MS: String(HOOK_TIMEOUT_MS),
    },
    stdio: ["pipe", "pipe", "pipe"],
  });
  child.stdin.end(JSON.stringify({ session_id: sessionId, cwd: home }));
  let stderr = "";
  child.stderr.on("data", (chunk) => {
    stderr += chunk;
  });
  const exitCode = await new Promise((resolve) => child.on("close", resolve));
  const elapsedMs = Date.now() - startedAt;

  assert.equal(exitCode, 0, stderr);
  assert.equal(completedResponses, 0, "parent waited for a network response");
  assert.ok(elapsedMs < HOOK_TIMEOUT_MS, `parent hook took ${elapsedMs}ms`);

  await waitFor(() => requests.some(({ url }) => url?.endsWith("/commit")), WORKER_WAIT_MS);
  const batch = requests.find(({ url }) => url?.endsWith("/messages/batch"));
  assert.ok(batch, "detached worker never posted messages");
  const body = JSON.parse(batch.body);
  assert.deepEqual(body.messages, [
    { role: "user", content: "slow question", turn_id: "turn-001" },
    { role: "assistant", content: "slow answer", turn_id: "turn-001" },
  ]);
});
